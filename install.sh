#!/usr/bin/env bash
# Install HopBaleCount's console entry point.
#
# Runs as an unprivileged user on the customer's machine: it only uses pip
# against PyPI/build tooling that is already present, never sudo, apt or any
# system service. It installs and exits - it never starts anything.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found; Python 3.9+ is required (see INSTALL.md)" >&2
  exit 1
fi

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "error: Python 3.9 or newer is required (found $("$PYTHON" -V 2>&1))" >&2
  exit 1
}

# Fresh tooling first: old pip silently fails editable installs.
if ! "$PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1; then
  echo "note: could not refresh pip/setuptools/wheel (offline?); using what is installed" >&2
fi

"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install --no-build-isolation -e .

# Cheap self-check: a broken/absent module fails here, not at first use.
"$PYTHON" -c 'import hopbalecount; print("hopbalecount", hopbalecount.__version__, "installed")'

if command -v hopbalecount >/dev/null 2>&1; then
  echo "Run: hopbalecount --help   (or: python3 hopbalecount.py --help)"
else
  echo "Note: the 'hopbalecount' command is not on PATH; use: python3 hopbalecount.py --help"
fi
echo "Setup complete."
