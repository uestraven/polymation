# Architectural decisions

Each entry records the decision, why it was taken, what else was considered, and
what it costs. Decisions are appended, not rewritten; a reversal gets a new
entry that supersedes the old one.

---

## D-001 — Versioned JSON manifests as the authoritative project state

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** A video project's state lives in readable JSON files inside its own
directory, each carrying an explicit `schema_version`. There is no database.

**Rationale.** Project state is edited by humans as much as by code — a brief, a
script, a rights note. JSON in a directory is inspectable with any editor,
diffable, trivially backed up, and survives the tooling that wrote it. The
milestone also forbids SQLite, and nothing about M0.1's access patterns needs
one: a handful of projects read whole.

**Alternatives considered.**
- *SQLite.* Better for cross-project queries and atomic multi-document writes.
  Rejected: forbidden for M0.1, and it makes manual inspection and hand-editing
  much worse for the dominant use case.
- *YAML for everything.* More pleasant to hand-write. Rejected for project data:
  YAML's implicit typing is a hazard for machine-written records. YAML is used
  only for channel configuration, which is hand-written and never written by
  code.

**Consequences.** Cross-project queries mean reading every manifest (fine at this
scale). Multi-document writes are not atomic as a set — each file is written
atomically, but a crash between two writes can leave documents inconsistent,
which the validator detects. A future index would have to be derived, never
authoritative.

---

## D-002 — Version-check stored documents before parsing them

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `read_document` calls `Model.assert_readable_version(payload)` on
the raw decoded dict before `model_validate`.

**Rationale.** A document written by a future build would otherwise fail as a
cascade of field errors that never mention the real cause. Checking the declared
version first yields one actionable sentence naming the found version, the
supported versions, and the remedy.

**Alternatives considered.** Relying on the `schema_version` field validator
alone. Rejected: it fires inside a `ValidationError` alongside unrelated field
errors, burying the signal.

**Consequences.** Two validation passes over the payload — negligible. Each
document class must declare `DOCUMENT_KIND` and `SUPPORTED_SCHEMA_VERSIONS`.

---

## D-003 — Project identifiers are assigned once, never derived from the title

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `project_id` is `YYYYMMDD-<slug≤40>-<6 hex>`, generated at creation
and persisted. The title's slug is stored separately and may change freely.

**Rationale.** The milestone requires identifiers stable across title changes.
Embedding the date and slug keeps directory names human-readable; the random
suffix prevents collisions between two projects created the same day from the
same title. Because the identifier is persisted rather than recomputed, renaming
a video cannot move or rename its project.

**Alternatives considered.**
- *UUID4.* Perfectly stable, completely unreadable in a directory listing.
- *Pure slug.* Readable, but collides, and invites re-derivation on rename —
  exactly the failure mode being avoided.

**Consequences.** Identifiers are long. Two identical titles on the same day
produce near-identical directory names distinguished only by suffix. The random
suffix makes identifiers non-reproducible, so tests pass an explicit
`--project-id` or `suffix`.

---

## D-004 — No database, and no cross-project index yet

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `polymation list` reads every manifest from disk. No index, no
cache.

**Rationale.** Correct by construction and impossible to leave stale. At the
expected scale (tens to low hundreds of projects) the cost is immeasurable.

**Alternatives considered.** A JSON index file. Rejected: a second source of
truth that can disagree with the manifests, for no benefit at this scale.

**Consequences.** Listing is O(projects) with a file read each. If that ever
matters, a *derived* index can be added — never an authoritative one.

---

## D-005 — Content-based media detection, with a stdlib WAV path before ffprobe

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** Format detection tries Pillow, then the standard library's `wave`
module, then ffprobe. File extensions are never consulted.

**Rationale.** The milestone forbids trusting extensions. Pillow and ffprobe
cover images and time-based media, but putting `wave` in between buys two real
things: uncompressed audio can be imported on a machine with no FFmpeg (so the
demonstration project can be *built* without it, and only *rendering* requires
it), and a WAV is described identically whether or not FFmpeg is installed,
which keeps test behaviour deterministic. `wave` parses the RIFF header, so this
is genuine content inspection, not an extension shortcut.

**Alternatives considered.**
- *ffprobe first for everything.* More uniform metadata, but makes every audio
  import depend on FFmpeg and makes results vary with the environment.
- *`python-magic` / libmagic.* Another dependency, and it identifies containers
  rather than decoding them — weaker evidence than a successful decode.

