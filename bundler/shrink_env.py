from __future__ import annotations

import glob
import io
import logging
import re
import zlib
from compression import zstd
from logging import info
from pathlib import Path
from shutil import copy2
from subprocess import check_call
from tarfile import TarFile
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from .conda_parse import PathType, Pkg, Platform, get_pkgs
from .remove_shlib_symlinks import (
    get_shlib_re,
    get_shlib_subs,
    get_shlib_symlinks,
    remove_shlib_symlinks,
    sub_shlibs_in_something,
)

WIN = Platform.WIN

COMPRESSION_LEVEL = 6
ELF_MAGIC = b"\x7fELF"

ENTRYPOINT_SCRIPT = """\
#!/bin/sh

# Resolve $0 through any symlinks to find the real script location.
# Needed since macos doesn't have realpath built-in.
SOURCE=$0
while [ -L "$SOURCE" ]; do
  DIR=$(cd -P "$(dirname "$SOURCE")" && pwd)
  SOURCE=$(readlink "$SOURCE")
  case "$SOURCE" in
    /*) ;;                      # already absolute, leave as-is
    *) SOURCE="$DIR/$SOURCE" ;; # relative to the symlink's directory
  esac
done
BIN_DIR=$(cd -P "$(dirname "$SOURCE")" && pwd)
PREFIX=$(dirname "$BIN_DIR")

export GI_TYPELIB_PATH="$PREFIX/lib/girepository-1.0"
export XKB_CONFIG_ROOT="$PREFIX/share/xkeyboard-config-2"
export FONTCONFIG_PATH="$PREFIX/etc/fonts"
export FONTCONFIG_FILE="$PREFIX/etc/fonts/fonts.conf"
export GSETTINGS_SCHEMA_DIR="$PREFIX/share/glib-2.0/schemas"
export XDG_DATA_DIRS="$PREFIX/share:$XDG_DATA_DIRS"

exec "$PREFIX/bin/python3.14" "$PREFIX/lib/meld" "$@"
"""

WINDOWS_ENTRYPOINT_SCRIPT = r"""
@echo off
setlocal

rem set PREFIX to the directory of this file, stripping the trailing backslash
set "PREFIX=%~dp0"
set "PREFIX=%PREFIX:~0,-1%"

set "GI_TYPELIB_PATH=%PREFIX%\Library\lib\girepository-1.0"
set "FONTCONFIG_PATH=%PREFIX%\Library\etc\fonts"
set "FONTCONFIG_FILE=%PREFIX%\Library\etc\fonts\fonts.conf"
set "GSETTINGS_SCHEMA_DIR=%PREFIX%\Library\share\glib-2.0\schemas"
set "XDG_DATA_DIRS=%PREFIX%\Library\share"

set "PATH=%PREFIX%;%PREFIX%\Library\bin;%PATH%"

"%PREFIX%\python.exe" "%PREFIX%\Library\lib\meld" %*
exit /b %ERRORLEVEL%
"""

EXCLUDE_PACKAGES = {
    "bzip2",
    "ca-certificates",
    "krb5",
    "libcups",
    "libsqlite",
    "ncurses",
    "openssl",
    "tk",
    "tzdata",
}

# If the path from the root of the env matches one of EXCLUDE_PATTERNS and doesn't
# match any of INCLUDE_PATTERNS, it's not copied to the dest env

DYNMODS = "_decimal _sha3 _pickle _sqlite _ssl _curses _curses_panel".split()

EXCLUDE_PATTERNS = [
    "bin/**",
    "Scripts/**",
    "Library/bin/*.exe",
    "conda-meta/**",
    "**/include/**",
    "**/lib/icu/**",  # contains a symlink to a directory, not needed
    "lib/libpython*",
    "**/ensurepip/**",
    "**/idlelib/**",
    *(f"lib/python*/lib-dynload/{x}.*" for x in DYNMODS),
    *(f"DLLs/{x}.pyd" for x in DYNMODS),
    "DLLs/insrumented/**",
    "libexec/**",
    "sbin/**",
    "**/share/doc/**",
    "**/share/icons/Adwaita/cursors/progress*",  # heavy cursor
    "**/share/icons/Adwaita/cursors/wait*",  # heavy cursor
    "**/share/icons/Adwaita/cursors/watch*",  # heavy cursor
    "**/share/icons/Adwaits/symbolic/status/**",  # ~1MB, not used
    "**/share/locale/**",
    "**/share/man/**",
    "share/python_compiler_compat/**",
    "**/zoneinfo/**",
    "share/X11/xkb",  # a symlink to share/xkeyboard-config-2
    "**/ssl/**",
    "var/cache/**",
    "*-conda-*/**",
    "**/__pycache__/**",
    "**/Makefile",
    "**/*.cmake",
    "**/valgrind/**",
    "**/*.a",
    "**/*.pdb",  # windows debug symbols
]

INCLUDE_PATTERNS = [
    "bin/python3.14",
]


def get_glob_re(patterns: list[str]) -> re.Pattern[str]:
    regexes = [
        glob.translate(pattern, recursive=True, include_hidden=True)
        for pattern in patterns
    ]
    combined = "|".join(f"(?:{p})" for p in regexes)
    return re.compile(combined)


