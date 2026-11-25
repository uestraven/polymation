# Composition reference

How to author a timeline, what the engine can and cannot do, and how rendering,
caching and recovery behave. Current as of M0.2.

Companion documents: [`ARCHITECTURE.md`](ARCHITECTURE.md) for structure,
[`DECISIONS.md`](DECISIONS.md) for why each choice was made,
[`DEVELOPMENT_STATUS.md`](DEVELOPMENT_STATUS.md) for what is not implemented.

---

## 1. Storyboard vs timeline

Two documents, two purposes, two schema versions:

| | Storyboard (`storyboard.json`) | Timeline (`timeline.json`) |
|---|---|---|
| Records | creative **intent** | executable media **placement** |
| Timing | `duration_seconds` (float) | integer frames and samples |
| Written by | a human, editorially | compilation, then hand-edited |
| Changes when | the edit idea changes | the execution changes |

`polymation timeline compile` projects the storyboard onto the frame grid. It is
**one-way**: it never writes back to the storyboard, and re-compiling discards
hand edits to the timeline — which is why it refuses to overwrite an existing
timeline without `--overwrite`.

A project may have no timeline at all. Every project M0.1 created is in that
state, and loads unchanged.

---

## 2. Timing

**Integer frames are authoritative for video; integer samples for audio.**
Seconds appear only where FFmpeg needs them.

The reason is drift. A three-minute video is ~5400 frames; accumulating 5400
float durations eventually drops or duplicates a frame and desynchronises the
audio. Integers cannot drift.

At the supported profile the two grids align exactly:

```
48000 Hz / 30 fps = 1600 samples per frame, no remainder
```

`frames_to_samples()` **refuses** to convert when the sample rate is not an
exact multiple of the frame rate, rather than rounding per frame. (Note that
44.1 kHz at 30 fps also aligns, at 1470 samples per frame — the unaligned cases
are rarer than they look, which is why the check is explicit.)

### Rounding

Converting a storyboard's float seconds to frames is the one place rounding
happens. Every rounding decision is returned as a `RoundingApplied` record and
surfaced by `timeline compile`:

```
$ polymation timeline compile PROJECT_ID
  3 duration(s) were snapped to the frame grid:
    shot 'shot-01' duration: 1.2500 -> 1 (nearest, error 0.2500)
```

Imported media at a different frame rate is normalised by FFmpeg's `fps` filter
at render time; the timeline itself is always on the output grid.

### Spans

`FrameSpan` and `SampleSpan` are half-open — `[start, start + duration)` — so
adjacent spans share a boundary without overlapping, and `sum(durations)`
equals the total exactly.

---

## 3. Scenes and shots

A **scene** is the unit of caching, and renders independently. Its shots are
timed **relative to the scene**, which is what makes that independence possible;
the assembler offsets scenes when it concatenates them.

Shots must **tile** their scene with no gaps:

```
shot-01: start_frame=0,  duration_frames=150     # 0 .. 150
shot-02: start_frame=150, duration_frames=90     # 150 .. 240
                                                 # scene = 240 frames
```

A crossfade is the one exception — the incoming shot overlaps the outgoing one,
and the overlap is subtracted from the scene length:

```
shot-01: start_frame=0,   duration_frames=150
shot-02: start_frame=140, duration_frames=90, transition_in=crossfade(10)
                                                 # scene = 230 frames
```

Anything else is a schema error naming the frame the shot should have started
at.

---

## 4. Fit modes

None of these distorts. There is no code path that emits an unconstrained
`scale=W:H`, and a test asserts that for every mode.

| Mode | Behaviour | Use for |
|---|---|---|
| `contain` | Scale to fit, pad the remainder with `background_color` | Archival 4:3 material — **the default** |
| `cover` | Scale to fill, crop the overflow | Full-frame backgrounds where edges do not matter |
| `crop` | Take an explicit normalised rectangle, then cover | Isolating part of a source |

A 4:3 source under `contain` at 1920×1080 occupies exactly **x ∈ [240, 1680)**,
with the pad colour outside. That is verified by decoding a frame, not by
inspection.

`crop` rectangles are **normalised** (`0.0`–`1.0`), so the same rectangle
survives the source being re-exported at a different resolution.

### Scaling quality

| `scaling` | Kernel | Use for |
|---|---|---|
| `smooth` | the profile's (`lanczos` final, `bilinear` preview) | photographic, vector-ish |
| `pixel` | `neighbor` | pixel-art-era game graphics |