**Consequences.** Two code paths produce audio probes; both are covered by tests.
WAV metadata is slightly thinner than ffprobe's (no codec profile), which does
not matter for M0.1.

---

## D-006 — A rights basis is a provenance claim, never permission

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `RightsBasis` and `ReviewStatus` are separate fields. `unknown` can
never be `approved`. `user_supplied`, `proposed_fair_use` and `licensed` require
an attached `ApprovalRecord` before `approved`. Imports always produce
`unreviewed`, whatever basis is claimed — including for fixtures this project
generates itself.

**Rationale.** The most dangerous failure mode for this project is software that
appears to have cleared rights it has not cleared. Collapsing provenance and
permission into one field is exactly how that happens. Enforcing the rules in
the Pydantic model means an invalid combination cannot be constructed, let alone
persisted.

**Alternatives considered.**
- *A single `rights_status` field.* Simpler, and silently conflates "I made
  this" with "this may be published".
- *Auto-approving `original_work`.* Tempting for our own fixtures, and it would
  train the system to self-approve. Rejected: the demonstration therefore
  renders with four unresolved-rights warnings, which is the honest state.

**Consequences.** The demonstration project always emits rights warnings — by
design, and the CLI explains why. Approving anything requires a human action
that M0.1 provides no command for; records must currently be edited by hand.
That gap is documented rather than papered over with an auto-approve path.

---

## D-007 — Structural validity, render readiness and publication readiness are three fields

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `validate_project` answers structural validity; `assess_render_readiness`
answers renderability; publication readiness is reported as `null` with a note
saying M0.1 does not evaluate it.

**Rationale.** The milestone requires not equating a renderable project with a
publishable one. The only way to guarantee that is to make them different
fields, so no caller can accidentally read one as the other. Unresolved rights
are a *caution* for rendering and a *blocker* for publication — a single
"is it ready" boolean cannot express that.

**Alternatives considered.** A single readiness score or a severity threshold.
Rejected: it invites exactly the conflation being guarded against.

**Consequences.** Three states to explain in output and in docs. Callers must
choose which question they are asking, which is the point.

---

## D-008 — FFmpeg invoked only through explicit argument vectors

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** Every external tool call goes through `run_tool(binary, list[str])`.
`shell=True` appears nowhere; no command line is built by string interpolation.

**Rationale.** Filenames are untrusted input. A project title, an imported
filename or an operator-supplied destination name could contain shell
metacharacters. An argument vector makes that impossible to exploit, and the
guarantee is structural rather than dependent on escaping being correct
everywhere.

**Alternatives considered.** `shlex.quote` on a shell string. Rejected: it works
until someone forgets, and there is no reason to accept that risk.

**Consequences.** Filter graphs are built as strings (they must be), so they are
assembled only from validated numeric values and palette colours — never from
paths. A test asserts that a filename full of metacharacters passes through
uninterpreted.

---

## D-009 — Font substitution is reported, never silent

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `resolve_font` returns a `FontResolution` recording whether the
requested family was found. When it was not, the description says "NOT the brand
font", `polymation fonts` reports it, and `demo create` prints a warning.

**Rationale.** The milestone forbids silently substituting fonts while claiming
brand fidelity. The proposed families (Space Grotesk, IBM Plex Serif, Silkscreen)
are not installed on the development machine, so substitution is the normal case,
not an edge case — which makes loud reporting essential rather than decorative.

**Alternatives considered.**
- *Vendoring the fonts.* Would give real brand typography. Rejected for M0.1:
  adding font binaries is a licensing decision for the owner, and the milestone
  forbids adding third-party assets.
- *Failing when a brand font is missing.* Rejected: it would block the
  demonstration on a dependency the milestone does not require.

**Consequences.** The demonstration renders with Pillow's built-in face and says
so. Fixture text is ASCII-only, because the fallback face has no em-dash glyph
and a missing glyph renders as a tofu box.

---

## D-010 — PyYAML added for channel configuration

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** PyYAML is a runtime dependency, used only to load
`channels/<id>/*.yaml` via `yaml.safe_load`.

**Rationale.** The milestone's repository structure mandates `brand.yaml` and
`editorial.yaml`, and YAML is not in the standard library. PyYAML is the
de-facto standard, free, and has no transitive dependencies. `safe_load` never
constructs arbitrary Python objects.

**Alternatives considered.** Converting channel config to JSON and dropping the
dependency. Rejected: the required structure specifies YAML, and YAML's comments
genuinely matter for a hand-edited file that has to say "these values are
provisional".

