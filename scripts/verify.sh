#!/usr/bin/env bash
#
# Run the same checks CI runs, locally, in the same order.
#
# Exits non-zero on the first failure. Reports clearly when the FFmpeg
# integration tests were skipped, because a skip means the M0.1 rendering
# acceptance criteria are NOT satisfied by this run.
#
# Usage: scripts/verify.sh
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"

if command -v uv >/dev/null 2>&1 && [ -d .venv ]; then
    RUN="uv run"
elif [ -x .venv/bin/python ]; then
    RUN=".venv/bin/python -m"
else
    echo "error: no environment found. Run scripts/bootstrap.sh first." >&2
    exit 1
fi

run() {
    echo
    echo "==> $*"
    # shellcheck disable=SC2086
    $RUN "$@"
}

run ruff check .
run ruff format --check .
run pytest -q

echo
echo "==> Environment readiness"
if [ "$RUN" = "uv run" ]; then
    uv run polymation doctor || DOCTOR_FAILED=1
else
    .venv/bin/polymation doctor || DOCTOR_FAILED=1
fi

if [ "${DOCTOR_FAILED:-0}" = "1" ]; then
    echo
    echo "FFmpeg is unavailable, so the rendering demonstration was NOT executed."
    echo "M0.1 rendering acceptance (A15-A17) remains unsatisfied by this run."
    exit 1
fi

echo
echo "==> Technical demonstration"
if [ "$RUN" = "uv run" ]; then
    uv run polymation demo create --recreate
    uv run polymation demo render --overwrite
else
    .venv/bin/polymation demo create --recreate
    .venv/bin/polymation demo render --overwrite
fi

echo
echo "All local checks passed, including the rendering demonstration."
