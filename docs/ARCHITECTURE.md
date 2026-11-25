# Architecture

Status: current as of M0.2. Everything described here is implemented unless
explicitly marked as a future extension point.

For how to *use* the composition engine — timeline authoring, fit modes, motion,
audio mixing, captions, profiles, caching, recovery, benchmark methodology — see
[`COMPOSITION.md`](COMPOSITION.md). This document covers structure and
boundaries.

---

## 1. Shape of the system

Polymation Studio is a **local CLI application over a filesystem store**. There
is no server, no database, no daemon and no network client. A command resolves a
workspace, loads or writes JSON documents, optionally shells out to FFmpeg, and
prints a result.

```
          polymation <command>
                  │
        ┌─────────▼──────────┐
        │ cli.py cli_render  │  parsing, output formatting, exit codes
        └─────────┬──────────┘
   ┌──────┬───────┼────────┬──────────┬───────────┬──────────┐
   ▼      ▼       ▼        ▼          ▼           ▼          ▼
projects/ media/ rights/ captions/ timeline/  validation/ rendering/
store     inspect report  srt       compile    validator   engine
ids       import          
locking   checksum                  validate   paths       scene
journal                                                    graph audio
                                                           cache overlays
                                                           text colors
                                                           profiles loudness
                                                           synth reference
                                                           fixtures compose
   └──────┴───────┴────────┴──────────┴───────────┴──────────┘
                            │
                    ┌───────▼───────┐
                    │   schemas/    │  Pydantic data contracts
                    └───────┬───────┘   (+ timeline, timing)
                            │
                    ┌───────▼───────┐
                    │    config/    │  workspace layout, channel YAML
                    └───────────────┘
```

Dependencies point downward only. `schemas` imports `validation.paths` (to
validate stored paths) and nothing else from the layers above it;
`validation/__init__.py` deliberately re-exports nothing so that this does not
become an import cycle.

### Module responsibilities

| Module | Owns |
|---|---|
| `cli.py` | Typer app, flag parsing, text and `--json` rendering, exit-code mapping. No business logic. |
| `errors.py` | Exception hierarchy and the `ExitCode` enum. Every error carries its own exit code. |
| `doctor.py` | Environment assessment: interpreter, packages, FFmpeg, channel config, disk. |
| `config/paths.py` | Workspace discovery and the project directory layout. |
| `config/channel.py` | Loading and validating `channels/<id>/{brand,editorial}.yaml`. |
| `schemas/` | All persisted data contracts, each with a `schema_version`. |
| `projects/ids.py` | Slug and stable project-identifier generation. |
| `projects/store.py` | Atomic read/write of project documents; `ProjectBundle`. |
| `media/checksum.py` | Streaming sha256. |
| `media/inspect.py` | Content-based format detection (Pillow, stdlib `wave`, ffprobe). |
| `media/importer.py` | Copying media into a project with provenance and a rights record. |
| `rights/report.py` | Grouping assets by rights review status. |
| `validation/paths.py` | Safe project-relative paths; traversal and symlink-escape rejection. |
| `validation/validator.py` | Cross-document validation; structural vs render readiness. |
| `rendering/ffmpeg.py` | Tool discovery and subprocess invocation. |
| `rendering/fixtures.py` | Programmatic generation of original images and audio. |
| `rendering/compose.py` | The M0.1 still-image renderer, plus profile-aware ffprobe verification used by both milestones. |
| `rendering/demo.py` | Building the M0.1 demonstration project from fixtures. |
| `rendering/profiles.py` | Preview and final profiles, including every encoder setting. |
| `rendering/engine.py` | Orchestration: preflight, per-scene render, assembly, verification. |
| `rendering/scene.py` | Rendering one scene to a cacheable intermediate. |
| `rendering/graph.py` | Filter-graph construction: fit, crop, motion, fades, joins, overlay compositing. |
| `rendering/overlays.py` | Overlay rendering and anchor/safe-area placement. |
| `rendering/text.py` | Text to pixels. The reason arbitrary text is safe. |
| `rendering/colors.py` | Resolving `#RRGGBB` and `palette:` specs against the brand. |
| `rendering/audio.py` | The audio mix graph, buses, and ducking automation. |
| `rendering/loudness.py` | EBU R128 measurement and two-pass normalisation. |
| `rendering/cache.py` | Content-addressed scene and assembly caches. |
| `rendering/synth.py` | Synthetic video generation and A/V sync markers. |
| `rendering/reference.py` | The ~180-second engineering reference fixture. |
| `timeline/compile.py` | Projecting a storyboard onto the frame grid. |
| `timeline/validate.py` | Timeline validation against assets, profile and filesystem. |
| `captions/srt.py` | SRT parsing, formatting and timing validation. |
| `projects/locking.py` | Advisory project locks with stale-lock recovery. |
| `projects/journal.py` | Write-ahead journal for recoverable multi-document writes. |
| `validation/findings.py` | Dependency-free finding types, shared by every layer. |