**Consequences.** One dependency beyond the approved list, recorded here. Loaded
YAML is validated through Pydantic models, so a malformed file produces a clear
error rather than a confusing `KeyError` later.

---

## D-011 — `requires-python = ">=3.11"` while pinning 3.12

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `.python-version` pins 3.12, which CI installs and uses. The
package declares `requires-python = ">=3.11"`.

**Rationale.** 3.12 is the target. The development machine has 3.11.4, 3.14 and
3.15 available but not 3.12, and installing a new interpreter there was blocked
(see `docs/DEVELOPMENT_STATUS.md`). Setting the floor at 3.11 lets local
development proceed on the available interpreter while CI verifies the pinned
version. 3.11 is the correct floor because the code uses `datetime.UTC`, which
3.11 introduced.

**Alternatives considered.**
- *Hard-require 3.12.* Honest to the spec, and blocks all local work on
  something orthogonal to the milestone's goals.
- *Target 3.11 outright.* Rejected: the milestone asks for 3.12 where practical,
  and it is practical in CI.

**Consequences.** Local and CI runs use different interpreters, so local results
are not evidence about 3.12. `polymation doctor` reports this explicitly when
the running interpreter is not the pinned one. The two results are reported
separately in the implementation report.

---

## D-012 — `no_args_is_help=False` on the root CLI app

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** The root Typer app sets `no_args_is_help=False` and the root
callback prints help and exits 0 when no subcommand is given.

**Rationale.** Click 8.2 changed `no_args_is_help` to raise a usage error, so
bare `polymation` exited 2 — reporting a usage error for what is really a
successful help request, and varying by Click version. Handling it in the
callback makes the behaviour explicit and stable across the supported range.
`invoke_without_command=True` is required anyway so that `--version` works
without naming a subcommand.

**Alternatives considered.** Pinning Click. Rejected: pinning a transitive
dependency to work around a behaviour we can simply own.

**Consequences.** Sub-groups (`asset`, `rights`, `demo`) still use
`no_args_is_help=True` and exit 2 when invoked without a subcommand, which is
correct — naming a group without a command *is* a usage error.

---

## D-013 — `UnsafePathError` also inherits from `ValueError`

**Date:** 2026-11-25 · **Status:** accepted (M0.1)

**Decision.** `UnsafePathError(PolymationError, ValueError)`.

**Rationale.** Path validation is called from two places: directly by the
importer, and from inside Pydantic field validators that screen stored paths.
Pydantic only converts `ValueError` and `AssertionError` into `ValidationError`.
Without the `ValueError` base, a traversing path in a hand-edited manifest would
escape the model-validation boundary instead of being reported alongside other
field problems.

**Alternatives considered.** Catching and re-raising as `ValueError` in each
validator. Rejected: repetitive, and easy to forget in a new validator.

**Consequences.** A traversing path found while *loading* a document surfaces as
`DocumentDecodeError` (exit 3, a validation failure), whereas one supplied
*directly* by the operator surfaces as `UnsafePathError` (exit 7, unsafe input).
Both are correct for their context, and both are tested.

---

## D-014 — Pinned-Python validation runs in a container; containers are not a runtime requirement

**Date:** 2026-11-25 · **Status:** accepted (M0.2 baseline closure)

**Decision.** `scripts/verify-python312.sh` + `docker/verify.Dockerfile` run the
full verification suite — lockfile install, lint, format, all tests including
real-FFmpeg integration, and the rendering demonstration — under the
interpreter pinned in `.python-version`, inside a disposable container. The
host repository is mounted **read-only** and copied inside, so neither the host
working tree nor the host `.venv` is touched.

**Rationale.** M0.1 closed with a genuine gap: all local evidence came from
Python 3.11.4 rather than the pinned 3.12, because this host is Amazon Linux 2
with glibc 2.26 while published Pillow wheels target `manylinux_2_28` (D-011).
The independent review classified A03 as PARTIAL for that reason. Docker was
already installed with a reachable daemon, so an isolated environment was
available without any privileged host modification — no `sudo`, no system
package installation, no change to the host interpreter set.

The container also verifies something the host cannot: that the pin is
*correct*. The entrypoint compares the running interpreter against
`.python-version` and fails if they differ, so the check cannot silently
degrade into "some Python passed".

