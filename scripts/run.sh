#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
exec .venv/bin/python -m uvicorn game_harness.app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-18080}" --workers 1
