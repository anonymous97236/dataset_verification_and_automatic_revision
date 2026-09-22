#!/usr/bin/env bash
set -euo pipefail

WEB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WEB_ROOT"

if [[ -x ".venv/bin/python" ]]; then
  WEB_PYTHON=".venv/bin/python"
else
  WEB_PYTHON="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$WEB_ROOT/src:$WEB_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$WEB_PYTHON" -m web.app
