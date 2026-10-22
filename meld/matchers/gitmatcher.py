# Copyright (C) 2026 Tomáš Hnyk <tomashnyk@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Line matching done by git, so that git's diff algorithms can be used."""

import logging
import os
import re
import shutil
import subprocess
import tempfile

from meld.matchers.myers import MyersSequenceMatcher

log = logging.getLogger(__name__)

_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def git_available():
    return shutil.which("git") is not None


def _write(path, lines):
    with open(path, "w", encoding="utf-8", errors="surrogatepass", newline="") as f:
        f.writelines(line + "\n" for line in lines)


class GitSequenceMatcher(MyersSequenceMatcher):
    def __init__(self, isjunk=None, a="", b="", algorithm="myers"):
        super().__init__(isjunk, a, b)
        self.algorithm = algorithm

    def initialise(self):
        with tempfile.TemporaryDirectory(prefix="meld-diff-") as tmp:
            old, new = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            _write(old, self.a)
            _write(new, self.b)
            try:
                result = subprocess.run(
                    [
                        "git",
                        "diff",
                        "--no-index",
                        "--no-color",
                        "--no-ext-diff",
                        "--no-textconv",
                        "-U0",
                        f"--diff-algorithm={self.algorithm}",
                        old,
                        new,
                    ],
                    capture_output=True,
                    check=False,
                )
            except OSError as err:
                result = None
                log.warning("Cannot run git, using Meld's own matcher: %s", err)
        if result is None or result.returncode not in (0, 1):
            if result is not None:
                log.warning(
                    "git diff failed, using Meld's own matcher: %s",
                    result.stderr.decode(errors="replace").strip(),
                )
            yield from super().initialise()
            return

        blocks = []
        i = j = 0
        for hunk in _HUNK.finditer(result.stdout):
            a_start, a_len = int(hunk[1]), int(hunk[2] or 1)
            b_start, b_len = int(hunk[3]), int(hunk[4] or 1)
            # With a length of 0, git's start is the line before the gap
            a_start = a_start - 1 if a_len else a_start
            b_start = b_start - 1 if b_len else b_start
            if a_start > i:
                blocks.append((i, j, a_start - i))
            i, j = a_start + a_len, b_start + b_len
        if len(self.a) > i:
            blocks.append((i, j, len(self.a) - i))
        blocks.append((len(self.a), len(self.b), 0))
        self.matching_blocks = blocks
        yield 1
