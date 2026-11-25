"""Independently verify a rendered file against the M0.1/M0.2 output profile.

Used by the container verification entrypoint. Deliberately does not import
``polymation``: it parses ffprobe's JSON itself, so a bug in the application's
own verification cannot hide a bad render.

Usage: inspect_output.py <profile> <video-path>
  profile: "final" (1920x1080) or "preview" (960x540)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

PROFILES: dict[str, tuple[int, int]] = {
    "final": (1920, 1080),
    "preview": (960, 540),
}

FRAME_RATE = "30/1"
VIDEO_CODEC = "h264"
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
AUDIO_SAMPLE_RATE = "48000"


def main(argv: list[str]) -> int:
    """Inspect and check one rendered file. Returns a process exit code."""
    if len(argv) != 3 or argv[1] not in PROFILES:
        print(f"usage: {argv[0]} {{{'|'.join(PROFILES)}}} <video-path>", file=sys.stderr)
        return 2

    profile, path = argv[1], argv[2]
    expected_width, expected_height = PROFILES[profile]

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        print("ffprobe is not on PATH", file=sys.stderr)
        return 1

    completed = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(f"ffprobe failed: {completed.stderr.strip()}", file=sys.stderr)
        return 1

    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    container = payload.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        print("no video stream in the output", file=sys.stderr)
        return 1
    if audio is None:
        print("no audio stream in the output", file=sys.stderr)
        return 1

    duration = float(container.get("duration", 0.0))

    print(f"  profile     {profile}")
    print(f"  resolution  {video['width']}x{video['height']}")
    print(f"  frame rate  {video['r_frame_rate']} ({video.get('nb_frames')} frames)")
    print(f"  video       {video['codec_name']} {video.get('profile')} {video['pix_fmt']}")
    print(f"  audio       {audio['codec_name']} {audio['sample_rate']}Hz {audio['channels']}ch")
    print(f"  duration    {duration}s")
    print(f"  size        {container.get('size')} bytes")

    failures: list[str] = []

    def expect(label: str, actual: object, wanted: object) -> None:
        if actual != wanted:
            failures.append(f"{label} is {actual!r}, expected {wanted!r}")

    expect("width", video["width"], expected_width)
    expect("height", video["height"], expected_height)
    expect("frame rate", video["r_frame_rate"], FRAME_RATE)
    expect("video codec", video["codec_name"], VIDEO_CODEC)
    expect("pixel format", video["pix_fmt"], PIXEL_FORMAT)
    expect("audio codec", audio["codec_name"], AUDIO_CODEC)
    expect("audio sample rate", audio["sample_rate"], AUDIO_SAMPLE_RATE)

    if duration <= 0:
        failures.append("duration is not positive")

    if failures:
        for failure in failures:
            print(f"  FAIL: {failure}", file=sys.stderr)
        return 1

    print("  Output conforms to the specification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
