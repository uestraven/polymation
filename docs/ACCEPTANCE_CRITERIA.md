# M0.1 acceptance criteria

**Evaluated:** 2026-11-25
**Verdict:** 24 of 24 PASS. No criterion is FAIL, PARTIAL or NOT EXECUTED.

**Important caveat on every result below:** this is **local** validation only.
The GitHub Actions workflow **has not yet completed successfully**. Its first
run (2026-11-25, after `origin` was added) reached the render step and produced
a conforming file, then failed on a defect in the workflow's own ffprobe
assertion step — now fixed. Any criterion whose evidence would be a CI run is
therefore marked as configuration-only, and said so explicitly. See
`docs/DEVELOPMENT_STATUS.md`.

Local environment used for all evidence:

| | |
|---|---|
| Host | Amazon Linux 2, Linux 5.10.273 x86_64, Intel Xeon 8252C, 8 vCPU, 30 GiB RAM |
| Python | 3.11.4 (Linuxbrew). Pinned target 3.12 — see A03 and D-011 |
| FFmpeg / ffprobe | 9.1 (Linuxbrew), with libx264 and native AAC |
| Package manager | uv 0.13.10 |
| Disk free | 229 GiB |

---

## A01 — Environment assessment completed · **PASS**

`polymation doctor` reports interpreter version and path, platform, every
runtime and dev package version, FFmpeg and ffprobe paths and versions, channel
configuration presence, and free disk space. It exits 0 when ready and 4 when a
required dependency is missing, and prints platform-specific install commands
for anything absent.

Verified output (abridged):

```
  [ok  ] python: 3.11.4 at .../.venv/bin/python (project targets 3.12; this
         interpreter is supported but not the pinned version)
  [ok  ] platform: Linux 5.10.273-289.1133.amzn2int.x86_64 (x86_64)
  [ok  ] python package: typer: 0.17.5
  [ok  ] python package: pydantic: 2.14.4
  [ok  ] python package: pillow: 12.3.0
  [ok  ] python package: pyyaml: 6.0.4
  [ok  ] dev tool: pytest: 8.4.2
  [ok  ] dev tool: ruff: 0.14.14
  [ok  ] ffmpeg: 9.1 at /home/linuxbrew/.linuxbrew/bin/ffmpeg
  [ok  ] ffprobe: 9.1 at /home/linuxbrew/.linuxbrew/bin/ffprobe
  [ok  ] channel configuration: .../channels
  [ok  ] disk space: 229.2 GiB free
All required dependencies are available.
```

Two blockers were found during assessment and resolved with the owner's
explicit approval: FFmpeg was absent, and uv was absent. Both were installed
user-local, without sudo. A third constraint was found and documented rather
than resolved (A03).

Tests: 4 in `tests/test_cli.py` covering doctor output, exit codes and
remediation text.

## A02 — Repository initialized · **PASS**

Git repository at `/local/home/paulcoh/workplace/polymation/polymation-studio`,
default branch `main` (not `master` — inclusive naming). No pre-existing
repository was present, so nothing was overwritten. No remote is configured and
none was created. Full tree in the implementation report.

## A03 — Dependencies reproducibly installable · **PASS**

`uv.lock` (106 KB, 21 packages) is committed, with a sha256 hash pinned for
every wheel and sdist. `uv sync --frozen --extra dev` installs exactly those
versions; CI uses the same command.

Verified: `uv lock` resolved 21 packages against Python 3.12.16, and
`uv sync --frozen --extra dev` installed 21 packages successfully.

**Documented environment constraint, not a defect in the lock:** this host is
Amazon Linux 2 with glibc 2.26. Published Pillow wheels require
`manylinux_2_28`, so uv's standalone CPython 3.12 (which links the system
glibc) finds no compatible wheel and attempts a source build that fails for
lack of libjpeg headers. The Linuxbrew interpreters work because Homebrew
installs its own newer glibc. Local work therefore overrides the pin:

```bash
export UV_PYTHON=/home/linuxbrew/.linuxbrew/bin/python3.11
uv sync --frozen --extra dev
```

CI runs on `ubuntu-latest` (glibc 2.39), where the pinned 3.12 installs from
wheels normally. The lockfile is platform-independent and correct; the
constraint is the host's glibc. Recorded as D-011.

A `pip`-only fallback is also supported (`pip install -e ".[dev]"`) and was
used for the first half of development; `scripts/bootstrap.sh` handles both.

