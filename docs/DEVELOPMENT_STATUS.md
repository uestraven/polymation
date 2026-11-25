# Development status

**Last updated:** 2026-11-25
**Current milestone:** M0.2 — Core Video Composition & Rendering Engine
**State:** complete and ready for independent human review
**Next authorized action:** human review of M0.2. **M0.3 is not authorized.**

M0.1's status is preserved below for continuity; its gaps are marked where M0.2
closed them.

---

## 1. What is implemented

### M0.2 — composition engine

**Timeline.** `CompositionTimeline`, a separate versioned document from the
storyboard: the storyboard records creative intent, the timeline records
executable placement. Integer frames authoritative for video, integer samples
for audio; at 30 fps / 48 kHz the grids align exactly at 1600 samples per frame,
and an unaligned profile is refused rather than rounded per frame. Every
rounding decision made during compilation is reported, not hidden. Optional:
projects without one (every M0.1 project) load unchanged.

**Composition.** Multi-shot scenes that must tile without gaps. Three
non-distorting fit modes (`contain`, `cover`, explicit `crop`) plus two scaling
kernels, with nearest-neighbour for pixel-art material. Video sources with
trimming, frame-rate and resolution normalisation, and muted source audio by
default. Deterministic pan and zoom over stills, constrained so motion cannot
expose an undefined edge. Seven overlay primitives with nine-point safe-area
anchors and offsets that scale between profiles.

**Text.** Rendered to pixels by Pillow and composited as an image; FFmpeg never
receives text. Wrapping, mid-word breaking for unspaced runs, per-line
alignment, padded background boxes, and a line budget that truncates with an
ellipsis and says it did.

**Audio.** Four independent buses. Per-segment placement, trimming, gain, fades
and explicitly-permitted silence padding. Music ducking as deterministic
automation from the narration's known sample spans. Two-pass EBU R128 loudness
with a true-peak limiter. Per-role stem export.

**Captions.** Externally supplied SRT: import, timing validation, sidecar
export, optional burn-in through the same safe text path.

**Transitions.** Cuts, fades from and to black (neither changes duration), and
within-scene crossfades (overlap accounted for in the spans).

**Profiles.** Preview 960×540 and final 1920×1080, both 30 fps H.264/AAC MP4
with square pixels, identical composition timing, separate caches and outputs.

**Caching.** Content-addressed per-scene and per-assembly caches keyed on
content — asset checksums, font file checksums, brand values, encoder settings,
renderer version, FFmpeg build — and explicitly **not** on timestamps. Entries
validated before reuse and written atomically.

**Integrity.** Advisory `O_EXCL` project locks with evidence-based stale-lock
recovery. Write-ahead journalled multi-document writes with roll-forward
recovery on load. A failed render never replaces a valid output.

**CLI.** `timeline compile|validate`, `render --preview|--final` with
`--scene`/`--force`/`--overwrite`, `cache status|clear`, `inspect`,
`reference build`.

**Reference fixture.** An original 5400-frame (180.000 s) engineering benchmark
with nine A/V synchronisation markers, plus `scripts/benchmark.py`.

### M0.1 — foundation (unchanged)

Project scaffolding and stable identifiers; six versioned document types;
content-based media detection and import with checksums and provenance; a
rights model that refuses to self-approve; validation separating errors from
warnings and structural from render readiness; the 15-second demonstration.

## 2. Validation evidence

**Local only, in two tiers reported separately.**

| Tier | Interpreter | FFmpeg | Command |
|---|---|---|---|
| Host | Python 3.11.4 (Linuxbrew) | 9.1 | `scripts/verify.sh` |
| Pinned | Python 3.12.16 (container) | 7.1.5 | `scripts/verify-python312.sh` |

| Check | Result |
|---|---|
| Tests | **650 passed, 0 failed, 0 errors, 0 skipped** — 144.5 s |
| Real-FFmpeg integration tests | **41 executed**, none skipped |
| Lint | `ruff check .` → All checks passed |
| Format | `ruff format --check .` → 74 files already formatted |
| Diagnostics | `polymation doctor` → exit 0 |
| Lockfile | `uv sync --frozen` verified |

### Reference fixture, final profile

```
frames decoded   5400  (expected 5400)
duration         180.000000 s
resolution       1920x1080 @ 30/1
video            h264 High yuv420p
audio            aac 48000 Hz 2 ch
file size        20 656 186 bytes
conforms         True  (no deviations)
loudness         -16.0 LUFS integrated, -7.1 dBTP true peak, 3.4 LU range
```

### Benchmarks

