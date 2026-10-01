#!/usr/bin/env python3

import subprocess
import sys
from os import environ
from pathlib import Path


def main():
    # This should be in sync with shrink_env.py:WINDOWS_ENTRYPOINT_SCRIPT
    mydir = Path(__file__).parent
    prefix = mydir / "appdir"
    environ["GI_TYPELIB_PATH"] = str(prefix / "Library/lib/girepository-1.0")
    environ["FONTCONFIG_PATH"] = str(prefix / "Library/etc/fonts")
    environ["FONTCONFIG_FILE"] = str(prefix / "Library/etc/fonts/fonts.conf")
    environ["GSETTINGS_SCHEMA_DIR"] = str(prefix / "Library/share/glib-2.0/schemas")
    environ["XDG_DATA_DIRS"] = str(prefix / "Library/share")
    environ["PATH"] = f"{prefix};{prefix / 'Library/bin'};{environ['PATH']}"

    rc = subprocess.call(
        [prefix / "python.exe", prefix / "Library/lib/meld", *sys.argv[1:]]
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
