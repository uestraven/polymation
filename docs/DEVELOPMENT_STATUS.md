# Development status

**Last updated:** 2026-11-25
**Current milestone:** M0.1 — Project Foundation & Pilot Production Workspace
**State:** complete and ready for independent human review
**Next authorized action:** human review of M0.1. **M0.2 is not authorized.**

---

## 1. What is implemented

### Foundation
- Git repository on `main`, Python package `polymation` under `src/`, hatchling
  build, `polymation` console script.
- `uv.lock` with 21 packages, each pinned by sha256. `pip`-based fallback also
  supported; `scripts/bootstrap.sh` handles either.
- Ruff lint + format configuration; pytest configuration with an `ffmpeg` marker.
- GitHub Actions workflow — **configured; one run so far, which failed on a
  defect in the workflow itself, now fixed** (see §4, G-1).

### Channel configuration
- `channels/polymation/brand.yaml` — palette, tagline, typography direction,
  proposed fonts, `provisional: true`.
- `channels/polymation/editorial.yaml` — historical scope, subjects, duration
  bands, content pillars, editorial principles.
- Loaded and validated through Pydantic. No channel-specific colour, font or
  duration is hardcoded in source.

### Data contracts
Six versioned documents, all `extra="forbid"`, all with `schema_version` checked
before the body is parsed: `VideoProject`, `ScriptDocument`, `Storyboard`,
`AssetCollection`, `RightsLedger`, `ApprovalLedger`, plus their nested models.
Narration scenes and visual shots are separate concepts throughout.

### Projects
- `polymation new` creates 7 documents and 10 subdirectories.
- Identifiers are assigned once and never re-derived from the title.
- Existing projects are never overwritten; atomic writes via temp + `os.replace`.
- `polymation list`, `polymation status`.

### Media
- Content-based detection: Pillow → stdlib `wave` → ffprobe. Extensions are
  never consulted.
- Import copies into the project, files by detected type, never overwrites,
  refuses duplicate content by sha256, re-hashes the copy against the source.
- Every import creates an `unreviewed` rights record.

### Rights
- `RightsBasis` (provenance) and `ReviewStatus` (human decision) are separate.
- Model-enforced: `unknown` can never be approved; `user_supplied`,
  `proposed_fair_use` and `licensed` need an attached `ApprovalRecord`;
  `licensed` needs a licence identifier; attribution requires attribution text.
- `polymation rights report` with all four statuses and orphan-record detection.

### Validation
- 14 error codes, 8 warning codes, errors and warnings separated.
- Structural validity, render readiness and publication readiness are three
  distinct reported states. Publication readiness is `null` by design.
- Path safety: traversal, absolute (POSIX and Windows), drive-relative, UNC,
  `.`/empty components, control characters, and symlink escape all rejected.

### Rendering
- Original fixture generation (three images, one audio tone), deterministic.
- FFmpeg composition of still-image shots plus one audio bed to
  1920×1080 / 30 fps / H.264 / AAC 48 kHz / MP4.
- ffprobe verification built into `demo render`, which exits 3 on any deviation.
- All FFmpeg calls use explicit argument vectors; `shell=True` appears nowhere.

---

## 2. Validation evidence

**Local only.** Amazon Linux 2, Python 3.11.4, FFmpeg 9.1, uv 0.13.10.

| Check | Command | Result |
|---|---|---|
| Tests | `uv run pytest -q` | **278 passed, 0 failed, 0 skipped**, 42.85 s |
| FFmpeg integration | `uv run pytest -m ffmpeg` | **7 selected, 7 executed** |
| Lint | `uv run ruff check .` | **All checks passed** |
| Format | `uv run ruff format --check .` | **41 files already formatted** |
| Diagnostics | `uv run polymation doctor` | **exit 0**, all required deps present |
| Demo build | `polymation demo create --recreate` | **exit 0**, 4 fixtures, 3 scenes, 15.0 s |
| Demo render | `polymation demo render --overwrite` | **exit 0**, conforms to spec |