---

## 2. Project storage model

**Versioned JSON manifests are the authoritative state of a video project.**
There is no database in M0.1 (see `docs/DECISIONS.md`, D-004).

A workspace root holds:

```
<workspace>/
├── channels/<channel_id>/{brand,editorial}.yaml   channel configuration
├── projects/<project_id>/                         one directory per video
└── fixtures/                                      generated original fixtures
```

Workspace discovery, in order: `$POLYMATION_HOME`, then the nearest ancestor of
the working directory containing `channels/`, then the working directory. The
`--workspace` flag overrides all of it.

Each project directory holds:

```
projects/<project_id>/
├── manifest.json      VideoProject        — identity, format, stage, paths
├── brief.md           (free text)         — human editorial brief
├── script.json        ScriptDocument      — narration scenes, claims, notes
├── storyboard.json    Storyboard          — scenes → ordered visual shots
├── assets.json        AssetCollection     — imported media + checksums
├── rights.json        RightsLedger        — one record per asset
├── approvals.json     ApprovalLedger      — human decisions + artefact hashes
├── timeline.json      CompositionTimeline — executable placement (M0.2, optional)
├── assets/{images,footage,narration,graphics,music}/
├── research/ captions/ previews/ thumbnails/ output/
├── .cache/             scene and assembly render caches (git-ignored)
├── .journal/           write-ahead journal, present only mid-write
└── .polymation.lock    advisory lock, present only while a command runs
```

`timeline.json` is **optional**: every project M0.1 created has none, and loads
unchanged (D-015).

### Write discipline

All document writes go through `projects.store.write_document`, which writes to a
temporary file in the destination directory, `fsync`s it, and `os.replace`s it
into place. An interrupted write therefore cannot leave a partially serialised
manifest. JSON is emitted with two-space indentation and a trailing newline so
it reads and diffs well.

### Identity rules

- `project_id` is assigned once at creation and persisted. Its format is
  `YYYYMMDD-<slug≤40>-<6 hex>` — readable, but **never re-derived from the
  title**, so renaming a video neither moves nor renames its project.
- The directory name must equal the manifest's `project_id`. A mismatch is a
  hard error; the loader refuses to guess which is authoritative.
- An existing project directory is never overwritten.

---

## 3. Schema ownership

Every persisted document subclasses `VersionedDocument` and declares:

- `DOCUMENT_KIND` — the name used in error messages;
- `SUPPORTED_SCHEMA_VERSIONS` — what this build can read;
- a `schema_version` field, validated on construction.

Reading a document is a two-step process on purpose:

1. `Model.assert_readable_version(raw_payload)` inspects only the declared
   version. A document from a future build raises
   `UnsupportedSchemaVersionError`, naming the found and supported versions.
2. `Model.model_validate(payload)` parses the body.

Without step 1, a future document would produce a pile of confusing field
errors. With it, the operator gets one actionable sentence.

