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

from .conda_parse import PathType, Pkg, get_pkgs
from .remove_shlib_symlinks import (
    get_shlib_re,
    get_shlib_subs,
    get_shlib_symlinks,
    remove_shlib_symlinks,
    sub_shlibs_in_something,
)

COMPRESSION_LEVEL = 9
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

EXCLUDE_PACKAGES = {
    "bzip2",
    "ca-certificates",
    "krb5",
    "libcups",
    "libsqlite",
    "ncurses",
    "pcre2",
    "tk",
}

# If the path from the root of the env matches one of EXCLUDE_PATTERNS and doesn't
# match any of INCLUDE_PATTERNS, it's not copied to the dest env

DYNMODS = "_decimal _sha3 _ctypes _pickle _sqlite _ssl _curses _curses_panel".split()

EXCLUDE_PATTERNS = [
    "bin/**",
    "conda-meta/**",
    "include/**",
    "lib/cairo/libcairo-fdr.a",
    "lib/icu/**",  # contains a symlink to a directory, not needed
    "lib/libpython*",
    "lib/python*/ensurepip/**",
    "lib/python*/idlelib/**",
    *(f"lib/python*/lib-dynload/{x}.*" for x in DYNMODS),
    "libexec/**",
    "sbin/**",
    "share/doc/**",
    "share/icons/Adwaita/cursors/progress",  # heavy cursor
    "share/icons/Adwaita/cursors/wait",  # heavy cursor
    "share/icons/Adwaita/cursors/watch",  # heavy cursor
    "share/icons/Adwaits/symbolic/status/**",  # ~1MB, not used
    "share/locale/**",
    "share/man/**",
    "share/python_compiler_compat/**",
    "share/zoneinfo/**",
    "share/X11/xkb",  # a symlink to share/xkeyboard-config-2
    "ssl/**",
    "var/cache/**",
    "*-conda-*/**",
    "**/__pycache__/**",
    "**/Makefile",
    "**/*.cmake",
    "**/valgrind/**",
    "**/*.a",
]

INCLUDE_PATTERNS = [
    "bin/meld",
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


def copy_without_excluded_packages(src_root: Path, dst_root: Path) -> None:
    pkgs0 = get_pkgs(src_root)
    pkgs = {name: pkg for name, pkg in pkgs0.items() if name not in EXCLUDE_PACKAGES}
    if "meld" in pkgs:
        raise RuntimeError(
            f"Not expecting to find a conda package 'meld' in {src_root}"
        )
    sorted_pkgs = sorted(pkgs.values(), key=lambda pkg: pkg.name)
    pkgs_s = [f"{pkg.name}=={pkg.version}" for pkg in sorted_pkgs]
    info(f"Copying packages: {pkgs_s}")
    for pkg in pkgs.values():
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
    check_call(["glib-compile-schemas", dst_root / "share/glib-2.0/schemas"])


def fix_fonts_conf(dst_root: Path) -> None:
    fonts_conf_fn = dst_root / "etc/fonts/fonts.conf"
    fonts_conf0 = fonts_conf_fn.read_text()
    PATTERN = r"\t<dir>.+/share/fonts</dir>\n\t<dir>.+/fonts</dir>\n"
    REL = (
        '\t<dir prefix="relative">../../share/fonts</dir>\n'
        + '\t<dir prefix="relative">../../fonts</dir>\n'
    )
    fonts_conf = re.sub(PATTERN, REL, fonts_conf0)
    if fonts_conf == fonts_conf0:
        raise RuntimeError(f"Didn't find pattern in {fonts_conf_fn}")
    fonts_conf_fn.write_text(fonts_conf)


def extract_meld_package(meld_pkg: Path, dst_root: Path) -> None:
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
            dst = dst_root / "lib/meld"
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
    copy_without_excluded_packages(src_root, dst_root)
    fix_fonts_conf(dst_root)
    info(f"Extracting {meld_pkg}...")
    extract_meld_package(meld_pkg, dst_root)
    info("Stripping debug symbols...")
    strip_elfs(dst_root)

    libdir = dst_root / "lib"
    shlib_symlinks = get_shlib_symlinks(libdir)
    subs = get_shlib_subs(shlib_symlinks)
    shlib_re = get_shlib_re(subs)

    remove_shlib_symlinks(libdir, shlib_symlinks)
    wasted_bytes = deref_symlinks(dst_root, print_symlink_sizes)
    extra = "" if print_symlink_sizes else " (add --print-symlink-sizes for details)"
    info(f"Dereferenced symlinks. Wasted bytes: {wasted_bytes:,}" + extra)

    info("Fixing shlib names in files...")
    fix_shlib_names(subs, shlib_re, dst_root)

    if compressed_dst is not None:
        info(f"Compressing files into {compressed_dst}...")
        make_compressed_dir(dst_root, compressed_dst)

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
        pkgs = get_pkgs(args.src)
        sizes = {
            pkg.name: get_pkg_compressed_size(args.src, pkg) for pkg in pkgs.values()
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