| Measurement | Value |
|---|---|
| Fixture build (15 original assets) | 23.1 s |
| Cold preview render | 53.6 s |
| Cold final render | 148.9 s |
| **Warm final, unchanged** | **1.2 s** |
| Final after editing one scene | 76.3 s — **1 rebuilt, 5 reused** |
| Peak RSS (this process only) | 402.7 MiB |

### A/V synchronisation

Nine markers at 0.2, 26.2, 42.0, 58.2, 75.0, 92.2, 122.2, 138.0 and 154.2 s.
**9/9 visual detected, 9/9 audio detected, 0 discrepancies.**

Per-criterion evidence: [`ACCEPTANCE_CRITERIA_M02.md`](ACCEPTANCE_CRITERIA_M02.md)
(34 PASS, 1 PARTIAL) and [`ACCEPTANCE_CRITERIA.md`](ACCEPTANCE_CRITERIA.md) for
M0.1.

## 3. Pending requirements

Nothing required by M0.2 is outstanding. Everything below is **out of scope** and
listed so the boundary is unambiguous.

Automated topic research · LLM integration · script generation · AI narration
and text-to-speech · AI image generation · AI-generated motion video ·
automatic stock media acquisition · real anime episode acquisition · automatic
speech transcription · final Polymation branding · final thumbnail templates ·
shorts rendering · publishing · YouTube API · analytics · web interfaces ·
distributed rendering · database infrastructure.

None is stubbed or faked. Where a data contract exists that a later milestone
will use (`FactualClaim`, `AudioRole.NARRATION`, `RightsRecord.ai_generated`,
`ApprovalStage.PUBLICATION`), it was required by M0.1 or M0.2 in its own right
and nothing sits behind it.

## 4. Known gaps and defects

**No known defects.** No test fails and no command misbehaves. Everything below
is a real limitation of what was delivered, not deferred polish.

### Closed since M0.1

- **G-2 (pinned-Python validation) — RESOLVED.** The suite now runs under the
  pinned Python 3.12 in a container with the host tree mounted read-only. No
  privileged host modification was needed (D-014). Residual: the host tier still
  runs 3.11.4 and a different FFmpeg build, so encoded bytes differ between
  tiers; the output *specification* is what is asserted, and byte reproducibility
  holds for a fixed FFmpeg build.
- **G-6 (renderer was a proof of concept) — CLOSED by M0.2**, within the
  boundaries below.
- **G-7 (multi-document writes not atomic as a set) — CLOSED** by the
  write-ahead journal, for every write that goes through `ProjectBundle.save()`.
- **G-8 (no concurrency protection) — CLOSED** by advisory project locks, within
  the limits stated in M-6 below.

### Composition boundaries

**M-1 — No cross-scene crossfades.** Scene-to-scene transitions are a cut or a
fade through black. A cross-scene crossfade would make scene *N*'s output depend
on *N−1*'s, so editing one scene would invalidate its neighbour and per-scene
caching would no longer hold. Both the schema and the validator reject it. The
milestone explicitly permits deferring this; see D-018.

**M-2 — No retiming.** Shots render at 1:1 speed: no slow motion, no speed
ramps. `SourceTrim` carries only a start offset, deliberately, because modelling
a separate source duration would imply support that does not exist.

**M-3 — No motion on video sources.** Pan and zoom apply to stills only;
applying motion to a video asset is a validation error.

**M-4 — No vertical shorts rendering.** Out of scope. The `short` format remains
a valid manifest value with nothing that renders it.

**M-5 — No CJK glyph coverage.** Text is rendered to pixels by Pillow, which is
what makes arbitrary text safe (D-016) — but no font installed on this host
covers CJK, so Japanese text renders as tofu boxes. **This is a real limitation
for a channel about Japanese media.** Safety is unaffected. Installing a CJK
font (for example Noto Sans JP) fixes it with no code change, and automatically
invalidates every cached scene because the font checksum is part of the cache
key. Acquiring a licensed font is an owner decision, as with the brand fonts.

### Operational limits

**M-6 — Locking is advisory and local.** An `O_EXCL` lock file prevents the
realistic accident — two commands in two terminals on one machine. It does
**not** coordinate across NFS or between hosts, and does not claim to. A stale
lock is broken on evidence (holder pid gone on this host, or past a 3-hour age
limit) and the break is reported.

**M-7 — Superseded cache entries are not garbage-collected.** Editing a scene
leaves its previous entry on disk; after the reference fixture's benchmark the
final scene cache held 7 entries for 6 scenes. `cache status` reports total
bytes so this is visible, and `cache clear` removes them. No automatic eviction
or size cap exists.

