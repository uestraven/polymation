# Isolated verification environment for the PINNED Python version.
#
# Development tooling only. Containers are NOT a runtime or deployment
# requirement for Polymation Studio — see docs/DECISIONS.md D-014 and
# scripts/verify-python312.sh.
#
# Exists because some development hosts have a glibc older than the
# manylinux_2_28 tag that published Pillow wheels target, which prevents the
# pinned interpreter from installing dependencies there.

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

# ffmpeg provides both ffmpeg and ffprobe, which the rendering integration
# tests execute for real. git is needed only so the entrypoint can report
# whether the copied tree is clean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# uv from PyPI rather than the shell installer, so the install is a normal
# verifiable package download.
RUN pip install --no-cache-dir "uv>=0.5,<1"

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY inspect_output.py /usr/local/bin/inspect_output.py
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
