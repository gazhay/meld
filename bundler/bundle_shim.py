#!/usr/bin/env python3

import subprocess
import sys
from os.path import dirname, join


def main():
    appdir = join(dirname(__file__), "appdir")
    if sys.platform == "win32":
        python = join(appdir, "python.exe")
        meld_script = join(appdir, "Library", "lib", "meld")
    else:
        python = join(appdir, "bin/python3.14")
        meld_script = join(appdir, "lib", "meld")
    rc = subprocess.call([python, meld_script, *sys.argv[1:]])
    sys.exit(rc)


if __name__ == "__main__":
    main()
