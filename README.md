# Polymation Studio

Internal software platform for **Polymation**, a video channel exploring the shared
creative DNA of classic Japanese role-playing games and retro anime (roughly
1990–2009): storytelling, world-building, character design, animation technique,
soundtrack craft and historical context.

Polymation Studio is the production *engine* — a reusable local toolchain for
research, scripting, media management, rights tracking, rendering and (much
later) packaging. It is not a content bot, and it does not publish anything.

**Current milestone: M0.2 — Core Video Composition & Rendering Engine.**

M0.1 delivered the foundation: project scaffolding, versioned data contracts,
media import with provenance, validation, and a narrow FFmpeg demonstration.

M0.2 replaces that demonstration with a real composition engine — a frame-exact
timeline, video and still sources, pan and zoom, timed overlays, independent
audio buses with ducking and loudness normalisation, SRT captions, two output
profiles, and content-addressed per-scene caching so editing one scene
re-renders one scene.

See [`docs/COMPOSITION.md`](docs/COMPOSITION.md) for how to use it,
[`docs/DEVELOPMENT_STATUS.md`](docs/DEVELOPMENT_STATUS.md) for exactly what is
and is not implemented, and
[`docs/ACCEPTANCE_CRITERIA.md`](docs/ACCEPTANCE_CRITERIA.md) for the evidence
behind each acceptance criterion.

---

## System requirements

| Requirement | Notes |
|---|---|
| Python 3.12 | Pinned in `.python-version`. The code also runs on 3.11 (`requires-python = ">=3.11"`). |
| FFmpeg + ffprobe | Required for rendering, and for importing any audio or video asset. Images work without it. |
| Local filesystem | All project state lives on disk. No database, no server, no cloud. |
| No credentials | M0.1 uses no API keys and no paid services. Expected external cost: **$0**. |

Everything runs offline. Nothing in this repository contacts the network.

### Installing FFmpeg

```bash
# Debian / Ubuntu
sudo apt-get install -y ffmpeg

# Amazon Linux 2
sudo amazon-linux-extras install -y epel && sudo yum install -y ffmpeg

# Homebrew (macOS or Linuxbrew)
brew install ffmpeg
```

If FFmpeg lives somewhere unusual, point `POLYMATION_FFMPEG` and
`POLYMATION_FFPROBE` at the binaries instead of adding them to `PATH`.

---

## Installation

### With `uv` (preferred)

```bash
uv python install               # installs the version pinned in .python-version
uv sync --frozen --extra dev    # installs exactly the versions in uv.lock
uv run polymation doctor
```

> **On older Linux (glibc < 2.28, e.g. Amazon Linux 2):** published Pillow
> wheels target `manylinux_2_28`, so uv's standalone CPython finds no compatible
> wheel and falls back to a source build that needs libjpeg headers. Point uv at
> an interpreter built against a newer glibc — a Homebrew Python works, because
> Homebrew ships its own:
>
> ```bash
> export UV_PYTHON=/home/linuxbrew/.linuxbrew/bin/python3.11
> uv sync --frozen --extra dev
> ```
>
> `polymation doctor` tells you when the running interpreter is not the pinned
> one. See `docs/DECISIONS.md` D-011.

### With `pip` and `venv` (fallback)

```bash
python3.12 -m venv .venv        # or python3.11
.venv/bin/pip install -e ".[dev]"
.venv/bin/polymation doctor
```

`polymation doctor` is the first thing to run. It reports every dependency, its
version, and exact installation instructions for anything missing. It exits `4`
when a required dependency is absent, so it is usable as a setup gate in
scripts.

---

## CLI

All commands accept `--json` for machine-readable output, and `--workspace PATH`
to override workspace discovery (which otherwise uses `$POLYMATION_HOME`, then
the nearest ancestor directory containing `channels/`).

