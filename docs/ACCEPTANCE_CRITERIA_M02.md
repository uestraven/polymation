# M0.2 acceptance criteria

**Evaluated:** 2026-11-25
**Verdict:** 34 PASS, 1 PARTIAL (B32, documentation — CI has no green run yet), 0 FAIL, 0 NOT EXECUTED.

M0.1's matrix is in [`ACCEPTANCE_CRITERIA.md`](ACCEPTANCE_CRITERIA.md) and is
unchanged except A03/A18, which the baseline closure checkpoint improved (see
B01, B02).

**Caveat applying to everything below:** this is **local** validation, in two
tiers reported separately. The GitHub Actions workflow has run once and failed
on a defect in its own assertion step (since fixed); the M0.2 steps have never
executed remotely. No claim is made that CI passes.

| Tier | Interpreter | FFmpeg | How |
|---|---|---|---|
| **Host** | Python 3.11.4 (Linuxbrew) | 9.1 | `scripts/verify.sh` |
| **Pinned** | Python 3.12.16 (container) | 7.1.5 | `scripts/verify-python312.sh` |

Benchmark host: Amazon Linux 2, Linux 5.10.273 x86_64, Intel Xeon Platinum
8252C @ 3.80 GHz, 8 vCPU, 30.6 GiB RAM, FFmpeg 9.1 (libx264 + native AAC),
uv 0.13.10.

---

## B01 — M0.1 baseline verification succeeds · **PASS**

Executed before any M0.2 code was written.

| Check | Result |
|---|---|
| Repository state | branch `main`, clean tree, 6 commits, baseline `a80567a` present |
| `uv sync --frozen` | 20 packages verified against `uv.lock` |
| `polymation doctor` | exit 0 |
| `ruff check` / `ruff format --check` | clean, 41 files |
| Tests | **278 passed**, 0 failed, 0 skipped |
| FFmpeg integration subset | **7 selected, 7 executed** |
| M0.1 demonstration | re-rendered, **sha256 `7b4ccb58…` byte-identical** to the M0.1 report, 1920×1080 / 30 fps / h264 / aac 48 kHz / 15.000 s, conforms |

One discrepancy found and corrected against the stated baseline: HEAD was
`723bbb4`, one commit ahead of `a80567a`, carrying the CI assertion fix made
after the M0.1 report. Reported rather than ignored.

The M0.1 suite still passes unchanged within the 650-test M0.2 suite (B29).

## B02 — Python environment compatibility resolved · **PASS**

M0.1 closed with A03 classified PARTIAL by the independent review, because all
local evidence came from Python 3.11.4 rather than the pinned 3.12. That is now
resolved.

Cause, confirmed: this host is Amazon Linux 2 with **glibc 2.26**, while
published Pillow wheels target `manylinux_2_28`. uv's standalone CPython 3.12
links the system glibc, finds no compatible wheel, and its source build fails
for lack of libjpeg headers. The Linuxbrew interpreters work because Homebrew
ships its own glibc (2.39).

Resolution: `scripts/verify-python312.sh` + `docker/verify.Dockerfile` run the
whole suite under the pinned interpreter in a disposable container, with the
host tree mounted **read-only**. Docker was already installed with a reachable
daemon, so **no privileged host modification was needed** — no sudo, no system
packages, no change to the host interpreter set.

Verified under Python **3.12.16** / FFmpeg 7.1.5 / glibc 2.41 (Debian 13):
`uv sync --frozen` from the lockfile, doctor, ruff lint and format, **278 tests
passed with 0 skipped** at the time of the checkpoint, demonstration rendered
and independently confirmed.

The entrypoint compares the running interpreter against `.python-version` and
fails if they differ, so the check cannot degrade into "some Python passed".
Containers are a **development** dependency only; the application runs directly
on any host with Python 3.11+ and FFmpeg (D-014).

The Python requirement was **not** silently changed: `.python-version` still
pins 3.12 and `requires-python` still reads `>=3.11`.

