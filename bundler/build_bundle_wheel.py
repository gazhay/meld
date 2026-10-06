#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from shlex import quote
from shutil import copy, copytree, rmtree
from subprocess import check_call
from tempfile import mkdtemp

from .conda_parse import Arch, Platform

try:
    import tomllib
except ImportError:
    tomllib = None

logger = logging.getLogger(__name__)
warning = logger.warning

PLATFORM_TAG = {
    (Platform.LINUX, Arch.X86_64): "manylinux_2_28_x86_64",
    (Platform.WIN, Arch.X86_64): "win_amd64",
    (Platform.OSX, Arch.ARM64): "macosx_11_0_arm64",
    (Platform.OSX, Arch.X86_64): "macosx_10_12_x86_64",
}

mydir = Path(__file__).absolute().parent
repodir = mydir.parent


def get_my_version() -> str:
    s = repodir.joinpath("meson.build").read_text()
    match = re.search(r"version\s*:\s*'([^']+)'", s)
    assert match is not None
    return match.group(1)


MELD_VERSION = get_my_version()


def verify_bundle_pyproject() -> None:
    if tomllib is None:
        warning("Not validating bundle-pyproject.toml because tomllib unavailable")
        return
    main_pyproj_path = repodir.joinpath("pyproject.toml")
    main_pyproj = tomllib.loads(main_pyproj_path.read_text())
    bundle_pyproj_path = mydir.joinpath("bundle-pyproject.toml")
    bundle_pyproj = tomllib.loads(bundle_pyproj_path.read_text())

    expected_proj = main_pyproj["project"].copy()
    for k in ("requires-python", "dependencies", "dynamic"):
        del expected_proj[k]
    expected_proj["name"] = "meldmerge"
    expected_proj["version"] = "{VERSION}"
    expected_proj["scripts"] = {
        "meld": "meld_bundle:main",
        "meldmerge": "meld_bundle:main",
    }

    bundle_proj = bundle_pyproj["project"]
    if bundle_proj != expected_proj:
        for key in sorted(set(expected_proj.keys()) | set(bundle_proj.keys())):
            expected = expected_proj.get(key)
            actual = bundle_proj.get(key)
            if actual != expected:
                print(f"{key}\n{actual=}\n{expected=}\n\n")
        raise AssertionError("bundle-pyproject.toml doesn't match pyproject.toml")


def replace(s: str, old: str, new: str) -> str:
    r = s.replace(old, new)
    if r == s:
        raise ValueError(f"Didn't find {old!r} in {s!r}")
    return r


def get_platform(appdir: Path) -> tuple[Platform, Arch]:
    platform_data_s = (appdir / "etc/platform.json").read_text()
    platform_data_d = json.loads(platform_data_s)
    platform_s = platform_data_d["platform"]
    assert isinstance(platform_s, str)
    platform = Platform(platform_s)
    arch_s = platform_data_d["arch"]
    assert isinstance(arch_s, str)
    arch = Arch(arch_s)
    return platform, arch


def build_bundle_wheel(
    appdir: Path, outdir: Path, tmpdir: Path | None = None, remove_tmpdir: bool = True
):
    verify_bundle_pyproject()
    platform, arch = get_platform(appdir)

    if tmpdir is None:
        tmpdir = Path(mkdtemp(prefix="meld-"))
    else:
        tmpdir.mkdir()

    pyproject0 = mydir.joinpath("bundle-pyproject.toml").read_text()
    pyproject1 = replace(pyproject0, "{VERSION}", MELD_VERSION)
    pyproject = replace(pyproject1, "{PLATFORM_TAG}", PLATFORM_TAG[platform, arch])
    tmpdir.joinpath("pyproject.toml").write_text(pyproject)

    copy(repodir / "README.md", tmpdir)
    copy(repodir / "COPYING", tmpdir)
    copy(mydir / "hatch_force_platform_tag.py", tmpdir)

    pkgdir = tmpdir / "meld_bundle"
    pkgdir.mkdir()
    bundle_shim = (
        "bundle_shim_windows.py" if platform == Platform.WIN else "bundle_shim.py"
    )
    copy(mydir / bundle_shim, pkgdir / "__init__.py")

    copytree(appdir, pkgdir / "appdir", symlinks=True)

    args = ["python", "-m", "build", "--outdir", outdir, "--wheel", tmpdir]
    print(" ".join(quote(str(arg)) for arg in args), file=sys.stderr)
    check_call(args)

    if remove_tmpdir:
        rmtree(tmpdir)


def main():
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Build a bundle wheel from an appdir")
    parser.add_argument(
        "--tmpdir",
        type=Path,
        help="Directory in which the project will be created. Must not exist.",
    )
    parser.add_argument("appdir", type=Path, help="The path of the appdir")
    parser.add_argument("outdir", type=Path, help="Directory to write the wheel in")
    args = parser.parse_args()

    remove_tmpdir = args.tmpdir is None
    build_bundle_wheel(args.appdir, args.outdir, args.tmpdir, remove_tmpdir)


if __name__ == "__main__":
    main()