Bilinear scaling of a 256×224 sprite sheet turns crisp pixels to mush; `pixel`
exists for exactly that material.

---

## 5. Video sources

Supported: trimming, frame-rate normalisation, resolution normalisation,
aspect-ratio preservation, muted source audio (the default), explicit source
audio inclusion, and mixed still/video sequences.

`source_trim.start_frame` is an offset into the source in **output frame
units**. There is no separate source duration, because M0.2 renders at 1:1
speed — retiming is out of scope, and modelling a source duration would imply
support that does not exist.

### Short sources

A source shorter than its shot is a **validation error by default**. The
alternatives must be requested explicitly:

| `short_source_policy` | Behaviour |
|---|---|
| `error` *(default)* | Refuse to render, naming the shortfall in frames and seconds |
| `hold_last_frame` | Freeze the final source frame for the remainder |
| `pad_with_background` | Fill the remainder with `background_color` |

Both non-error options **preserve the shot's timeline duration**. Shortening the
shot instead would move every downstream frame and the audio with it, so it is
not offered: if a shot should be shorter, shorten it in the timeline.

Nothing is ever silently looped, frozen or stretched.

---

## 6. Image motion

A closed set of deterministic operations: `static`, `pan`, `zoom_in`,
`zoom_out`, `pan_zoom`. Motion is applied only to **still images**, and only
when the timeline asks for it — it is never added automatically.

Motion cannot reveal undefined pixels, by construction:

- zoom is constrained to `>= 1.0`;
- at zoom *z* the visible window is `1/z` of the source, so the centre must lie
  within `[0.5/z, 1 − 0.5/z]`.

A centre outside that interval is **rejected** with a message naming the
permitted range, not silently clamped:

```
start_center_x=0.32 at zoom 1.25 would show past the image edge;
at this zoom the centre must be within [0.4000, 0.6000]
```

Implementation: `zoompan` over a canvas pre-scaled by the maximum zoom, so at
full zoom the crop window is at native canvas resolution rather than an upscale
of the output. `validate_timeline` warns when the source has too few pixels for
the requested magnification.

---

## 7. Overlays and typography

### Text is never given to FFmpeg

Text is wrapped, measured and drawn into an RGBA PNG by Pillow. FFmpeg receives
only that file's **path**, as one element of an argument vector.

`drawtext` would require escaping `:` `'` `\` `%` and more, with rules that
differ between the filter-graph parser and the filter itself. On
operator-supplied text that is a command-injection-shaped bug. Not doing it at
all removes the class of bug. Apostrophes, backslashes, quotes, colons, percent
signs, brackets, newlines, tabs, Greek, Cyrillic and CJK are all just pixels.

### Primitives

`title`, `subtitle`, `lower_third`, `info_text`, `image`, `color_panel`,
`comparison_label`. A comparison layout is built from two `color_panel`s plus
two `comparison_label`s — see scene 4 of the reference fixture.

### Placement

A nine-point `anchor` positions the overlay against the **safe rectangle**, not
the frame edge, inset by `safe_area_inset` (5% by default). `offset_x_px` and
`offset_y_px` adjust from there, and are **scaled** when a timeline authored at
one resolution renders at another — which is what makes preview a faithful
stand-in for final.

Text wraps to `max_width_fraction`. A word longer than the available width is
broken mid-word rather than allowed to overflow.

### Fonts

`font_family` is a *reference*. Resolution tries the requested family, then a
documented fallback chain (DejaVu Sans and similar, chosen for glyph coverage),
then Pillow's built-in face. **A substitute is always reported**, and the render
output says the result does not represent brand typography. Final brand
typography is M0.3.

---

## 8. Audio

Four independently controllable buses: `narration`, `music`, `source`,
`effect`. Each segment supports placement, trimming, gain, fades and — only
when explicitly permitted — silence padding.

Per segment the chain is: resample to the output rate → normalise to stereo →
trim to exactly the requested sample count → optional padding → fades → gain →
delay to the timeline position.

### Ducking is automation, not a compressor

Narration positions are known exactly — they are integer sample spans — so the
attenuation applied to music is computed from those positions and expressed as a
piecewise `volume` envelope with linear attack and release ramps.

A sidechain compressor would key off the waveform instead, making the result
depend on narration level and resisting assertion in a test. Automation is
reproducible: a test can compute where music *should* be quiet and check that it
is.

Overlapping narration segments are **rejected** — two voices at once is not what
this pipeline is for. Overlapping music is allowed.

### Loudness

Two-pass: measure the mix with `ebur128`, then normalise using the measured
figures (`loudnorm` in linear mode, so it is a gain change rather than dynamic
processing). A true-peak limiter sits before encoding, because summed buses can
exceed full scale even when each bus does not.

Targets default to **−16 LUFS integrated** and **−1 dBTP true peak**. These are
engineering figures for this milestone, **not a platform requirement**. The
renderer reports what it actually achieved:

```
Loudness (EBU R128, measured):
  Integrated:  -16.0 LUFS
  Range:       3.4 LU
  True peak:   -7.1 dBTP