## B03 — Versioned timeline schema implemented · **PASS**

`CompositionTimeline` in `schemas/timeline.py`, its own document with its own
`schema_version`, carrying: project identifier, output profile name, fps,
resolution, audio sample rate, safe-area inset, ordered scenes, visual shots,
overlays, narration and music segments, caption settings, transitions, and
explicit integer timing throughout.

Supporting contracts: `TimelineScene`, `CompositionShot`, `Overlay`,
`TextStyle`, `Motion`, `CropRect`, `Transition`, `SourceTrim`, `AudioMix`,
`AudioSegment`, `Ducking`, `LoudnessTarget`, `CaptionSettings`,
`CaptionPlacement`, plus `FrameSpan`/`SampleSpan` in `schemas/timing.py`.

Invalid, negative and ambiguous ranges are rejected: a zero-length span, a
negative start, a span past the timeline limit, shots that do not tile their
scene, an overlay outside its span, audio past the end of the video, and
overlapping narration. **70 tests** in `test_timeline_schema.py`.

## B04 — M0.1 project compatibility preserved · **PASS**

`timeline.json` is a **new optional document**; `ManifestPaths` gained
defaulted `timeline` and `cache_dir` fields. Every `ManifestPaths` field already
had a default, which is what makes the addition backward compatible (D-015).

Tested directly:

- `test_a_project_without_a_timeline_still_loads` — the M0.1 state loads, with
  `bundle.timeline is None`.
- `test_an_m01_manifest_without_the_new_path_fields_loads` — strips `timeline`
  and `cache_dir` from a manifest's `paths`, reloads, and asserts the defaults
  are picked up.
- `test_an_m01_project_can_be_compiled_and_rendered_into` — the upgrade path: an
  M0.1 project gains a timeline with no migration step.

No migration was written, because a defaulted additive field needs none.
Downgrading is **not** supported and is documented as such.

## B05 — Deterministic frame timing · **PASS**

Integer frames are authoritative for video, integer samples for audio. At
30 fps / 48 kHz the grids align exactly at **1600 samples per frame**;
`frames_to_samples` refuses to convert an unaligned profile rather than rounding
per frame.

Measured on the 180-second reference fixture: **5400 frames expected, 5400
frames decoded** (`ffprobe -count_frames`), duration exactly 180.0 s.

Tests: **25** in `test_timing.py`, including a no-drift check that
`sum(per-scene samples) == whole-timeline samples`, and
`test_the_output_frame_count_matches_the_timeline_exactly`, which renders
37 + 23 frames and decodes exactly 60.

Rounding is reported, never hidden: `seconds_to_frames` returns a
`RoundingApplied` record and `timeline compile` prints every one.

## B06 — Multi-shot scene composition · **PASS**

A scene holds any number of shots, which must tile it without gaps. The
reference fixture has **11 shots across 6 scenes**, with scenes of 1, 3, 3, 1, 2
and 1 shots.

Verified end to end by `test_mixed_stills_and_video_render_together` (15-frame
still + 30-frame video → exactly 45 decoded frames) and by the fixture's
contact sheet.

## B07 — Actual video source rendering · **PASS**

Real video sources render, not only stills. Frame rate is normalised by the
`fps` filter; resolution and aspect by the fit chain.

Generated fixtures cover the required combinations: 640×480 at 24 fps without
audio, 1280×720 at 25 fps with audio, 480×360 at 15 fps. All original, encoded
from Pillow-drawn frames.

- `test_a_video_source_renders_with_frame_rate_normalisation` — a 24 fps source
  yields exactly 30 output frames.
- `test_a_source_trim_selects_a_later_part_of_the_clip` — the synthetic clip's
  moving bar encodes the frame index, so a trim is visibly different.
- `test_a_short_source_with_the_default_policy_is_refused_before_encoding` —
  refused at preflight with the shortfall in frames and seconds.
