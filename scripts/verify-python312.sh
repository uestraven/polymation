#!/usr/bin/env bash
#
# Validate the repository against the PINNED Python 3.12 in an isolated
# container, then run the full suite and the rendering demonstration.
#
# Why this exists: some development hosts cannot run the pinned interpreter.
# On glibc < 2.28 (for example Amazon Linux 2, glibc 2.26), published Pillow
# wheels target manylinux_2_28, so a standalone CPython 3.12 that links the
# system glibc finds no compatible wheel and falls back to a source build that
# needs libjpeg headers. A container supplies a modern glibc without touching
# the host.
#
# This is a DEVELOPMENT VERIFICATION environment only. Containers are NOT a
# runtime or deployment requirement for Polymation Studio: the application runs
# directly on a host with Python 3.11+ and FFmpeg. See docs/DECISIONS.md D-014.
#
# The repository is mounted read-only and copied inside the container, so the
# host working tree and host .venv are never modified.
#
# Usage: scripts/verify-python312.sh [pytest args...]
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"

IMAGE="polymation-verify:py312"
PINNED_PYTHON="$(tr -d '[:space:]' < .python-version)"

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker is not installed; cannot run the isolated 3.12 check." >&2
    echo "       Run scripts/verify.sh for host validation instead." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "error: the docker daemon is not reachable." >&2
    exit 1
fi

echo "==> Building $IMAGE (pinned Python $PINNED_PYTHON + FFmpeg)"
docker build \
    --build-arg "PYTHON_VERSION=${PINNED_PYTHON}" \
    -f docker/verify.Dockerfile \
    -t "$IMAGE" \
    docker/

echo
echo "==> Running verification inside the container"
docker run --rm \
    -v "$REPOSITORY_ROOT:/src:ro" \
    -e "PYTEST_ARGS=${*:-}" \
    "$IMAGE"
