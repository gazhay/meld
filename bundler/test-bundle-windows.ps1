# We use a script instead of separate commands insite .gitlab-ci.yml,
# since this seems to be needed in order to actually catch errors.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

tar -xf meld-win-x86_64.zip
$env:MELD_TURN_ON_HIGHLIGHT_SYNTAX = "1"
$env:MELD_SCREENSHOT_AND_EXIT = "appdir-screenshot.png"
meld-win-x86_64/meld.bat bundler/examples/calc_REMOTE.py bundler/examples/calc_BASE.py bundler/examples/calc_LOCAL.py

    # - python -m venv venv
    # - venv/bin/pip install meld*.whl
    # - MELD_SCREENSHOT_AND_EXIT=wheel-screenshot.png MELD_TURN_ON_HIGHLIGHT_SYNTAX=1 xvfb-run -a -s "-screen 0 1920x1080x24" venv/bin/meld bundler/examples/calc_{REMOTE,BASE,LOCAL}.py