- `test_the_hold_last_frame_policy_preserves_the_shot_duration` — 60 frames
  requested from a 1-second source yields exactly 60 frames, and the policy is
  reported as a caution.

Nothing is silently looped, frozen or stretched. Scene 3 of the fixture
demonstrates the explicit hold policy with an on-screen note saying so.

## B08 — Original aspect ratios preserved · **PASS**

**Measured in pixels, not inspected.** A 4:3 source under `contain` at
1920×1080 occupies exactly **x ∈ [240, 1680)**:

```
x=   2 (0,0,0)      x= 120 (0,0,0)      x= 238 (0,0,0)
x= 242 white        x= 960 white        x=1676 white
x=1917 (0,0,0)
```

`test_a_four_by_three_still_is_pillarboxed_not_stretched` asserts exactly that;
`test_cover_fills_the_frame_edge_to_edge` asserts the complement;
`test_an_explicit_crop_selects_the_requested_region` crops the blue half of a
two-tone source and checks the centre is blue.

No code path emits an unconstrained `scale=W:H`, which is the only way to
stretch. `test_no_fit_mode_can_distort` asserts that for all three modes, and
`test_the_distortion_guard_actually_detects_a_stretch` proves the guard can
fail — an earlier version of it could not, because a regex lookahead backtracked
into the digits.

## B09 — Image motion implemented · **PASS**

`static`, `pan`, `zoom_in`, `zoom_out`, `pan_zoom`, with configurable start and
end positions. Applied only to stills, and only when the timeline asks.

Undefined boundaries cannot be exposed: zoom is constrained `>= 1.0`, and at
zoom *z* the centre must lie within `[0.5/z, 1 − 0.5/z]`. An out-of-range centre
is **rejected naming the interval**, not clamped — which caught a mistake in my
own fixture during development:

```
start_center_x=0.32 at zoom 1.25 would show past the image edge;
at this zoom the centre must be within [0.4000, 0.6000]
```

`test_motion_never_exposes_an_undefined_edge` pans a uniformly white source to
the permitted extreme and asserts no frame corner is ever dark, at three
timestamps. `test_motion_expressions_contain_no_commas` pins the no-escaping
property.

## B10 — Timed overlays implemented · **PASS**

Seven primitives: `title`, `subtitle`, `lower_third`, `info_text`, `image`,
`color_panel`, `comparison_label`. Nine-point anchors against the safe
rectangle, pixel offsets scaled between profiles, configurable size, colours,
opacity, start and end timing, wrapping, and a line budget.

`test_an_overlay_appears_only_within_its_span` samples the frame centre at
0.5 s, 1.5 s and 2.5 s for an overlay spanning frames 30–59, and asserts
absent/present/absent. **52 tests** in `test_text_overlays.py`, including all
nine anchors staying inside the frame.

The reference fixture carries **24 overlays**, including a comparison layout
built from two panels plus two labels (scene 4).

## B11 — Safe arbitrary text handling · **PASS**

**Text never reaches FFmpeg.** It is wrapped, measured and drawn to an RGBA PNG
by Pillow; FFmpeg receives only that file's path, as one element of an argument
vector. `drawtext` is not used anywhere (D-016).