**M-8 — Caption burn-in forces a full re-encode.** Captions change pixels, so
the stream-copy assembly path cannot be used. The assembly cache (D-019) makes
this free on a warm render, but a cold render with captions is materially slower
than one without.

**M-9 — Peak memory is only partly measured.** `scripts/benchmark.py` reports
this process's maximum RSS, which **excludes the FFmpeg child processes** that
do the encoding. Treat the figure as a floor, not the true peak.

**M-10 — No green CI run.** The workflow is configured and extended for M0.2,
and the new cache gate's logic was verified locally in both directions, but the
M0.2 steps have never executed remotely. Nothing in this repository claims CI
passes.

### Carried forward from M0.1

**M-11 — No command creates an approval record.** The platform stores, verifies
and invalidates `ApprovalRecord`s, but approving an asset still means
hand-editing `rights.json` and `approvals.json`. Left as a gap rather than
filled with a convenient auto-approve path. Still the likeliest first
owner-requested addition.

**M-12 — Brand values and fonts are provisional.** `channels/polymation/` is
flagged `provisional: true`, and overlay text uses a substitute typeface which
the tooling reports. Final typography is M0.3.

**M-13 — No cross-project index.** `polymation list` reads every manifest from
disk. Fine at this scale.

**M-14 — Checksum verification cost is unbounded**, being proportional to total
media size. `--no-verify-checksums` is the escape hatch.

**M-15 — Downgrading is not supported.** An M0.1 project loads under M0.2
unchanged, but a manifest written by M0.2 carries keys M0.1's `extra="forbid"`
would reject (D-015).

## 5. Environment constraints

| Constraint | Status |
|---|---|
| Python 3.12 not installable on this host | **Worked around** — validated under the pin in a container; host tier runs 3.11.4 (D-014) |
| glibc 2.26 vs `manylinux_2_28` wheels | **Worked around** — the container supplies glibc 2.41 |
| FFmpeg | **Resolved in M0.1** — 9.1 via Linuxbrew, user-local |
| uv | **Resolved in M0.1** — 0.13.10 via pipx, user-local |
| Docker | Available, daemon reachable; used for the pinned-Python tier only |
| No CJK font installed | **Documented, not resolved** (M-5) |
| Brand fonts not installed | **Documented**, reported at runtime (M-12) |
| `/usr/bin/time` absent | Benchmarks use `resource.getrusage` instead |
| No green CI run | **Documented** (M-10) |

Development installs performed, all user-local and without sudo, each with the
owner's explicit approval:

```
brew install ffmpeg      -> /home/linuxbrew/.linuxbrew/bin/{ffmpeg,ffprobe} 9.1
pipx install uv          -> ~/.local/bin/uv 0.13.10
uv python install        -> uv-managed CPython 3.12.16
docker pull python:3.12-slim   (verification container base)
```

No privileged system modification was made.

Local development override on this host, because the pinned interpreter cannot
install Pillow here:

```bash
export UV_PYTHON=/home/linuxbrew/.linuxbrew/bin/python3.11
uv sync --frozen --extra dev
```

## 6. Financial usage

**$0.00.** No paid API, no credential, no metered service. M0.2 added **no new
runtime dependency**: the same 21 free open-source packages as M0.1, all pinned
in `uv.lock`.

No runtime network access exists anywhere in `src/` — nothing imports `urllib`,
`requests`, `httpx` or `socket`, except `socket.gethostname()` in the lock file,
which makes no connection. The test suite requires no network.

---

## 7. Next authorized action

**Human review of M0.2.** The owner's attention is most useful on:

1. **The reference fixture as a visual artefact.** It is technically correct —
   frame-exact, conforming, synchronised — but whether the composition *reads*
   well is an editorial judgement no test can make. Technical tests passing is
   not visual or editorial approval.
2. **CJK glyph coverage (M-5).** A channel about Japanese media that cannot
   render Japanese text is a real problem. Installing a licensed CJK font is an
   owner decision, and it interacts with the M0.3 typography decision.
3. **Cross-scene transitions (M-1).** Deferred on cache-isolation grounds. If
   scene-to-scene crossfades matter editorially, that changes the caching design
   and should be decided before M0.3 builds templates on top.
4. **Approval records (M-11).** Still hand-edited. Worth deciding whether M0.3
   should add the command, since M0.4's pilot will need it.
5. **Whether to obtain a green CI run (M-10)** by pushing the M0.2 work to the
   configured remote.

**M0.3 must not be started until that review is complete and authorization is
given.** No final branding decisions have been made, and no Polymation episode
has been produced or published.
