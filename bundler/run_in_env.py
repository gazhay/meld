#!/usr/bin/env python3

"""
run_in_env.py: this script sets environment variables needed to run meld
in a bundled environment.
"""

import os
import sys
from pathlib import Path
from subprocess import call


def main() -> int:
    if len(sys.argv) == 1 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 1
    mydir = Path(__file__).absolute().parent
    is_win = sys.platform == "win32"
    # Note: `prefix` is the conda env root on linux/mac, and root/Library on windows.
    prefix = mydir / "Library" if is_win else mydir.parent
    gi_typelib_path = prefix / "lib/girepository-1.0"
    if not gi_typelib_path.is_dir():
        raise RuntimeError(f"setup_conda_envvars: Expecting {gi_typelib_path} to exist")
    os.environ["GI_TYPELIB_PATH"] = str(gi_typelib_path)
    os.environ["XKB_CONFIG_ROOT"] = str(prefix / "share/xkeyboard-config-2")
    os.environ["FONTCONFIG_PATH"] = str(prefix / "etc/fonts")
    os.environ["FONTCONFIG_FILE"] = str(prefix / "etc/fonts/fonts.conf")
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(prefix / "share/glib-2.0/schemas")
    prev_xdg_data_dirs = os.environ.get("XDG_DATA_DIRS")
    xdg_data_dirs_suffix = ":" + prev_xdg_data_dirs if prev_xdg_data_dirs else ""
    os.environ["XDG_DATA_DIRS"] = str(prefix / "share") + xdg_data_dirs_suffix
    if is_win:
        env_root = prefix.parent
        os.environ["PATH"] = f"{env_root};{prefix / 'bin'};{os.environ['PATH']}"
    rc = call(sys.argv[1:])
    return rc


if __name__ == "__main__":
    sys.exit(main())