```

Set `loudness.measure_only` to measure without normalising.

`export_stems` writes one WAV per role beside the output. Each stem is rendered
from its own single-role graph, because an FFmpeg filter output may be consumed
only once and the mix already feeds every bus into `amix`.

**No text-to-speech.** Narration is either supplied by a human or, in fixtures,
a synthetic tone — which is never presented as speech.

---

## 9. Captions

Externally supplied SRT only. There is no transcription and no speech
alignment; caption *generation* is deferred.

- Import and parse, timed in integer frames.
- Validate timing: a cue past the end of the video or an overlapping pair is an
  **error**; a very short or long cue, an over-long line, or too many lines is a
  **warning**.
- Export a sidecar `.srt` beside the rendered video.
- Optionally burn in, drawn through the same safe text path as overlays.

Parsing tolerates what real SRT files contain — a BOM, CRLF, a missing or
non-sequential index, dots instead of commas, trailing positioning data — and
refuses anything ambiguous: a reversed cue, a zero-length cue, or a cue with
timing but no text.

Burn-in forces a full re-encode, since captions change pixels. That is why
assembly has its own cache (§11).

---

## 10. Transitions and profiles

| Transition | Scope | Duration effect |
|---|---|---|
| `cut` | — | none |
| `fade_from_black` | within a shot | **none** — the fade happens inside the span |
| `fade_to_black` | within a shot | **none** |
| `crossfade` | between shots **in one scene** | the overlap shortens the scene, accounted for in the spans |

**Cross-scene crossfades are not supported.** They would couple adjacent scenes
and break per-scene caching. Both the schema and the validator reject them; use
a shot-level crossfade inside a scene. This is a documented limitation.

### Output profiles

| | Preview | Final |
|---|---|---|
| Resolution | 960×540 | 1920×1080 |
| Frame rate | 30 | 30 |
| Video | H.264 main, CRF 28, `veryfast` | H.264 high, CRF 18, `medium` |
| Pixel format | `yuv420p` | `yuv420p` |
| Audio | AAC 96k, 48 kHz stereo | AAC 192k, 48 kHz stereo |
| Scaler | `bilinear` | `lanczos` |
| Container | MP4 | MP4, `+faststart` |

Square pixels, stated explicitly via `-aspect`. Composition timing is identical
across profiles; only encoding differs. Caches and outputs are separate.

Vertical shorts rendering is out of scope.

---

## 11. Caching

Two caches, under `<project>/.cache/`, with separate dependencies.

### Scene cache — `.cache/scenes/<profile>/`

Keyed on a sha256 of **everything that can change the rendered bytes**:

- the scene's serialised content;
- the **content checksum** of every asset it references;
- the resolved font file's identity **and its checksum** — two machines with the
  same family name but different font files must not share entries;
- the brand values composition reads;
- the render profile, including every encoder setting;
- the renderer implementation version;
- the FFmpeg build.

**Modification timestamps are deliberately excluded.** A timestamp is not
evidence about content: touching a file changes it, restoring a backup does not,
and coarse filesystem timestamps can miss a change entirely. A test asserts no
timestamp field appears in the key payload.

Two consequences, both tested: editing one scene leaves every other scene's key
unchanged; changing a global input changes every key.

### Assembly cache — `.cache/assembly/<profile>/`

Keyed on the scene digests plus the audio mix, loudness target and caption
text — none of which belong to any single scene. Without it, burning in
captions re-encoded the whole video on every render, because captions defeat the
stream-copy path: a warm 180-second final render took 115 s, of which 112 s was
assembly. With it, warm assembly is 0.1 s.

### Validation before reuse

An entry is validated before it is served: the recorded digest, the recorded
size against the file on disk, and the file's content hash. A render killed
mid-write can leave a short file, and serving that as a hit would corrupt the
output silently. A failed check **discards** the entry and reports it:

```
Scenes:       5 cached, 1 rebuilt
  discarded as corrupt and rebuilt: scene-02