All models use `extra="forbid"`, so an unexpected field is an error rather than
silent data loss, and `validate_assignment=True`, so mutating a loaded document
re-validates it.

### The schema layer vs the validator

The split matters:

- **Schema layer** — shape of one document: types, ranges, enum membership,
  identifier grammar, uniqueness within a document, internal consistency (an
  image asset must have dimensions; a shot must sit under the scene it names).
- **`validation/validator.py`** — relationships *between* documents and the
  filesystem: dangling asset and scene references, missing files, checksum
  drift, orphan rights records, invalidated approvals.

A problem the schema layer can catch is caught there, at load time, so an
invalid document never reaches business logic.

---

## 4. Narration scenes vs visual shots

These are deliberately distinct concepts, and the schemas enforce the
distinction:

- A `NarrationScene` (in `script.json`) is a narrative beat with a stable
  `scene_id` and narration text.
- A `StoryboardScene` (in `storyboard.json`) references a narration scene by
  identifier and contains an **ordered list of `VisualShot`s**.
- A `VisualShot` is the renderable unit: one asset, one duration, one fit mode.

One scene may have many shots. Nothing in the system assumes one scene equals
one asset.

---

## 5. Media asset handling

### Detection is content-based

`media/inspect.py` never trusts a file extension:

1. Pillow opens and `verify()`s the file. Success means it is an image; its
   format and dimensions come from the decoder.
2. The standard library's `wave` module parses the RIFF header. Success means
   uncompressed audio. This runs *before* ffprobe deliberately, so a WAV is
   described identically whether or not FFmpeg is installed.
3. ffprobe describes anything else, and the stream list — not the container
   name — decides video versus audio. Cover art (`attached_pic`) is excluded so
   an MP3 with embedded artwork is not mistaken for a video.

A file that none of the three can read is **rejected**, never imported with
guessed metadata. If step 3 would be needed and ffprobe is absent, the error
says exactly that.

### Import guarantees

`media/importer.py`:

- validates the destination filename as a single safe path segment;
- chooses the destination directory from the *detected* type, rejecting a
  category that does not suit it (an image cannot be filed under `music/`);
- **never overwrites** — a name collision is resolved by suffixing `-1`, `-2`;
- refuses byte-identical content by sha256 unless `--allow-duplicate` is passed;
- re-hashes the copy and deletes it if it does not match the source;
- creates a `MediaAsset` *and* an `unreviewed` `RightsRecord` in one operation,
  so no asset can exist without provenance.

Checksums are the identity of the bytes. `polymation validate` re-hashes every
asset file and reports drift, which is also what invalidates any approval of it.

---

## 6. Rights record architecture

The central design rule: **a basis is a claim about provenance, not permission.**

`RightsBasis` records where material came from — `original_work`, `licensed`,
`public_domain`, `user_supplied`, `proposed_fair_use`, `unknown`.
`ReviewStatus` records what a human decided — `unreviewed`, `approved`,
`rejected`, `restricted`.

The model enforces, at construction time:

| Rule | Enforced by |
|---|---|
| `unknown` rights can never be `approved` | `BASES_NEVER_AUTO_RESOLVABLE` |
| `user_supplied`, `proposed_fair_use` and `licensed` need an `ApprovalRecord` before `approved` | `BASES_REQUIRING_HUMAN_APPROVAL` |
| `licensed` requires a `license_identifier` | field validator |
| `attribution_required` requires `attribution_text` | field validator |

Imports always produce `unreviewed`, regardless of the basis claimed — including
for fixtures this project generates itself. Nothing in the codebase sets
`approved`; that transition belongs to a human.

`rights/report.py` groups assets into approved / restricted / rejected /
unresolved. An asset with **no** rights record is reported as unresolved, never
as cleared: a missing record is an absence of information.

No code path makes a copyright determination. See `docs/MEDIA_RIGHTS.md`.

---

## 7. Approvals and invalidation