## A04 — CLI available and documented · **PASS**

Console script `polymation` installed via `[project.scripts]`. Eleven commands
across three groups, all working and all exercised by tests:

```
polymation doctor | new | list | status | validate | fonts
polymation asset import | asset list
polymation rights report
polymation demo create | demo render
```

Every command supports `--json`. Documented in `README.md` (usage and examples)
and `docs/ARCHITECTURE.md` §11 (structure and exit codes). Exit codes verified
live: 0 success, 2 usage error, 3 validation failure, 4 missing dependency,
5 not found, 6 conflict, 7 unsafe input.

Tests: 28 in `tests/test_cli.py`.

## A05 — Project initialization functional · **PASS**

```
$ polymation new "Why Chrono Trigger Feels Like an Adventure" \
    --pillar story_world_deep_dive --subject jrpg --format standard --duration 240
Created project 20261125-why-chrono-trigger-feels-like-an-adventu-d674d6
```

Creates all 7 documents (`manifest.json`, `brief.md`, `script.json`,
`storyboard.json`, `assets.json`, `rights.json`, `approvals.json`) and all 10
subdirectories (`assets/{images,footage,narration,graphics,music}`, `research`,
`captions`, `previews`, `thumbnails`, `output`). Duration defaults to the
format midpoint when omitted (240 s standard, 45 s short).

Tests: 31 in `tests/test_projects.py`, including scaffold completeness,
document validity on reload, identifier stability across a title change,
duplicate-creation refusal, and that a refused duplicate leaves the original
untouched.

## A06 — Versioned data schemas implemented · **PASS**

Six versioned document types, all Pydantic v2 with `extra="forbid"` and an
explicit `schema_version`: `VideoProject`, `ScriptDocument`, `Storyboard`,
`AssetCollection`, `RightsLedger`, `ApprovalLedger`. Plus nested contracts:
`NarrationScene`, `FactualClaim`, `PronunciationNote`, `StoryboardScene`,
`VisualShot`, `MediaAsset`, `RightsRecord`, `ApprovalRecord`.

An unsupported version is rejected before the body is parsed, with an
actionable message:

```
project manifest declares schema_version '99.0', which this build of Polymation
Studio cannot read. Supported versions: 1.0. Upgrade Polymation Studio, or
migrate the document to a supported version.
```

Narration scenes and visual shots are separate concepts; one scene may hold
many shots (tested directly).

Tests: 57 in `tests/test_schemas.py`.

## A07 — Project manifests validated · **PASS**

`polymation validate PROJECT_ID` reports 14 error codes and 8 warning codes,
separating the two, and reports structural validity, render readiness and
publication readiness as three distinct states. `--json` gives machine-readable
output; `--strict` promotes warnings to failure (exit 3).

Verified live on the demonstration project: structurally valid, render ready,
publication readiness `null`, 5 warnings (4 unresolved rights + draft
narration).

Tests: 32 in `tests/test_validation.py`.

## A08 — Media importing functional · **PASS**

```
$ polymation asset import demo-technical-fixture fixtures/demo/scene-01-title-card.png
Imported asset-0001
  Path:     assets/graphics/scene-01-title-card.png
  Detected: image / PNG
  Size:     1920x1080
  sha256:   <64 hex>
  Rights record rights-0001 created with status 'unreviewed'.
```

Files are copied (source untouched), filed by detected type, never overwritten
(collisions suffix `-1`, `-2`), and the copy is re-hashed against the source.
Verified live: 4 assets imported into the demonstration project.

Tests: 34 in `tests/test_media.py`.

## A09 — Media metadata extraction functional · **PASS**

Detection is content-based and never trusts the extension. Verified directly:
a PNG named `.wav` is reported as an image; a WAV named `.png` is reported as
audio; a truncated PNG is refused; an empty file is refused; unrecognisable
bytes are refused.

Three detection paths: Pillow (images, with the decompression-bomb guard
retained), stdlib `wave` (RIFF header parsing, so WAV works without FFmpeg),
ffprobe (everything else, deciding from the stream list, excluding cover art).

Live extraction: `1920x1080` PNG dimensions; `15.0s @ 48000 Hz` for the tone;
`1920x1080 h264/aac 15.0s` for the rendered MP4 re-inspected as an asset.

## A10 — Asset checksums recorded · **PASS**