**Alternatives considered.**
- *Build Python 3.12 from source into a user prefix.* Workable, but it adds a
  bespoke interpreter to the host that nobody else can reproduce, and it would
  still need libjpeg/zlib headers to build Pillow from source.
- *`brew install python@3.12`.* The formula is not present in this Homebrew
  installation, and relying on Homebrew's own glibc makes the result specific
  to Homebrew hosts rather than to the pin.
- *Installing image codec headers so Pillow builds from source under 3.12.*
  Possible, but it validates a locally compiled Pillow rather than the wheel
  that `uv.lock` actually pins — weaker evidence, not stronger.
- *Lowering the pin to 3.11.* Rejected: the milestone forbids silently changing
  the Python requirement, and the pin is the deliberate target.

**Consequences.** Verification now has two tiers, and they are reported
separately: host (`scripts/verify.sh`, Python 3.11, FFmpeg 9.1) and pinned
(`scripts/verify-python312.sh`, Python 3.12, FFmpeg 7.1.5). Running the
pinned tier needs Docker, which is a *development* dependency only — the
application itself runs directly on any host with Python 3.11+ and FFmpeg, and
no container is involved in running or deploying it.

The two tiers use different FFmpeg builds, so encoded bytes differ between them
(356 122 vs 356 559 bytes for the same demonstration). That is expected: the
output *specification* is what is asserted, not a byte-for-byte hash across
encoder versions. Output remains byte-reproducible for a fixed FFmpeg build.

---

## D-015 — `timeline.json` is a new optional document, not a manifest migration

**Date:** 2026-11-25 · **Status:** accepted (M0.2)

**Decision.** The composition timeline is its own document with its own schema
version. `ManifestPaths` gained defaulted `timeline` and `cache_dir` fields.
The manifest's own `schema_version` stays `1.0`.

**Rationale.** Every field of `ManifestPaths` already had a default, which is
what makes adding one backward compatible: an M0.1 manifest that predates
`timeline` validates cleanly and picks up the default. A separate document was
right rather than extending the storyboard because the two change for different
reasons — the storyboard records creative intent, the timeline records
executable placement — and because a project with no timeline is still valid.

**Alternatives considered.**
- *Extend the storyboard with executable fields.* Rejected: it would force
  every editorial change through a schema that also carries encoder-facing
  detail, and conflate two meanings the milestone explicitly separates.
- *Bump the manifest schema version and write a migration.* Rejected as
  unnecessary: a defaulted additive field needs no migration, and a migration
  with nothing to migrate is dead code.

**Consequences.** M0.1 projects load unchanged, verified by a test that strips
the new keys from a manifest and reloads it. The reverse is not true: a manifest
written by M0.2 has keys M0.1's `extra="forbid"` would reject. That is accepted
— M0.1 is superseded — but it means downgrading is not supported.
`bundle.timeline` is `Optional`, so every consumer must handle `None`.

---

## D-016 — Text becomes pixels before FFmpeg sees it

**Date:** 2026-11-25 · **Status:** accepted (M0.2)

**Decision.** All overlay and caption text is wrapped, measured and drawn to an
RGBA PNG by Pillow. FFmpeg receives only the file's path, as one element of an
argument vector. `drawtext` is not used anywhere.

