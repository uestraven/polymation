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