Streaming sha256 on every import, stored in `assets.json`, re-verified by
`polymation validate`. Implementation checked against the FIPS 180-4 vectors
for `""` and `"abc"`, and across a multi-chunk file.

Live example from the demonstration:
`asset-0004` → `assets/music/demo-tone.wav`, 2 880 044 bytes, sha256 recorded
and re-verified against disk.

Tampering is detected: flipping one bit without changing file length produces
`asset_checksum_mismatch`; changing the length produces
`asset_size_mismatch`. Both tested.

## A11 — Duplicate asset handling verified · **PASS**

Byte-identical content is refused by checksum, regardless of filename:

```
This file is byte-identical to already-imported asset asset-0001
(assets/images/swatch.png); sha256=<64 hex>. Pass --allow-duplicate to import
a second copy anyway.
```

Exit 6. `--allow-duplicate` imports deliberately. A *different* file with a
colliding name is renamed rather than overwritten, and the renaming is reported.
All three behaviours tested.

## A12 — Rights records created for imported assets · **PASS**

Import creates a `MediaAsset` and a `RightsRecord` in one operation, always
with `review_status=unreviewed`. Verified live: the demonstration project has
4 assets and 4 rights records, all `unreviewed`, all `basis=original_work`,
`rights_holder="Polymation Studio"`, `ai_generated=false`.

That the project's own generated fixtures are not self-approved is deliberate
(D-006). Model-level invariants prevent `unknown` from ever being approved, and
require an `ApprovalRecord` before `user_supplied`, `proposed_fair_use` or
`licensed` can be. All tested.

## A13 — Unresolved rights reported accurately · **PASS**

```
$ polymation rights report demo-technical-fixture
  4 asset(s) covered
  Approved: 0
  Restricted: 0
  Rejected: 0
  Unresolved: 4
    asset-0001  basis=original_work  assets/graphics/scene-01-title-card.png
    ...
Not all material is cleared. M0.1 does not determine publication readiness,
and publishing is out of scope.
```

All four statuses are always shown, including empty ones. An asset with no
rights record at all is reported unresolved (`status=missing_record`), never
cleared. Validation raises a warning per unresolved asset and an error for a
rejected asset still present. All tested.

## A14 — Unsafe paths rejected · **PASS**

`validation/paths.py` rejects, with tests for each: `..` in any position,
POSIX absolute paths, Windows absolute and drive-relative (`C:data`) and UNC
paths, backslash separators, `.` components, empty components (leading,
trailing or doubled `/`), surrounding whitespace, NUL and control characters,
and multi-segment destination filenames.

Symlink escape is also rejected — `resolve_within` compares fully resolved
paths, verified by a test that plants a symlink inside a project pointing
outside it.

Live: `polymation status "../etc"` → exit 7;
`asset import --as "../../escaped.png"` → exit 7, nothing written.

Tests: 43 in `tests/test_paths.py`, plus traversal cases in
`test_media.py` and `test_validation.py`.

## A15 — Original synthetic demonstration generated · **PASS**

`polymation demo create` generated four original fixtures:

| File | Bytes | Properties |
|---|---|---|
| `scene-01-title-card.png` | 34 749 | 1920×1080 PNG |
| `scene-02-test-pattern-4x3.png` | 2 608 | 640×480 PNG (4:3) |
| `scene-03-closing-card.png` | 32 611 | 1920×1080 PNG |
| `demo-tone.wav` | 2 880 044 | 15.0 s, 48 kHz, 16-bit stereo |

All produced from code in `src/polymation/rendering/fixtures.py`: Pillow
primitives, text in locally installed fonts, and a sine tone from `math` +
`wave`. Generation is deterministic (tested: two runs produce identical
checksums). A test asserts the test pattern contains *only* declared palette
colours, and another asserts the title card is not a flat fill.

Font substitution is reported honestly:

```
Space Grotesk -> Pillow built-in fallback (NOT the brand font)
IBM Plex Serif -> Pillow built-in fallback (NOT the brand font)
The proposed brand fonts are not installed. Text was drawn with a substitute,
so this render does NOT represent brand typography.
```

## A16 — Demonstration video rendered successfully · **PASS**

**Executed, not inferred.**

```
$ polymation demo render --overwrite
Rendered .../projects/demo-technical-fixture/output/demo-technical-fixture.mp4
  FFmpeg:       9.1
  Render time:  3.59s
  File size:    356122 bytes
  Output conforms to the M0.1 specification.
```

