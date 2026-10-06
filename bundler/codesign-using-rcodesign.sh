#!/usr/bin/env bash
# Use rcodesign to allow using "codesign" on linux.
# This just takes the last argument and passes it to rcodesign.
# rcodesign doesn't have a `-q` option, so we print the output on non-zero return.
out=$(rcodesign sign "${!#}" 2>&1) || { echo "$out" >&2; exit 1; }\n