```bash
# Environment diagnostics
polymation doctor
polymation fonts                      # are the proposed brand fonts installed?

# Projects
polymation new "Why Chrono Trigger Feels Like an Adventure" \
    --pillar story_world_deep_dive --subject jrpg --format standard --duration 240
polymation list
polymation status PROJECT_ID
polymation validate PROJECT_ID [--strict] [--no-verify-checksums]

# Media
polymation asset import PROJECT_ID path/to/file.png \
    --basis original_work --rights-holder "Your Name" --intended-use "B-roll for scene 2"
polymation asset list PROJECT_ID

# Rights
polymation rights report PROJECT_ID

# Technical demonstration (M0.1)
polymation demo create [--recreate]
polymation demo render [--overwrite]

# Composition timeline (M0.2)
polymation timeline compile PROJECT_ID [--profile final|preview] \
    [--narration ASSET_ID ...] [--music ASSET_ID] [--overwrite]
polymation timeline validate PROJECT_ID [--profile ...] [--strict]

# Rendering (M0.2)
polymation render PROJECT_ID --preview            # 960x540, fast
polymation render PROJECT_ID --final              # 1920x1080, publication quality
polymation render PROJECT_ID --final --scene scene-03   # one scene only
polymation render PROJECT_ID --final --force      # ignore the cache
polymation render PROJECT_ID --final --overwrite  # replace an existing output

# Caches (M0.2) — scoped to the project; never touches source media
polymation cache status PROJECT_ID
polymation cache clear PROJECT_ID [--profile preview|final]

# Inspect any video against a profile (M0.2)
polymation inspect VIDEO_PATH [--profile final] [--expect-duration 180]

# Engineering reference fixture (M0.2)
polymation reference build [--recreate]
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unclassified error |
| 2 | CLI usage error (reserved by Click) |
| 3 | Validation failed, or rendered output deviates from spec |
| 4 | Required dependency missing |
| 5 | Project not found |
| 6 | Conflict (project exists, duplicate asset, output exists) |
| 7 | Unsafe input (path traversal, bad identifier) |

### What a new project looks like

```
projects/<PROJECT_ID>/
├── manifest.json      # authoritative project state (versioned)
├── brief.md           # editorial brief, written by a human
├── script.json        # narration scenes, claims, pronunciation notes
├── storyboard.json    # ordered scenes, each holding ordered visual shots
├── assets.json        # imported media, with checksums and dimensions
├── rights.json        # one rights record per asset
├── approvals.json     # human approval records, bound to artefact checksums
├── assets/{images,footage,narration,graphics,music}/
├── research/ captions/ previews/ thumbnails/ output/
```

An empty draft project is *structurally valid* but neither *render-ready* nor
*publication-ready*. The CLI reports those three states separately and never
conflates them.

---

## The technical demonstration

```bash
polymation demo create      # generates original fixtures, builds the project
polymation demo render      # renders with FFmpeg, verifies with ffprobe
# output: projects/demo-technical-fixture/output/demo-technical-fixture.mp4
```

Fifteen seconds, three scenes: a Polymation title card, an original synthetic 4:3
geometric test pattern pillarboxed into 16:9 without distortion, and a closing
graphic with a vermilion accent. Audio is a quiet synthesised sine tone.

Output specification, verified by ffprobe on every render: 1920×1080, 30 fps,
H.264 video, AAC audio at 48 kHz, MP4 container.

Every fixture is generated from code in
[`src/polymation/rendering/fixtures.py`](src/polymation/rendering/fixtures.py) —
geometric primitives, text drawn with locally installed fonts, and arithmetic
sine synthesis. **No game footage, anime footage, commercial music or downloaded
artwork is used or required.**

This is a toolchain proof, not a channel intro and not a creative pilot. The
closing card says so on screen.

---

## The engineering reference fixture (M0.2)

```bash
polymation reference build --recreate
polymation render reference-engineering-fixture --preview
polymation render reference-engineering-fixture --final
```

Exactly **5400 frames = 180.000 s** at 30 fps: six scenes, 11 shots, 24
overlays, 7 audio segments, 15 generated assets. It exercises every fit mode,
every motion kind, stills and video together, a within-scene crossfade, fades at
the boundaries, every overlay primitive, the explicit short-source policy,
narration plus ducked music, and burned-in captions.

Nine synchronisation markers (a magenta block on known frames plus a click at
the corresponding samples) let A/V sync be **measured** rather than eyeballed.

```bash
scripts/benchmark.py --json results.json   # cold/warm timings, sync, loudness
```

Measured on an 8-vCPU Xeon 8252C with FFmpeg 9.1: cold preview ~54 s, cold final
~149 s, warm final ~1.2 s, and a one-scene edit rebuilds exactly one scene and
reuses five. Output: 5400/5400 frames decoded, −16.0 LUFS, 9/9 markers in both
channels.

Like the M0.1 demonstration, it is an engineering benchmark and **not an
episode**: narration is a synthetic tone, not speech, and rights are
deliberately unreviewed.

---

## Testing

```bash
uv run pytest                        # or: .venv/bin/pytest
uv run pytest -m "not ffmpeg"        # skip the FFmpeg integration tests
uv run ruff check .
uv run ruff format --check .