Tested against each awkward character individually and in combination:
`'` `"` `\` `:` `%` `,` `;` `[` `]` `=`, newline, tab, a vertical-tab control
character, leading/trailing whitespace, and Unicode (Greek, Cyrillic, accented
Latin, CJK). Plus `test_hostile_overlay_text_renders_through_a_real_encode`,
which puts

```
It's 100%: a \ test; "q" [x]=1, 日本語 — éèê\nsecond: line
```

through a real encode and confirms conformance and that text was drawn.

Two standing invariants, each pinned by a test *and* by a test proving the guard
can fail: no input path appears in the filter string
(`FFmpegPlan.leaked_paths()`), and filter expressions contain only numbers,
arithmetic and fixed keywords.

**Known limitation:** CJK renders as tofu boxes because no installed font on
this host covers CJK. Safety is unaffected; glyph coverage is not. Recorded in
`DEVELOPMENT_STATUS.md`.

## B12 — Independent audio tracks · **PASS**

Four buses: `narration`, `music`, `source`, `effect`. Per segment: placement,
source offset, trimming to an exact sample count, gain, fade in/out, and
explicitly-permitted silence padding. Per role: a gain trim and optional stem
export.

Each segment is resampled and channel-normalised before mixing, so a mono
44.1 kHz source and a stereo 48 kHz source mix identically.

The reference fixture has **7 segments** (6 narration + 1 music) and exports
`narration.wav` and `music.wav` stems alongside the output. Stems are each
rendered from their own single-role graph, because a filter output may be
consumed only once.

## B13 — Audio mixing and ducking · **PASS**

Ducking is **deterministic gain automation** computed from the narration's known
integer sample spans, not a sidechain compressor (D-017). Attenuation, attack,
release and lead in/out are all configurable; the fixture uses −13 dB.

- `test_duck_windows_are_computed_from_narration_positions` — the windows are
  exactly the narration spans plus the lead times.
- `test_touching_duck_windows_are_merged` — overlapping lead times cannot
  double-attenuate.
- `test_ducking_disabled_produces_no_windows`.
- `test_narration_and_music_mix_with_ducking` — renders narration over music and
  measures the envelope either side.
- `test_ducking_without_narration_is_a_warning` — enabled ducking with nothing
  to duck is surfaced.

Overlapping narration is rejected rather than mixed.

## B14 — Audio loudness and peak verification · **PASS**

Two-pass EBU R128: measure with `ebur128`, then normalise with the measured
figures via `loudnorm` in linear mode. An `alimiter` sits before encoding
because summed buses can exceed full scale even when each bus does not.

Measured on the reference fixture against targets of −16 LUFS / −1 dBTP:

```
integrated_lufs   -16.0
loudness_range_lu   3.4
true_peak_dbtp      -7.1
```

Integrated loudness lands **exactly on target**; true peak is 6.1 dB below the
ceiling, which is a ceiling and not a target. The targets are engineering
figures for this milestone, not a platform requirement, and the renderer reports
what it achieved rather than asserting it hit them.

`test_loudness_is_measured_and_within_the_target` asserts within 3 LU of target
and at or below the peak ceiling. `measure_only` suppresses normalisation while
still reporting.

## B15 — SRT import and export · **PASS**

Import, export and timing validation, timed in integer frames. **37 tests** in
`test_captions.py`.

Tolerant of what real files contain: a UTF-8 BOM, CRLF, a missing or
non-sequential index, dots instead of commas, extra blank lines, trailing
positioning data, Unicode. Intolerant of anything ambiguous: a reversed cue, a
zero-length cue, a cue with timing but no text, a malformed timing line, a
non-numeric index.

Round trip is a **fixed point** — formatting then parsing twice yields identical
output — and hostile text survives it unchanged.

Export verified end to end: `test_burned_in_captions_appear_and_a_sidecar_is_written`
confirms the sidecar exists and contains the cue; the reference fixture writes a
932-byte `.srt` beside each render.

## B16 — Optional burned-in captions · **PASS**

`captions.burn_in` composites cues through the same safe text path as overlays,
with configurable anchor, offset, style and a `max_lines` budget that truncates
with an ellipsis and reports that it did.

`test_burned_in_captions_appear_and_a_sidecar_is_written` compares frame colour
counts at 1.0 s (caption on screen) and 1.9 s (gone). The reference fixture
burns in 12 cues including `It's 100% — "quoted", colon: ok, éèê`, visible in
the contact sheet.

## B17 — Transition timing verified · **PASS**