EXCLUDE_RE = get_glob_re(EXCLUDE_PATTERNS)
INCLUDE_RE = get_glob_re(INCLUDE_PATTERNS)


def strip_elf(src: bytes) -> bytes:
    with NamedTemporaryFile(delete_on_close=False) as f:
        f.write(src)
        f.close()
        check_call(["strip", f.name])
        with open(f.name, "rb") as f2:
            return f2.read()


def get_compressed_size(data: bytes) -> int:
    if data.startswith(ELF_MAGIC):
        data = strip_elf(data)
    return len(zlib.compress(data, COMPRESSION_LEVEL))


def get_pkg_compressed_size(root: Path, pkg: Pkg) -> int:
    total_size = 0
    for name, pkgpath in pkg.paths.items():
        if pkgpath.path_type == PathType.HARDLINK:
            data = root.joinpath(name).read_bytes()
            compressed_size = get_compressed_size(data)
            total_size += compressed_size
    return total_size


def print_sizes(sizes: dict[str, int]) -> None:
    total_size = sum(sizes.values())
    sorted_sizes = sorted(((size, name) for name, size in sizes.items()), reverse=True)
    for size, name in sorted_sizes:
        print(f"{size:>10,}  {size / total_size:>5.1%}  {name}")


def copy_without_excluded_packages(src_root: Path, dst_root: Path) -> Platform:
    pkgs0, platform = get_pkgs(src_root)
    pkgs = [pkg for pkg in pkgs0 if pkg.name not in EXCLUDE_PACKAGES]
    if any(pkg.name == "meld" for pkg in pkgs):
        raise RuntimeError(
            f"Not expecting to find a conda package 'meld' in {src_root}"
        )
    sorted_pkgs = sorted(pkgs, key=lambda pkg: pkg.name)
    pkgs_s = [f"{pkg.name}=={pkg.version}" for pkg in sorted_pkgs]
    info(f"Copying packages: {pkgs_s}")
    for pkg in pkgs:
        for relpath, pkgpath in pkg.paths.items():
            src = src_root / relpath
            dst = dst_root / relpath
            path_type = pkgpath.path_type
            if (path_type == PathType.SOFTLINK) != src.is_symlink():
                raise RuntimeError(
                    f"{src}.is_symlink() not as expected by pkg metadata"
                )
            if EXCLUDE_RE.match(relpath) and not INCLUDE_RE.match(relpath):
                continue
            if path_type in (PathType.HARDLINK, PathType.SOFTLINK):
                dst.parent.mkdir(parents=True, exist_ok=True)
                copy2(src, dst, follow_symlinks=False)
            elif path_type == PathType.DIRECTORY:
                dst.mkdir(parents=True, exist_ok=True)
            else:
                assert False
    share_dir = "Library/share" if platform == WIN else "share"
    check_call(["glib-compile-schemas", dst_root / share_dir / "glib-2.0/schemas"])
    return platform


def fix_fonts_conf(dst_root: Path, platform: Platform) -> None:
    etc_dir = "Library/etc" if platform == WIN else "etc"
    fonts_conf_fn = dst_root / etc_dir / "fonts/fonts.conf"
    fonts_conf0 = fonts_conf_fn.read_text()
    BEFORE = "<!-- Font directory list -->\n"
    if platform == WIN:
        # fonts.conf is in root/Library/etc/fonts, and the fonts are in root/fonts
        ADD = '\t<dir prefix="relative">../../../fonts</dir>\n'
    else:
        # fonts.conf is in root/etc/fonts, and the fonts are in root/fonts
        ADD = (
            '\t<dir prefix="relative">../../share/fonts</dir>\n'
            + '\t<dir prefix="relative">../../fonts</dir>\n'
        )
    fonts_conf = fonts_conf0.replace(BEFORE, BEFORE + ADD)
    if fonts_conf == fonts_conf0:
        raise RuntimeError(f"Didn't find pattern in {fonts_conf_fn}")
    fonts_conf_fn.write_text(fonts_conf)


def extract_meld_package(meld_pkg: Path, dst_root: Path, platform: Platform) -> None:
    if platform == WIN:
        site_packages = dst_root / "Lib/site-packages"
    else:
        (site_packages,) = dst_root.glob("lib/python*/site-packages")
    zf = ZipFile(meld_pkg)
    (pkg_zi,) = [zi for zi in zf.filelist if zi.filename.startswith("pkg-")]
    assert pkg_zi.filename.endswith(".zst")
    zst = zf.read(pkg_zi)
    tar_bytes = zstd.decompress(zst)
    tf = TarFile(fileobj=io.BytesIO(tar_bytes))
    members = tf.getnames()
    assert "python-scripts/meld" in members
    for member in members:
        data_io = tf.extractfile(member)
        assert data_io is not None
        data = data_io.read()
        if member == "python-scripts/meld":
            # Put the bin/meld script, which will not find the right python executable,
            # in lib/meld
            libdir = "Library/lib" if platform == WIN else "lib"
            dst = dst_root / libdir / "meld"
        else:
            assert member.startswith("site-packages/")
            dst = site_packages.parent / member
            dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)


