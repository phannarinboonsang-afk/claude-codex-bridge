#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if command -v python3.11 >/dev/null 2>&1; then PY=python3.11; else PY=python3; fi
exec "$PY" "$ROOT/scripts/verify.py" "$@"
