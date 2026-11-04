#!/usr/bin/env bash
# Run with bash; installs only into this module's virtual environment.
set -euo pipefail
ROBOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'This setup is validated for Apple Silicon macOS only.' >&2
  exit 1
fi
ROBOT_PYTHON="${ROBOT_PYTHON:-}"
if [[ -z "$ROBOT_PYTHON" ]]; then
  for candidate in python3.11 /opt/homebrew/opt/python@3.11/bin/python3.11 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ROBOT_PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$ROBOT_PYTHON" ]]; then
  echo 'Install Python 3.11, then set ROBOT_PYTHON to its executable.' >&2
  exit 1
fi
"$ROBOT_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 required"'
if [[ ! -d "$ROBOT_DIR/.venv" ]]; then
  "$ROBOT_PYTHON" -m venv "$ROBOT_DIR/.venv"
fi
ROBOT_VENV_PYTHON="$ROBOT_DIR/.venv/bin/python"
"$ROBOT_VENV_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 11), "Existing .venv must use Python 3.11"'
"$ROBOT_VENV_PYTHON" -m pip install 'pip==25.3' 'setuptools==80.9.0' 'wheel==0.45.1'
"$ROBOT_VENV_PYTHON" -m pip install -r "$ROBOT_DIR/requirements-macos.lock"
"$ROBOT_VENV_PYTHON" -m pip install --no-build-isolation --no-deps -e "$ROBOT_DIR/compat"
"$ROBOT_VENV_PYTHON" -m pip check
"$ROBOT_VENV_PYTHON" "$ROBOT_DIR/check_environment.py"