An `ApprovalRecord` binds a stage, an artefact reference, an **artefact
checksum**, an approver and a timestamp. `ApprovalRecord.covers()` returns true
only when the decision was `approved`, the reference matches, *and* the checksum
matches.

That checksum binding is what makes invalidation possible: when an approved
artefact's bytes change, its hash changes and the approval stops applying. The
validator surfaces this as an `approval_invalidated` warning.

M0.1 stores and compares approval records. It deliberately does **not** implement
a workflow engine, notifications, or automatic stage transitions.

---

## 8. Validation: three separate questions

`validation/validator.py` keeps three states apart, and the CLI reports all
three separately:

| State | Meaning | Who decides |
|---|---|---|
| **Structurally valid** | Documents parse and refer only to things that exist. An empty draft qualifies. | `validate_project()` — no errors |
| **Render ready** | Enough material to produce frames; every referenced file present and unmodified. | `assess_render_readiness()` — no blockers |
| **Publication ready** | Cleared rights plus recorded human approvals. | **Not evaluated by M0.1.** Reported as `null`. |

Findings are classified:

- **Error** — blocks progress: missing file, checksum mismatch, dangling
  reference, unsafe stored path, rejected asset still present, document
  belonging to another project.
- **Warning** — demands attention but does not block: unresolved or restricted
  rights, draft narration, a claim without a source, a duration outside the
  format band, an invalidated approval.

Unresolved rights are a warning for rendering and a blocker for publication —
which is precisely why those two states are not the same field. A technical
render is never evidence that something may be published.

---

## 9. Path safety

Every path stored in a manifest is project-relative, POSIX-separated, and passes
`validation/paths.py`:

- `validate_project_relative_path` rejects absolute paths (POSIX *and* Windows,
  including drive-relative `C:data` and UNC), `..`, `.`, empty components
  (leading, trailing or doubled `/`), surrounding whitespace, and characters
  unsafe on either filesystem family. It splits the raw string rather than
  reading `PurePosixPath.parts`, because the latter silently normalises away the
  very components being screened for.
- `validate_filename` additionally requires a single segment, for
  operator-supplied destination names.
- `resolve_within` compares **fully resolved** paths, so a symlink inside a
  project that points outside it is refused.

---

## 10. Rendering boundaries

M0.1's `rendering/compose.py` renderer remains, unchanged, driving the M0.1
demonstration. M0.2 adds a real composition engine alongside it.

### What the M0.2 engine does

Still images **and** video sources, trimmed and frame-rate normalised; three
non-distorting fit modes plus explicit crop; two scaling kernels; deterministic
pan and zoom over stills; seven overlay primitives with safe-area placement;
four independent audio buses with automation ducking and two-pass loudness
normalisation; SRT import, export and burn-in; cuts, fades to and from black,
and within-scene crossfades; two output profiles; content-addressed per-scene
and per-assembly caching; advisory locking and journalled writes.

### What it deliberately does not do

- **Cross-scene crossfades.** They would couple adjacent scenes and break
  per-scene caching (D-018).
- **Retiming.** Shots render at 1:1 speed. There is no slow motion, no speed
  ramp, and `SourceTrim` carries only a start offset, because modelling a
  separate source duration would imply support that does not exist.
- **Motion on video sources.** Rejected by validation; moving an
  already-moving source is out of scope.
- **Vertical shorts.** Out of scope.
- **A general effects framework.** The transition and motion sets are closed by
  design.
- **Caption generation.** Externally supplied SRT only; no transcription, no
  speech alignment.
- **Text-to-speech.** Narration is supplied, or a synthetic tone in fixtures.

### Why scenes render independently

A scene is the unit of caching, so scene *N*'s output must not depend on
*N−1*'s. That single constraint explains several design choices that would
otherwise look arbitrary: shot spans are scene-relative; audio is mixed once at
assembly rather than per scene, because narration deliberately crosses scene
boundaries; and cross-scene crossfades are absent.