def strip_elfs(root: Path):
    for path in root.rglob("*"):
        if not path.is_symlink() and path.is_file():
            with open(path, "rb") as f:
                maybe_magic = f.read(len(ELF_MAGIC))
                if maybe_magic == ELF_MAGIC:
                    check_call(["strip", path])


def deref_symlinks(root: Path, print_symlink_sizes: bool) -> int:
    sizes = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            src = path.resolve()
            relpath = str(path.relative_to(root))
            sizes[relpath] = get_compressed_size(src.read_bytes())
            path.unlink()
            copy2(src, path)
    if print_symlink_sizes:
        print_sizes(sizes)
    return sum(sizes.values())


def fix_shlib_names(
    subs: dict[bytes, bytes], shlib_re: re.Pattern[bytes], dst_root: Path
) -> None:
    for path in dst_root.rglob("*"):
        assert not path.is_symlink()
        if path.is_dir():
            continue
        src_data = path.read_bytes()
        dst_data = sub_shlibs_in_something(subs, shlib_re, src_data)
        if dst_data != src_data:
            path.write_bytes(dst_data)


def make_compressed_dir(uncompressed_root: Path, compressed_root: Path) -> None:
    if compressed_root.exists():
        raise RuntimeError(f"{compressed_root} exists")
    for path in uncompressed_root.rglob("*"):
        dst_path = compressed_root.joinpath(path.relative_to(uncompressed_root))
        assert not path.is_symlink()
        if path.is_dir():
            continue
        src_data = path.read_bytes()
        dst_data = zlib.compress(src_data, COMPRESSION_LEVEL)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(dst_data)


def shrink_env(
    src_root: Path,
    meld_pkg: Path,
    dst_root: Path,
    print_symlink_sizes: bool = False,
    compressed_dst: Path | None = None,
) -> None:
    if dst_root.is_dir() and len(list(dst_root.iterdir())) != 0:
        raise RuntimeError(f"Destination {dst_root} is not empty")

    info(f"Copying files from {src_root} to {dst_root}...")
    platform = copy_without_excluded_packages(src_root, dst_root)
    fix_fonts_conf(dst_root, platform)
    info(f"Extracting {meld_pkg}...")
    extract_meld_package(meld_pkg, dst_root, platform)

    if platform == Platform.LINUX:
        info("Stripping debug symbols...")
        strip_elfs(dst_root)

        libdir = dst_root / "lib"
        shlib_symlinks = get_shlib_symlinks(libdir)
        subs = get_shlib_subs(shlib_symlinks)
        shlib_re = get_shlib_re(subs)

        remove_shlib_symlinks(libdir, shlib_symlinks)
        wasted_bytes = deref_symlinks(dst_root, print_symlink_sizes)
        extra = (
            "" if print_symlink_sizes else " (add --print-symlink-sizes for details)"
        )
        info(f"Dereferenced symlinks. Wasted bytes: {wasted_bytes:,}" + extra)

        info("Fixing shlib names in files...")
        fix_shlib_names(subs, shlib_re, dst_root)

    if compressed_dst is not None:
        info(f"Compressing files into {compressed_dst}...")
        make_compressed_dir(dst_root, compressed_dst)

    if platform == WIN:
        entrypoint = dst_root / "meld.bat"
        entrypoint.write_text(WINDOWS_ENTRYPOINT_SCRIPT)
    else:
        entrypoint = dst_root / "bin/meld"
        entrypoint.write_text(ENTRYPOINT_SCRIPT)
        entrypoint.chmod(0o755)

    info("Done!")


def main():
    from argparse import ArgumentParser

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = ArgumentParser(description="Shrink a conda env for packaging")
    parser.add_argument(
        "--print-pkg-sizes",
        action="store_true",
        help="Print the sizes each conda package would take in the compressed archive",
    )
    parser.add_argument(
        "--print-symlink-sizes",
        action="store_true",
        help="Print sizes wasted by dereferencing symlinks",
    )
    parser.add_argument(
        "--compressed",
        type=Path,
        help="Create a mirror with all files compressed, to help find large files in"
        " the resulting archive",
    )
    parser.add_argument("src", type=Path)
    parser.add_argument("meld_pkg", type=Path, help="The meld*.conda file")
    parser.add_argument("dst", type=Path, nargs="?")
    args = parser.parse_args()

    if not args.dst and not args.print_pkg_sizes:
        parser.error("If --print-pkg-sizes is not given, dst must be given")

    if args.print_pkg_sizes:
        pkgs, _platform = get_pkgs(args.src)
        sizes = {
            pkg.name: get_pkg_compressed_size(args.src, pkg)
            for pkg in pkgs
            if pkg.name not in EXCLUDE_PACKAGES
        }
        print_sizes(sizes)

    if args.dst is not None:
        shrink_env(
            args.src,
            args.meld_pkg,
            args.dst,
            args.print_symlink_sizes,
            args.compressed,
        )


if __name__ == "__main__":
    main()