| Transition | Duration effect | Verified by |
|---|---|---|
| `cut` | none | `test_a_cut_adds_no_fade` |
| `fade_from_black` | **none** | `test_a_fade_from_black_starts_dark_and_brightens` — luminance < 60 at 0.0 s, > 200 at 1.5 s, and exactly 60 frames decoded |
| `fade_to_black` | **none** | `test_fade_to_black_fades_out_at_the_end` — fade starts at exactly `(frames − duration)/fps` |
| `crossfade` | overlap, accounted for in spans | `test_a_crossfade_preserves_the_scene_duration` — 30 + 30 − 10 = 50, and exactly 50 frames decoded |

Transitions never introduce an unintended duration change. Cross-scene
crossfades are **deferred and documented** (D-018): they would couple adjacent
scenes and break per-scene caching. Both the schema and the validator reject
them.

## B18 — Preview profile verified · **PASS**

960×540, 30 fps, H.264 main CRF 28 `veryfast`, yuv420p, AAC 96k 48 kHz stereo,
MP4. Square pixels declared via `-aspect`.

Rendered and verified on the reference fixture: `960x540 @ 30.0, 180.0 s,
h264/yuv420p/aac@48000, conforms=True`, 4 047 094 bytes. Cold render 53.6 s.

## B19 — Final profile verified · **PASS**

1920×1080, 30 fps, H.264 high CRF 18 `medium`, yuv420p, AAC 192k 48 kHz stereo,
MP4 `+faststart`.

```
resolution     1920x1080
frame rate     30/1
frames decoded 5400 (expected 5400)
duration       180.000000 s
video / audio  h264 High yuv420p / aac 48000 Hz 2 ch
file size      20 656 186 bytes
conforms       True (no deviations)
```

Both profiles preserve equivalent composition timing: the same timeline decodes
to the same frame count in both, asserted by
`test_both_profiles_conform_and_share_timing`. `inspect_output` is
profile-aware and also checks pixel format.

## B20 — Scene-level cache implemented · **PASS**

Content-addressed, under `.cache/scenes/<profile>/`. The key is a sha256 over
the scene's serialised content, every referenced asset's **content checksum**,
the resolved font file's identity **and checksum**, the brand values, the
profile including every encoder setting, the renderer version, and the FFmpeg
build.

**Timestamps are deliberately excluded** — a timestamp is not evidence about
content. `test_a_timestamp_is_not_part_of_the_key` asserts no `mtime`,
`modified_at`, `st_mtime`, `updated_at` or `created_at` appears in the key
payload.

Nine tests assert each input changes the key, including a CRF change and a
single palette colour. **31 tests** in `test_cache.py`.

A second, separate **assembly cache** keys on the scene digests plus the audio
mix, loudness target and caption text (D-019). Preview and final never share a
directory.

## B21 — Selective cache invalidation demonstrated · **PASS**

Measured on the 180-second fixture:

| Run | Time | Rebuilt | Reused |
|---|---|---|---|
| Cold final | 148.9 s | 6 | 0 |
| Warm final (unchanged) | **1.2 s** | 0 | 6 |
| After editing scene-04 | 76.3 s | **1** (`scene-04`) | **5** |
| Forced (`--force`) | ~66 s | 6 | 0 |

`test_editing_one_scene_rebuilds_only_that_scene` asserts
`rebuilt == ["scene-02"]` and `hits == ["scene-01"]`.
`test_editing_one_scene_leaves_other_scene_keys_unchanged` asserts the same at
the key level, without rendering.

Changing a global dependency invalidates everything, asserted per input class
(profile, encoder settings, brand, FFmpeg version, renderer version).

## B22 — Corrupt cache recovery verified · **PASS**

Entries are validated before reuse: recorded digest, recorded size against the
file on disk, and the file's content hash. A failed check **discards** the entry
and rebuilds.

Tested for a truncated file, a same-length-different-bytes file, missing
metadata, corrupt metadata, and a mismatched digest. End to end:
`test_a_corrupt_cache_entry_is_detected_and_rebuilt` truncates a real entry and
asserts `scene-01` appears in both `corrupt` and `rebuilt` while `scene-02`
stays a hit, and the output still conforms.