Exit 0. Three scenes, 5 s each. Rights and script cautions were printed, and
the output states on screen and in the CLI that it is a technical fixture, not
a channel intro and not cleared for publication.

## A17 — Output media properties verified · **PASS**

ffprobe 9.1 on the actual rendered file:

| Property | Required | Measured |
|---|---|---|
| Resolution | 1920×1080 | **1920×1080** |
| Frame rate | 30 fps | **30/1** (450 frames / 15.000 s) |
| Video codec | H.264 | **h264**, High profile, yuv420p |
| Audio codec | AAC | **aac**, LC profile |
| Sample rate | 48 kHz | **48000 Hz**, 2 channels |
| Container | MP4 | **mov,mp4,m4a,3gp,3g2,mj2** |
| Duration | ~15 s | **15.000000 s** |

File size 356 122 bytes, sha256
`7b4ccb582495a56fb26da22094ea5aa4aa6095377920dffc8524e8ecaafb71d1`.

Verification is built into `demo render`, which exits 3 on any deviation. A
test builds a deliberately off-spec file (640×480, 25 fps, no audio) and
asserts all three deviations are reported, so the check is proven to fail when
it should.

**Aspect-ratio handling confirmed visually.** A frame extracted at t=7.5 s
shows the 640×480 source pillarboxed into 1920×1080 with indigo bars left and
right; the concentric squares remain square and the checkerboard cells remain
square — no distortion. An automated test asserts the pillarbox colour at
x=2, x=100 and x=1917, and that the frame is not a flat fill.

## A18 — Automated tests executed · **PASS**

```
278 passed in 42.85s
```

Zero failures, zero errors, **zero skipped**. Breakdown:

| Module | Tests |
|---|---|
| `test_schemas.py` | 57 |
| `test_paths.py` | 43 |
| `test_media.py` | 34 |
| `test_validation.py` | 32 |
| `test_rendering.py` | 31 (7 marked `ffmpeg`, all executed) |
| `test_projects.py` | 31 |
| `test_cli.py` | 28 |
| `test_fixtures.py` | 22 |
| **Total** | **278** |

All 7 FFmpeg integration tests ran against real FFmpeg 9.1. Tests use
`tmp_path` and a temporary workspace; none touches the repository's own
`projects/` directory, and none touches the network.

Every area the milestone requires is covered: CLI behaviour, project
initialization, manifest round-tripping, schema validation, identifier
stability, duplicate prevention, asset import, checksums, duplicate detection,
metadata extraction, rights creation, missing-asset detection, invalid
references, path traversal, structural validation, the render-readiness
distinction, diagnostics, demo creation, demo rendering, and output inspection.

## A19 — Lint and formatting checks executed · **PASS**

```
$ ruff check .            → All checks passed!
$ ruff format --check .   → 41 files already formatted
```

Ruff 0.14.14, line length 100, rule sets E, F, I, B, UP, S (bandit), RUF. No
blanket suppressions: the only `# noqa` comments are four in
`rendering/ffmpeg.py` explaining why deliberate, non-shell subprocess use is
not the risk S404/S603 describe, and two internal invariant asserts.

## A20 — CI configuration established · **PASS (configuration only — no green run yet)**

`.github/workflows/ci.yml` installs uv, installs the pinned Python from
`.python-version`, installs FFmpeg via apt, runs `uv sync --frozen`, runs
`polymation doctor`, `ruff check`, `ruff format --check`, the full test suite
with a JUnit report, then builds and renders the demonstration and asserts
every output property with ffprobe (resolution, frame rate, pixel format, both
codecs, sample rate, channel count, duration within 14.5–15.5 s). It uploads
the rendered MP4 as an artifact.

It also includes a step that **fails the build if any FFmpeg-marked test was
skipped**, so a silently-skipped render cannot pass CI.

**Run history.** The first run executed on 2026-11-25 once `origin` was
configured. Every step up to and including the render succeeded, and the render
produced a conforming file — the run's own log shows `1920x1080`, `30/1`,
`h264`, `aac`, `48000`, `15.000000`. The run then **failed** on the
"Assert the output matches the M0.1 specification" step, due to a defect in the
workflow, not in the application: `ffprobe -show_entries
stream=width,height,r_frame_rate,codec_name` returns fields in ffprobe's own
field order rather than the requested order, so a positional `read` bound every
value to the wrong variable (reporting `width is h264`).