scripts/verify.sh                    # everything CI runs, on this host
scripts/verify-python312.sh          # the same, under the PINNED Python in Docker
```

Verification has two tiers, reported separately:

| Tier | Interpreter | How |
|---|---|---|
| **Host** | whatever is installed (3.11+) | `scripts/verify.sh` |
| **Pinned** | exactly `.python-version` | `scripts/verify-python312.sh` (Docker) |

The pinned tier exists because some hosts cannot install the pinned
interpreter's dependencies (see the glibc note above). It mounts the repository
read-only, copies it into a container, and fails if the running interpreter does
not match the pin. **Docker is a development dependency only** — the
application runs directly on a host with Python 3.11+ and FFmpeg, and no
container is involved in running it. See `docs/DECISIONS.md` D-014.

Tests use temporary directories and never touch the network. FFmpeg integration
tests execute real FFmpeg commands; when FFmpeg is absent they skip explicitly,
and the M0.1 rendering acceptance criteria are then **not** satisfied. CI
installs FFmpeg and runs them.

---

## Known limitations

These are real gaps, not deferred polish. Full detail with rationale in
[`docs/DEVELOPMENT_STATUS.md`](docs/DEVELOPMENT_STATUS.md) §4.

**Composition**

- **No cross-scene crossfades.** Scene-to-scene transitions are a cut or a fade
  through black. A cross-scene crossfade would couple adjacent scenes and break
  per-scene caching (`docs/DECISIONS.md` D-018).
- **No retiming.** Shots render at 1:1 speed — no slow motion, no speed ramps.
- **No motion on video sources.** Rejected by validation; pan and zoom apply to
  stills only.
- **No vertical shorts rendering.** The `short` format is a valid manifest
  value; nothing renders it.
- **No CJK glyphs.** Text is rendered to pixels by Pillow, which is what makes
  arbitrary text safe — but no installed font on the development host covers
  CJK, so Japanese text renders as tofu boxes. A real limitation for a channel
  about Japanese media; installing a CJK font fixes it with no code change.
- **Brand values and fonts are provisional.** Overlay text uses a substitute
  typeface and the tooling says so. Final typography is M0.3.

**Content and rights**

- **No content generation of any kind.** No research, no LLM integration, no
  script generation, no image generation, no text-to-speech, no stock media
  acquisition. The schemas exist; the generators do not.
- **No caption generation.** Externally supplied SRT only — no transcription,
  no speech alignment.
- **No publishing, no analytics, no YouTube integration.** Out of scope and
  deliberately absent rather than stubbed.
- **Rights decisions are human-only.** The platform records provenance and
  refuses to approve user-supplied, proposed-fair-use or unknown material on its
  own. It makes no copyright determination.
- **No command creates an approval record.** The platform stores, verifies and
  invalidates approvals, but approving an asset still means hand-editing
  `rights.json` and `approvals.json`.

**Operations**

- **Advisory locking only.** A lock file prevents two commands on one machine
  from interleaving writes; it does not coordinate across NFS or between hosts.
- **Superseded cache entries are not garbage-collected.** Editing a scene leaves
  its old entry on disk. `cache status` reports total bytes so this is visible.
- **No cross-project index.** `polymation list` reads every manifest from disk.
- **Checksum verification cost is unbounded**, being proportional to total media
  size. `--no-verify-checksums` is the escape hatch.
- **CI has not yet completed successfully.** The workflow is configured and has
  run; the first run failed on a defect in its own assertion step, since fixed.
  Local and CI validation are reported separately and no claim is made about CI
  passing until a run goes green.

## Documentation

| Document | Contents |
|---|---|
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | Milestone roadmap, M0.1 through M8 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module structure, storage model, extension points |
| [`docs/COMPOSITION.md`](docs/COMPOSITION.md) | Timeline authoring, composition operations, audio, profiles, caching, recovery, benchmarks |
| [`docs/EDITORIAL_STANDARD.md`](docs/EDITORIAL_STANDARD.md) | Editorial principles and accuracy requirements |
| [`docs/MEDIA_RIGHTS.md`](docs/MEDIA_RIGHTS.md) | Rights policy and how the platform enforces it |
| [`docs/ACCEPTANCE_CRITERIA.md`](docs/ACCEPTANCE_CRITERIA.md) | M0.1 criteria A01–A24 with evidence |
| [`docs/ACCEPTANCE_CRITERIA_M02.md`](docs/ACCEPTANCE_CRITERIA_M02.md) | M0.2 criteria B01–B35 with evidence |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architectural decisions, with alternatives and consequences |
| [`docs/DEVELOPMENT_STATUS.md`](docs/DEVELOPMENT_STATUS.md) | Current status, defects, constraints, next action |
| [`CLAUDE.md`](CLAUDE.md) | Standing instructions for coding agents on this repository |