Live demonstration: an 8 075 195-byte entry truncated to 100 bytes was detected,
discarded, and rebuilt — only that scene.

Incomplete renders are never cached: `cache.store` refuses a missing or empty
file, and entries are written atomically with the metadata sidecar last, so a
crash between the two leaves an entry `lookup` rejects.

A bug was found and fixed here: corrupt entries were previously reported as
ordinary misses, because `lookup` discards before the check ran.

## B23 — Interrupted rendering preserves valid outputs · **PASS**

The final file is produced under a temporary name, **verified with ffprobe**,
and only then moved into place. If verification fails the existing output is
left untouched and the deviations are reported — observed live during
development, when a profile-awareness bug made a preview non-conforming and the
existing output was correctly preserved.

- `test_a_failed_render_leaves_a_valid_output_intact` — renders successfully,
  breaks the project, renders again (fails), and asserts the original bytes are
  unchanged.
- `test_an_existing_output_is_not_replaced_without_overwrite`.
- Scene intermediates are likewise produced in a temporary file and moved on
  success only.

## B24 — Project write protection and recovery verified · **PASS**

**Locking:** an `O_EXCL` lock file with pid, hostname and start time. Broken
only on evidence — holder pid gone on this host, or past the 3-hour staleness
limit — and the break is always reported.

**Journalled writes:** `ProjectBundle.save()` stages every document, makes a
manifest durable, then moves. Recovery runs on load: no readable manifest means
the batch never reached the point of intent, so it is discarded; a complete
manifest is **rolled forward**.

**30 tests** in `test_integrity.py`, including a simulated crash after the
manifest is durable but before any move, a partially-applied batch that
completes, a corrupt manifest, a future journal version, and a journal that
cannot write outside the project.

Found and fixed a real bug here: the journal's own manifest was named
`manifest.json`, colliding with the project's manifest — it overwrote the staged
copy and then moved its own bookkeeping into place as the project manifest.
Caught by the existing M0.1 suite. `test_the_journal_manifest_cannot_collide_with_a_staged_file`
now pins it.

Render integration: the lock is released after success *and* after failure, and
a render refuses to start while another command holds it.

## B25 — Approximately 180-second technical fixture rendered · **PASS**

**Exactly 5400 frames = 180.000 s** at 30 fps. Six scenes, 11 shots, 24
overlays, 7 audio segments, 15 generated assets, built in 23.1 s.

Demonstrates: six narrative scenes; multiple shots within scenes; synthetic 4:3
video (24 fps) and widescreen video (25 fps); original geometric stills at
640×480, 1024×1024, 2560×1080 and 1920×1080; pan, zoom-in, zoom-out and
pan+zoom; titles, subtitles and lower thirds; informational text; a comparison
layout; six narration-surrogate segments; a background bed; ducking; 12 SRT
cues burned in; hard cuts; fades from and to black; a within-scene crossfade;
the explicit short-source hold policy; and both output profiles.

All assets generated by `rendering/reference.py` and `rendering/synth.py`. No
third-party game or anime media is used or required.

**Not an episode.** Narration is a synthetic tone, not speech; the closing card
says "Synthetic fixtures - not an episode, not approved"; rights are
`original_work` but **unreviewed**.

## B26 — Output properties inspected · **PASS**

See B19 for the final profile and B18 for preview. Inspection is performed three
ways: by the engine during assembly (before replacing any existing output), by
`polymation inspect` as a separate command, and in the container by
`docker/inspect_output.py`, which parses ffprobe's JSON itself so a bug in the
application's own verification cannot hide a bad render.

`test_inspection_reports_deviations_from_the_specification` builds a
deliberately off-spec file (640×480, 25 fps, no audio) and asserts all three
deviations are reported — the check is proven able to fail.

## B27 — Cold and warm rendering benchmarked · **PASS**