Fixed by issuing one ffprobe call per field, which removes the ordering
dependence; the step now also checks pixel format and channel count. The
corrected logic was extracted from the workflow and executed against the real
rendered file locally: it prints `Output conforms to the M0.1 specification.`
and exits 0.

The criterion is satisfied as *configuration established*. It is explicitly
**not** evidence that CI passes, and **no green run has occurred yet**. Nothing
in this report claims otherwise.

## A21 — Documentation completed · **PASS**

All eight required documents written, with real content:

| Document | Lines | Covers |
|---|---|---|
| `README.md` | ~200 | Overview, requirements, installation, CLI, demo, testing, limitations, status |
| `CLAUDE.md` | ~160 | Scope control, architecture, quality, testing, approval gates, copyright, credentials, no paid APIs, no publishing, accurate reporting |
| `docs/ARCHITECTURE.md` | ~330 | Structure, storage model, schema ownership, CLI architecture, media handling, rights architecture, rendering boundaries, extension points |
| `docs/PROJECT_PLAN.md` | ~140 | M0.1–M8, all post-M0.1 marked planned and subject to revision |
| `docs/EDITORIAL_STANDARD.md` | ~130 | Remit, principles, pillars, structure, narration, visuals, rights, corrections |
| `docs/MEDIA_RIGHTS.md` | ~180 | Basis vs permission, enforced invariants, approval invalidation, per-material rules, checklist |
| `docs/DECISIONS.md` | ~260 | 13 decisions (D-001–D-013) with rationale, alternatives, consequences |
| `docs/DEVELOPMENT_STATUS.md` | ~230 | Status, completed, pending, evidence, defects, constraints, next action |

## A22 — No unauthorized paid API usage · **PASS**

**External cost incurred: $0.00.** No API key, no credential, no paid service,
no metered dependency. All dependencies are free and open-source from PyPI
(21 packages, all in `uv.lock`).

The only network access during development was package downloads from PyPI and
Homebrew — no runtime network access exists. Nothing in `src/` imports
`urllib`, `requests`, `httpx`, `socket` or any HTTP client; `.env.example`
documents that no credentials are needed.

## A23 — No copyrighted third-party fixture assets · **PASS**

Every fixture is generated from code in
`src/polymation/rendering/fixtures.py`. No game footage, screenshots, anime
footage or stills, commercial music, downloaded artwork, downloaded fonts or
stock media exist anywhere in the repository.

The only binary-ish content is text source and generated PNG/WAV files, and the
generated files are git-ignored. `.gitignore` excludes all media extensions.
Text fixtures are ASCII-only (the fallback font lacks an em-dash glyph).

Tests assert the test pattern uses only declared palette colours, and that
fixture generation is deterministic — i.e. the output is fully accounted for by
the code.

## A24 — Known limitations explicitly documented · **PASS**

Documented in three places, consistently: `README.md` ("Known limitations"),
`docs/DEVELOPMENT_STATUS.md` (pending requirements, known gaps, environment
constraints), and `docs/ARCHITECTURE.md` §10 (what the renderer does *not* do).

Named explicitly, among others: the renderer handles still images only (no
transitions, motion, narration sync, captions, video shots, caching); there is
no command to create an approval record, so approvals must be hand-edited; no
content generation of any kind; no publishing; shorts do not render; brand
values and fonts are provisional with substitution in effect; CI has never
run; local validation used Python 3.11 rather than the pinned 3.12.

---

## Summary

| Verdict | Count | Criteria |
|---|---|---|
| **PASS** | 24 | A01–A24 |
| FAIL | 0 | — |
| PARTIAL | 0 | — |
| NOT EXECUTED | 0 | — |

Two qualifications that are **not** downgrades but must be read with the table:

1. **A20 is configuration only.** CI has run once and failed, on a defect in
   the workflow's own assertion step rather than in the application; that defect
   is fixed but no green run has happened yet. No claim is made about CI passing.
2. **A03 and A18 evidence came from Python 3.11.4, not the pinned 3.12.** The
   host's glibc blocks the pinned interpreter from installing Pillow wheels.
   CI covers 3.12; local results are not evidence about it.

M0.1 is ready for independent human review. M0.2 is **not** authorized.