Rendered output, measured by ffprobe 9.1:

```
path        projects/demo-technical-fixture/output/demo-technical-fixture.mp4
resolution  1920x1080
frame rate  30/1  (450 frames / 15.000000 s)
video       h264, High profile, yuv420p
audio       aac, LC profile, 48000 Hz, 2 channels
container   mov,mp4,m4a,3gp,3g2,mj2
duration    15.000000 s
size        356122 bytes
sha256      7b4ccb582495a56fb26da22094ea5aa4aa6095377920dffc8524e8ecaafb71d1
render time 3.59 s
```

Aspect-ratio handling was confirmed by extracting a frame at t=7.5 s: the
640×480 source is pillarboxed into 1920×1080 with indigo bars left and right,
and its squares remain square. An automated test asserts the same.

Per-criterion evidence: `docs/ACCEPTANCE_CRITERIA.md` (24/24 PASS).

---

## 3. Pending requirements

Nothing required by M0.1 is outstanding. Everything below is **out of scope for
M0.1** and listed so the boundary is unambiguous.

### Explicitly out of scope, and genuinely absent
Automated historical research · LLM integrations · AI-generated scripts ·
AI-generated images · text-to-speech · automated stock media acquisition ·
anime episode downloading · gameplay recording automation · automated copyright
determinations · production-ready visual templates · advanced timeline editing ·
scene-level render caching · shorts rendering · YouTube API · publishing ·
analytics · database infrastructure · graphical interfaces · cloud deployment ·
multi-channel orchestration.

None of these is stubbed or faked. Where a data contract exists that a future
milestone will use (`FactualClaim`, `AssetCategory.NARRATION`,
`RightsRecord.ai_generated`), it was required by M0.1 in its own right, and no
generator or integration sits behind it.

---

## 4. Known gaps and defects

No known defects — no test fails and no command misbehaves. The following are
**real gaps**, not deferred polish, and should be read as limitations of what
was delivered.

### G-1 — CI has not completed successfully yet
`origin` is configured (`github.com/uestraven/polymation`) and the workflow has
run once, on 2026-11-25. Every step up to and including the render succeeded,
and the render produced a conforming file — the run log shows `1920x1080`,
`30/1`, `h264`, `aac`, `48000`, `15.000000`.

The run then **failed** on the "Assert the output matches the M0.1
specification" step, from a defect in the workflow rather than in the
application: `ffprobe -show_entries stream=width,height,r_frame_rate,codec_name`
returns fields in ffprobe's own field order, not the requested order, so a
positional `read` bound each value to the wrong variable and the step reported
`width is h264`.

Fixed by issuing one ffprobe call per field, eliminating the ordering
dependence; the step now also asserts pixel format and channel count. The
corrected logic was extracted from the workflow and run against the real
rendered file locally — it reports conformance and exits 0.

**No green CI run has occurred.** Nothing in this repository's documentation
claims CI passes, and the workflow stays unverified until a run completes
successfully.

### G-2 — Local validation used Python 3.11, not the pinned 3.12
`.python-version` pins 3.12 and CI installs it, but all local evidence came
from Python 3.11.4. This host is Amazon Linux 2 with glibc 2.26; published
Pillow wheels require `manylinux_2_28`, so uv's standalone 3.12 finds no
compatible wheel and the source build fails for lack of libjpeg headers. The
Linuxbrew interpreters work because Homebrew ships its own newer glibc.

Workaround in use locally:

```bash
export UV_PYTHON=/home/linuxbrew/.linuxbrew/bin/python3.11
uv sync --frozen --extra dev
```

`polymation doctor` reports the mismatch explicitly. **Local results are not
evidence about 3.12.** Recorded as D-011.

### G-3 — No command creates an approval record
The platform stores, verifies and invalidates `ApprovalRecord`s, but provides
no CLI command to create one. Approving an asset means hand-editing
`rights.json` and `approvals.json`. This was left as a gap rather than filled
with a convenient auto-approve path, which would have been the wrong thing to
build first (D-006). It is the most likely candidate for the first
owner-requested addition.