Scene intermediates are video-only and encoded with the profile's exact
settings, so assembly concatenates them by **stream copy** rather than
re-encoding. That is what makes a warm render fast.

### Subprocess discipline

Unchanged from M0.1 and extended: every FFmpeg call goes through `run_tool`
with an explicit `list[str]`. `shell=True` appears nowhere. Two further
invariants hold across the composition engine, each pinned by a test that is
also shown catching a planted violation:

1. **No filesystem path ever enters a filter string.** Sources and overlay
   images are `-i` arguments. `FFmpegPlan.leaked_paths()` checks this.
2. **No operator text ever enters a filter string.** Text is already pixels
   (D-016); colours are resolved to `0xRRGGBB` literals; filter expressions
   contain only numbers, arithmetic and fixed keywords, and avoid commas so
   there is no argument escaping at all.

## 11. CLI architecture

- One Typer app. M0.1 sub-apps: `asset`, `rights`, `demo`. M0.2 adds
  `timeline`, `cache` and `reference` sub-apps plus top-level `render` and
  `inspect`, defined in `cli_render.py` so `cli.py` stays navigable.
- Every command: resolve workspace → load channel/project → call a library
  function → format output. Logic that a test might want to call directly lives
  in the library, not in the command.
- Every command supports `--json`, so behaviour is machine-checkable.
- Errors propagate as `PolymationError` subclasses; `main()` prints the message
  to stderr and exits with the exception's own `exit_code`.

`main()` runs the app with `standalone_mode=False` so library exceptions reach
that handler. In that mode Click *returns* the code from a `typer.Exit` rather
than propagating it, so the return value is honoured as well as the exception.

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unclassified error |
| 2 | CLI usage error (reserved by Click) |
| 3 | Validation failed, or output deviates from spec |
| 4 | Required dependency missing |
| 5 | Project not found |
| 6 | Conflict |
| 7 | Unsafe input |

---

## 12. Future extension points

Where later milestones are expected to attach. Nothing below is implemented.

| Milestone | Extension point |
|---|---|
| M0.3 templates | `rendering/overlays.py` already consumes `ChannelBrand` and a `TextStyle`; a template layer would compose overlays from brand data rather than per-project styling. `FALLBACK_FONT_FAMILIES` is where an acquired brand font would land. |
| M0.3 typography | `resolve_font` reports substitution; installing licensed brand fonts changes behaviour with no code change, and invalidates every scene cache automatically because the font checksum is in the key. |
| M0.4 pilot | The engine is the deliverable it exercises. `ShortSourcePolicy` and `validate_timeline`'s warnings are the places real material will press first. |
| M1 research | `ScriptDocument.claims` (`FactualClaim`, with `source` and `verification`). |
| M2 script/storyboard generation | `compile_timeline` is the seam: anything that produces a valid storyboard gets a timeline for free. `NarrationStatus` already separates draft from approved. |
| M3 narration | `AudioRole.NARRATION` and the audio bus system exist; a TTS integration would import audio and place segments. `Ducking` is already configurable per project. |
| M3 asset generation | `RightsRecord.ai_generated` exists; no generator does. |
| M5 packaging / QA | `ApprovalStage` includes `RENDER` and `PUBLICATION`; `ApprovalLedger.has_valid_approval` is the gate a QA step would call. `inspect_output` and `measure_loudness` are the QA primitives. |
| Retiming / effects | `CompositionShot` would need a source duration separate from its span, and `MotionKind`/`TransitionKind` are closed enums ready to widen. Both are deliberately absent (§10). |
| Cross-scene transitions | Would require the cache unit to become a scene *group*. See D-018 for why that was not done. |
| Cross-project analytics | `polymation list` reads manifests directly. A derived read-only index could be added if scale ever justifies it (D-004). |

The data contracts that already exist were required by M0.1 or M0.2 in their own
right. None of them is speculative scaffolding for a later milestone.
