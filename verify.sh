#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
uv sync --frozen --extra dev
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy --strict issue2pr
