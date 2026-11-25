#!/usr/bin/env bash
#
# Set up a local development environment for Polymation Studio.
#
# Prefers uv (the approved dependency manager). Falls back to python -m venv
# plus pip when uv is unavailable, so a machine without uv can still run the
# test suite. Never installs system packages; FFmpeg must be installed
# separately, and `polymation doctor` prints the exact command for your
# platform.
#
# Usage: scripts/bootstrap.sh
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"

if command -v uv >/dev/null 2>&1; then
    echo "==> uv found: $(uv --version)"
    echo "==> Installing the pinned Python from .python-version"
    uv python install
    echo "==> Syncing dependencies (including dev extras)"
    if [ -f uv.lock ]; then
        uv sync --frozen --extra dev
    else
        echo "    No uv.lock present; resolving and writing one."
        uv sync --extra dev
    fi
    RUN="uv run"
else
    echo "==> uv not found; falling back to venv + pip."
    echo "    Install uv for reproducible installs: https://docs.astral.sh/uv/"

    PYTHON=""
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "error: no suitable Python interpreter found (need 3.11 or newer)." >&2
        exit 1
    fi

    echo "==> Using $PYTHON ($("$PYTHON" --version 2>&1))"
    [ -d .venv ] || "$PYTHON" -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e ".[dev]"
    RUN=".venv/bin"
fi

echo
echo "==> Environment diagnostics"
if [ "$RUN" = "uv run" ]; then
    uv run polymation doctor || true
else
    .venv/bin/polymation doctor || true
fi

echo
echo "Next steps:"
echo "  Run the tests:        ${RUN} pytest"
echo "  Lint and format:      ${RUN} ruff check . && ${RUN} ruff format --check ."
echo "  Build the demo:       ${RUN} polymation demo create"
echo "  Render the demo:      ${RUN} polymation demo render"