### G-4 — Brand fonts are not installed, so all text is substituted
Space Grotesk, IBM Plex Serif and Silkscreen are *proposed*, not acquired. None
is present on this host, so every rendered glyph comes from Pillow's built-in
face. The tooling says so loudly in `demo create`, `polymation fonts` and
`FontResolution.description`. **The demonstration does not represent brand
typography.** Acquiring properly licensed fonts is an owner decision (D-009).

### G-5 — Brand values are provisional
The palette, tagline and typography in `channels/polymation/brand.yaml` are a
working starting point, flagged `provisional: true` and surfaced as a warning.
They are not approved production artwork.

### G-6 — The renderer is a proof of concept
Still-image shots plus one audio bed, concatenated. No transitions, no motion,
no narration synchronisation, no captions, no video-source shots (a video asset
in a shot raises a clear error), no multi-track mixing, no render caching, no
shorts rendering. All M0.2 and later.

### G-7 — Multi-document writes are not atomic as a set
Each document is written atomically, but `ProjectBundle.save()` writes six files
in sequence. A crash between two writes can leave documents inconsistent. The
validator detects this (`document_project_mismatch`, dangling references), but
there is no transaction or rollback. Acceptable for a single-operator local
tool; worth revisiting if concurrent use ever becomes real.

### G-8 — No concurrency protection
Two simultaneous commands against the same project can interleave writes. There
is no lock file. Not a problem for the intended single-operator use, and not
solved.

### G-9 — Demo cleanup is scoped but blunt
`demo create --recreate` deletes the demo project tree. It is hard-scoped to the
fixed identifier `demo-technical-fixture` so no operator project can be
destroyed, but it is still an unconditional `rmtree` of that one directory.

### G-10 — Checksum verification cost is unbounded
`polymation validate` re-hashes every asset by default. Correct, but
proportional to total media size; with hours of footage this will be slow.
`--no-verify-checksums` exists as an escape hatch.

---

## 5. Environment constraints

| Constraint | Status |
|---|---|
| Python 3.12 unavailable from system/brew | Resolved via uv-managed 3.12 for CI; local uses brew 3.11 (G-2) |
| FFmpeg absent | **Resolved** — FFmpeg 9.1 installed user-local via Linuxbrew with owner approval |
| uv absent | **Resolved** — uv 0.13.10 installed user-local via pipx with owner approval |
| `curl \| sh` installers blocked by the agent sandbox | Worked around with pipx |
| glibc 2.26 vs `manylinux_2_28` wheels | Documented, not resolved (G-2) |
| No GitHub remote | **Resolved** — `origin` added on owner instruction; not pushed by the agent (G-1) |
| Brand fonts not installed | Documented and reported at runtime (G-4) |

Installs performed, both user-local and without sudo, each with the owner's
explicit approval given during this milestone:

```
brew install ffmpeg      → /home/linuxbrew/.linuxbrew/bin/{ffmpeg,ffprobe} 9.1
pipx install uv          → ~/.local/bin/uv 0.13.10
uv python install        → uv-managed CPython 3.12.16
```

No privileged system modification was made. No system package manager (`yum`)
was used.

---

## 6. Financial usage

**$0.00.** No paid API, no credential, no metered service. All dependencies are
free and open-source. No runtime network access exists anywhere in `src/`.

---

## 7. Next authorized action

**Human review of M0.1.** Specifically, the owner's attention is wanted on:

1. The three-state readiness model (structural / render / publication) — is the
   distinction drawn where it should be?
2. The rights model, particularly that the project's own generated fixtures are
   not self-approved, and that no command creates approvals (G-3).
3. Whether to acquire licensed brand fonts, or choose families that are reliably
   available (G-4), before M0.3.
4. Re-running CI now that the workflow's assertion step is fixed, to obtain
   the first green run (G-1).

M0.2 must not be started until that review is complete and authorization is
given.
