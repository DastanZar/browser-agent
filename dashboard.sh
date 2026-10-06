#!/usr/bin/env bash
# Browser Agent dashboard launcher (macOS/Linux). First run installs everything. Needs Python 3.11+.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then PY="$c"; break; fi
  done
  [[ -n "$PY" ]] || { echo "ERROR: Python 3.11 or newer is required (https://www.python.org/downloads/)."; exit 1; }
  echo "First run: creating .venv with $PY and installing (one-time)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt || { rm -rf .venv; echo "ERROR: install failed (see above)."; exit 1; }
fi
ANONYMIZED_TELEMETRY=false exec .venv/bin/python dashboard.py
