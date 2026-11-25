#!/usr/bin/env bash
#
# Runs inside the verification container. Copies the read-only mounted
# repository into a writable workspace, installs the locked dependencies under
# the pinned interpreter, and runs the same checks as CI — including a real
# FFmpeg render.
#
# The host tree is mounted read-only, so nothing here can modify it.
set -euo pipefail

echo "==> Copying /src -> /work (host tree is read-only)"
# Exclude host build artefacts so the container resolves its own environment.
tar -C /src \
    --exclude='./.venv' \
    --exclude='./.git' \
    --exclude='./projects/*' \
    --exclude='./fixtures/demo' \
    --exclude='./.pytest_cache' \
    --exclude='./.ruff_cache' \
    --exclude='*/__pycache__' \
    -cf - . | tar -C /work -xf -

mkdir -p /work/projects /work/fixtures

echo
echo "==> Environment"
python -VV
echo "pinned (.python-version): $(tr -d '[:space:]' < /work/.python-version)"
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
uv --version
echo "glibc: $(getconf GNU_LIBC_VERSION)"

PINNED="$(tr -d '[:space:]' < /work/.python-version)"
RUNNING="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PINNED" != "$RUNNING" ]; then
    echo "::error:: running Python $RUNNING but .python-version pins $PINNED" >&2
    exit 1
fi
echo "Running interpreter matches the pin ($PINNED)."

echo
echo "==> uv sync --frozen --extra dev (installs exactly uv.lock)"
uv sync --frozen --extra dev

echo
echo "==> polymation doctor"
uv run polymation doctor

echo
echo "==> ruff check"
uv run ruff check .

echo
echo "==> ruff format --check"
uv run ruff format --check .

echo
echo "==> pytest (FFmpeg integration tests must NOT skip here)"
# shellcheck disable=SC2086
uv run pytest -q --junitxml=/work/pytest-report.xml ${PYTEST_ARGS:-}

python - <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse("/work/pytest-report.xml").getroot()
suite = root.find("testsuite") if root.tag == "testsuites" else root
skipped = [
    case.get("name")
    for case in suite.iter("testcase")
    if case.find("skipped") is not None
]
print(
    f"tests={suite.get('tests')} failures={suite.get('failures')} "
    f"errors={suite.get('errors')} skipped={suite.get('skipped')} "
    f"time={suite.get('time')}s"
)
if skipped:
    print("skipped tests present:", *skipped, sep="\n  ")
    sys.exit(1)
print("No tests were skipped.")
PY

echo
echo "==> Rendering demonstration"
uv run polymation demo create --recreate
uv run polymation demo render --overwrite

echo
echo "==> Independent ffprobe verification"
OUTPUT=/work/projects/demo-technical-fixture/output/demo-technical-fixture.mp4
test -s "$OUTPUT"
# A separate script that parses ffprobe JSON itself, so a bug in the
# application's own verification cannot hide a bad render.
python /usr/local/bin/inspect_output.py final "$OUTPUT"

echo "sha256: $(sha256sum "$OUTPUT" | cut -d' ' -f1)"

echo
echo "==> All checks passed under the pinned Python $PINNED"