```

Entries are written atomically: the video is placed with `os.replace`, and the
metadata sidecar only afterwards, so a crash between the two leaves an entry
`lookup` rejects rather than one it accepts.

### Commands

```bash
polymation cache status PROJECT_ID    # per profile, per scene: fresh or stale
polymation cache clear PROJECT_ID     # both profiles
polymation cache clear PROJECT_ID --profile preview
```

Clearing is scoped to the named project's `.cache`. **Imported source media and
rendered outputs are never touched** — there is a test for that.

Superseded entries are not garbage-collected: editing a scene leaves its old
entry on disk. `cache status` reports total bytes so this is visible.

---

## 12. Integrity and recovery

### Project locks

An advisory lock file (`.polymation.lock`) created with `O_EXCL`, carrying the
holder's pid, hostname and start time. It prevents the realistic accident — two
commands in two terminals — and does **not** claim to coordinate across NFS or
between hosts.

A lock is broken only on evidence: the holder's pid is gone on this host, or the
lock is past the staleness limit (3 hours, generous because a long final render
legitimately holds one for minutes). Breaking a stale lock is always reported.

### Journalled writes

`ProjectBundle.save()` writes six or seven documents. Each was already atomic,
but the *set* was not: a crash between two writes could leave an asset recorded
with no matching rights record.

Writes now go through a write-ahead journal in `.journal/`: stage every
document, make a manifest durable, then move the files into place. Recovery
runs automatically on load:

- **manifest absent or unreadable** → the batch never reached the point of
  intent, so it is discarded;
- **manifest complete** → the batch is **rolled forward**, moving whatever is
  still staged.

Roll-forward rather than rollback: once the manifest is durable, the new state
is the intended one, and some files may already be live.

### Render output protection

A failed render never replaces a valid output. The final file is produced under
a temporary name, **verified with ffprobe**, and only then moved into place. If
verification fails, the existing output is left untouched and the deviations are
reported.

An existing output is never overwritten without `--overwrite`.

A **scene-only** render (`--scene`) deliberately skips assembly, and says so:

```
Final assembly was NOT updated: this was a scene-only render.
The existing output at ... is now STALE and does not reflect these scenes.
```

---

## 13. Reproducing the reference fixture

```bash
polymation reference build --recreate
polymation render reference-engineering-fixture --preview
polymation render reference-engineering-fixture --final
polymation inspect projects/reference-engineering-fixture/output/\
reference-engineering-fixture-final.mp4
```

Exactly **5400 frames = 180.000 s** at 30 fps: six scenes, 11 shots, 24
overlays, 7 audio segments, 15 generated assets. It covers every fit mode,
every motion kind, stills and video together, multi-shot scenes, a within-scene
crossfade, fades at the boundaries, every overlay primitive, the explicit
short-source policy, narration plus ducked music, and burned-in captions.

Generation is deterministic, so repeated builds produce identical checksums and
therefore identical cache keys.

**It is an engineering benchmark, not an episode.** Narration is a synthetic
tone, not speech, and the closing card says so on screen. Rights are
`original_work` but **unreviewed** — the project does not approve its own
output.

### Synchronisation markers

Nine markers at known positions (0.2 s, 26.2 s, 42.0 s, 58.2 s, 75.0 s,
92.2 s, 122.2 s, 138.0 s, 154.2 s). Each is a magenta block on exactly known
frames plus a short click at the corresponding samples, so A/V sync is
**measured**: extract the frame, measure the audio envelope, and drift in either
direction breaks one of the two checks.

---

## 14. Benchmark methodology

```bash
scripts/benchmark.py [--json results.json] [--keep]
```

- A **fresh temporary workspace per run**, so no cache state leaks in. This
  matters: a hand-run of these measurements once reported a warm render as slow
  because the assembly cache did not yet exist, and reported a modified-scene
  rebuild as a cache hit because the previous run had applied the same edit.
- Build time is reported separately from render time.
- The single-scene-edit measurement applies a **unique** edit each run, so it
  cannot reproduce a cached state by accident.
- Peak memory is this process's maximum RSS and **excludes FFmpeg children**,
  which do the heavy lifting. Treat it as a floor, not the true peak.
- Times include FFmpeg process startup and are specific to the host CPU and
  FFmpeg build, both of which the script reports.

The script exits non-zero unless the output conforms, the decoded frame count
equals the timeline's, and all markers are detected in both channels.
