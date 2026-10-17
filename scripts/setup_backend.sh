#!/usr/bin/env bash
# Temporary S1 bootstrap until S0 adds the shared pyproject.toml / uv.lock.
# These are already documented project dependencies, not a competing lockfile.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --no-project --python 3.12 python scripts/setup_backend.py