`scripts/benchmark.py`, reproducible, on a **fresh temporary workspace per run**
so no cache state leaks in. Full methodology in `COMPOSITION.md` §14.

| # | Measurement | Value |
|---|---|---|
| 1 | Cold preview render | **53.6 s** |
| 2 | Cold final render | **148.9 s** |
| 3 | Warm final, unchanged | **1.2 s** |
| 4 | Final after one scene edit | **76.3 s** |
| 5 | Scenes rebuilt (after edit) | **1** |
| 6 | Scenes reused (after edit) | **5** |
| 7 | Peak RSS, this process | **402.7 MiB** (excludes FFmpeg children) |
| 8 | Final output size | **20 656 186 bytes** |
| 9 | Intermediate cache size | ~16.7 MB scenes + ~20.6 MB assembly (final) |
| 10 | Loudness / peak | **−16.0 LUFS**, −7.1 dBTP, 3.4 LU range |
| 11 | Output frame count | **5400** decoded (expected 5400) |
| 12 | Output duration | **180.000 s** |
| 13 | A/V timing discrepancies | **0** |

Conditions reported by the script: Amazon Linux 2, Linux 5.10.273 x86_64, Xeon
8252C @ 3.80 GHz, 8 vCPU, 30.6 GiB, FFmpeg 9.1, Python 3.11.4.

Honest caveat on (7): `ru_maxrss` covers this process, not the FFmpeg children
that do the encoding. It is a floor, not the true peak, and the script says so.

No performance optimisation was done speculatively. The assembly cache (D-019)
exists **because** the measurement showed warm renders spending 112 s of 115 s
in assembly.

## B28 — Audio/video synchronisation verified · **PASS**

**Measured, not eyeballed.** Nine markers across the full 180 seconds, each a
magenta block on exactly known frames plus a short click at the corresponding
samples:

```
markers expected  9
visual detected   9
audio detected    9
discrepancies     0
at 0.2, 26.2, 42.0, 58.2, 75.0, 92.2, 122.2, 138.0, 154.2 s
```

Drift in either direction breaks one of the two checks.
`test_markers_line_up_in_video_and_audio` does the same at test scale and adds a
**negative** check at a timestamp where no marker should be, so the detector is
shown able to report absence.

## B29 — Existing regression suite passes · **PASS**

All 279 M0.1 tests pass unchanged inside the M0.2 suite (278 original plus one
added during the baseline closure for the font fallback chain). No M0.1 test was
deleted or weakened.

## B30 — New integration tests pass · **PASS**

**650 tests total: 0 failed, 0 errors, 0 skipped**, 144.5 s.

| Module | Tests |
|---|---|
| `test_timeline_schema` | 70 |
| `test_text_overlays` | 52 |
| `test_schemas` | 57 |
| `test_timeline_pipeline` | 44 |
| `test_paths` | 43 |
| `test_graph` | 41 |
| `test_render_integration` | **41** (all real FFmpeg) |
| `test_captions` | 37 |
| `test_media` | 34 |
| `test_validation` | 32 |
| `test_cache` | 31 |
| `test_projects` | 31 |
| `test_rendering` | 31 |
| `test_integrity` | 30 |
| `test_timing` | 25 |
| `test_cli` | 28 |
| `test_fixtures` | 23 |

Integration tests execute real FFmpeg commands throughout; **no rendering is
mocked**. Temporary workspaces only, no network access.

Writing them found four real bugs, all fixed: `compile_timeline` raised from
`zip(..., strict=True)` whenever no narration was supplied (so compiling any
storyboard without narration was broken); the first distortion guard could not
detect a stretch; and two test premises were wrong rather than the code.

## B31 — Lint and formatting pass · **PASS**

```
$ ruff check .          → All checks passed!
$ ruff format --check . → 74 files already formatted
```

Ruff 0.14.14, rule sets E, F, I, B, UP, S (bandit), RUF, line length 100. No
blanket suppressions; the few `# noqa` comments each explain why the rule does
not apply, and the `per-file-ignores` entries carry a comment saying why.