**Rationale.** The milestone requires that arbitrary approved text be handled
safely and not interpolated into filter expressions. `drawtext` needs escaping
for `:` `'` `\` `%` and more, and the rules differ between the filter-graph
parser and the filter's own argument parser. Getting that wrong on
operator-supplied text is a command-injection-shaped bug. Rendering to pixels
removes the class of bug rather than defending against it: there is no escaping
to get wrong, because there is no text in the filter string.

It also buys real capability — wrapping, mid-word breaking, per-line alignment,
a padded background box, and a line budget with an ellipsis — all of which
`drawtext` does poorly or not at all.

**Alternatives considered.**
- *`drawtext` with careful escaping.* Rejected on the reasoning above. It also
  cannot wrap text at all, which the milestone requires.
- *ASS/SSA subtitles for captions.* Capable, but it is another text format with
  its own escaping rules, and it would split caption rendering from overlay
  rendering for no benefit.

**Consequences.** One PNG per overlay per render, written to a scratch
directory — cheap, and the files are small because the box shrinks to its
content. Glyph coverage becomes a font problem rather than a filter problem:
the fallback chain covers Latin-1, Greek and Cyrillic, but **no installed font
covers CJK on this host**, so Japanese text renders as tofu. That is a real
limitation for a channel about Japanese media and is recorded in
`DEVELOPMENT_STATUS.md`.

---

## D-017 — Ducking is gain automation, not a sidechain compressor

**Date:** 2026-11-25 · **Status:** accepted (M0.2)

**Decision.** Music attenuation under narration is computed from the narration
segments' known integer sample spans and applied as a piecewise `volume`
envelope with linear attack and release ramps. `sidechaincompress` is not used.

**Rationale.** The narration positions are *already known exactly* — they are
timeline data, not something to be discovered from a signal. A compressor keyed
off the narration waveform makes the result depend on the narration's level and
dynamics, which means the same timeline ducks differently when the narration is
re-recorded slightly louder, and a test cannot assert where music should be
quiet without reasoning about the detector. Automation is reproducible and
directly verifiable.

Touching windows are merged before the envelope is built, so overlapping lead
times cannot double-attenuate.

**Alternatives considered.**
- *`sidechaincompress`.* More "natural" sounding and the conventional broadcast
  approach. Rejected for this milestone on reproducibility and testability; it
  is a reasonable M0.3+ option once there is real narration to tune against.
- *A fixed music level with no ducking.* Rejected: the milestone explicitly
  says not to assume a fixed background level is universally appropriate.

**Consequences.** The envelope is a long `volume` expression when there are many
narration segments — numeric only, so no escaping concern, but it does grow.
Ducking shape is linear rather than following a compressor's curve, which is
less sophisticated than a real mix engineer would want. The attenuation,
attack, release and lead times are all configurable.

---

## D-018 — Cross-scene crossfades are not supported

**Date:** 2026-11-25 · **Status:** accepted (M0.2)

**Decision.** `cut`, `fade_from_black` and `fade_to_black` apply within a shot;
`crossfade` applies only between shots **inside one scene**. A crossfade on a
scene's `transition_in`/`transition_out` is rejected by both the schema and the
validator.

**Rationale.** A scene is the unit of caching, and caching is only sound if a
scene renders independently. A cross-scene crossfade makes scene *N*'s output
depend on scene *N−1*'s, so editing one scene would invalidate its neighbour,
and the two could no longer be rendered or reused separately. The milestone
explicitly permits deferring this if it complicates independent scene
rendering — it does, fundamentally.

Fades to and from black happen *inside* the owning span, so they never change
the timeline duration. Within-scene crossfades do consume timeline, and the
overlap is accounted for explicitly in the shot spans rather than left implicit.

**Alternatives considered.**
- *Render overlapping scene pairs as a combined unit.* Would work, at the cost
  of a cache whose unit is sometimes a scene and sometimes a pair — much more
  complexity for a transition that is rarely the right editorial choice between
  two scenes anyway.
- *Cross-fade at assembly by re-encoding the join.* Defeats the stream-copy
  concatenation that makes warm renders fast.

**Consequences.** A documented limitation: scene-to-scene transitions are a cut
or a fade through black. Recorded in `DEVELOPMENT_STATUS.md` and
`COMPOSITION.md`.

---

## D-019 — Final assembly has its own cache

**Date:** 2026-11-25 · **Status:** accepted (M0.2)

**Decision.** A second cache under `.cache/assembly/<profile>/`, keyed on the
scene digests plus the audio mix, the loudness target and the caption text.

**Rationale.** Found by measurement, not by design. Scene caching made the
scene-rendering phase nearly free on a warm run, but a warm 180-second final
render still took 115 s — 112 s of it in assembly. The cause: burning in
captions changes pixels, so the concatenated video must be re-encoded, which
defeats the stream-copy path that assembly otherwise uses.

Assembly genuinely has dependencies that belong to no single scene — the audio
mix, the loudness target, the caption text — which is exactly the case the
milestone anticipates in allowing separate cache dependencies for global mixing
and final assembly. With the cache, warm assembly is 0.1 s and a warm render is
~1.2 s.

**Alternatives considered.**
- *Cache only the captioned video, re-mux audio each time.* Cheaper to store,
  but the audio path includes a two-pass loudness measurement that decodes the
  whole programme twice — tens of seconds, for no benefit when nothing changed.
- *Accept slow warm renders when captions are burned in.* Rejected: selective
  rendering is a headline goal of the milestone, and "fast unless you use
  captions" is not selective rendering.

**Consequences.** Cached output is stored once more on disk (about 20 MB for the
reference fixture at final quality), and a hit is a file copy to the
destination. Both caches are reported separately by `cache status`. Superseded
entries are not garbage-collected.
