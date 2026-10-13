#!/usr/bin/env bash
# One-command start: creates the venv, installs deps, runs the server.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python server.py