## B32 — Documentation updated · **PARTIAL**

Updated: `README.md`, `docs/ARCHITECTURE.md` (module map, storage model,
rendering boundaries, CLI, extension points), `docs/DECISIONS.md` (D-014–D-019),
`docs/DEVELOPMENT_STATUS.md`, this matrix, and a new `docs/COMPOSITION.md`
covering timeline authoring, composition operations, audio mixing, rendering
profiles, cache behaviour, project recovery, benchmark methodology and fixture
reproduction.

**Why PARTIAL rather than PASS:** the documentation states that CI's M0.2 steps
have never run remotely, which is accurate but means the documented CI evidence
is incomplete. The CI configuration is extended and its gate logic was verified
locally in both directions, but no green run exists. Marking this PASS would
imply documented evidence that does not yet exist.

## B33 — No copyrighted third-party fixture assets · **PASS**

Every fixture asset is generated by code in this repository:
`rendering/fixtures.py` (geometric stills, title and closing cards),
`rendering/synth.py` (video encoded from Pillow-drawn frames, arithmetic audio),
`rendering/reference.py` (the 180-second fixture's 15 assets).

No game footage, anime footage, screenshots, commercial music, downloaded
artwork, downloaded fonts or stock media exists anywhere in the repository.
Generated media is git-ignored. Fonts are *resolved from the host*, never
vendored.

`test_test_pattern_uses_only_palette_colours` asserts the test pattern contains
only declared brand colours — the output is fully accounted for by the code.
Generation is deterministic, verified by checksum.

## B34 — Zero unauthorized paid API expenditure · **PASS**

**$0.00.** No API key, no credential, no paid or metered service. Dependencies
are unchanged from M0.1: 21 free open-source packages, all in `uv.lock`. No new
runtime dependency was added for M0.2.

No runtime network access exists: nothing in `src/` imports `urllib`,
`requests`, `httpx` or `socket` except `socket.gethostname()` in the lock file,
which makes no connection. The test suite requires no network.

Network use during development was limited to PyPI, Homebrew and the Docker Hub
pull of `python:3.12-slim` for the verification container.

## B35 — Known limitations disclosed · **PASS**

Disclosed in three places, consistently: `README.md` ("Known limitations",
grouped by area), `docs/DEVELOPMENT_STATUS.md` §4 (with rationale and the
decision that produced each), and `docs/ARCHITECTURE.md` §10 ("What it
deliberately does not do").

Named explicitly: no cross-scene crossfades; no retiming; no motion on video;
no vertical shorts; no CJK glyph coverage; provisional brand and substituted
fonts; no content generation of any kind; no caption generation; no publishing;
rights decisions human-only with no command to create an approval; advisory
locking only; cache entries not garbage-collected; no cross-project index;
unbounded checksum verification cost; and no green CI run.

---

## Summary

| Verdict | Count | Criteria |
|---|---|---|
| **PASS** | 34 | B01–B31, B33–B35 |
| **PARTIAL** | 1 | B32 (documentation accurate, but no green CI run to document) |
| FAIL | 0 | — |
| NOT EXECUTED | 0 | — |

Qualifications to read alongside the table:

1. **No green CI run.** The workflow is extended for M0.2 and its new gate logic
   was verified locally in both directions, but the M0.2 steps have never
   executed remotely. Nothing claims CI passes.
2. **Two validation tiers, reported separately.** Host (Python 3.11.4,
   FFmpeg 9.1) and pinned (Python 3.12.16, FFmpeg 7.1.5 in a container). The
   tiers use different FFmpeg builds, so encoded bytes differ between them; the
   output *specification* is what is asserted.
3. **Rendering a fixture is not editorial or rights approval.** The reference
   fixture's rights are deliberately unreviewed, and publication readiness is
   reported as `null` everywhere.

M0.2 is ready for independent review. **M0.3 is not authorized and has not been
started.**
