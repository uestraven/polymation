# Standing instructions for coding agents — Polymation Studio

These instructions govern all automated work in this repository. They are not
suggestions; a change that violates them should not be proposed.

---

## 1. Scope control

- **Implement only the authorized milestone.** The current milestone is recorded
  in `docs/DEVELOPMENT_STATUS.md`. Do not start the next one without explicit
  authorization from the project owner.
- **Do not anticipate future milestones.** No speculative abstractions, no empty
  placeholder modules, no "we'll need this later" hooks. See
  `docs/PROJECT_PLAN.md` for what is deferred and why.
- **Do not expand scope silently.** If a task cannot be completed within the
  approved scope, say so and stop, rather than widening the change.
- The explicit out-of-scope list for M0.1 is in `docs/DEVELOPMENT_STATUS.md`.

## 2. Approved architecture

Use the stack in `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`:

| Concern | Technology |
|---|---|
| Language | Python 3.12 (floor 3.11) |
| CLI | Typer |
| Data contracts | Pydantic v2, with explicit `schema_version` |
| Dependencies | uv |
| Video / inspection | FFmpeg / ffprobe, via explicit argument lists |
| Text rendering | Pillow — **never** FFmpeg `drawtext` (D-016) |
| Image fixtures | Pillow |
| Tests | pytest |
| Lint / format | Ruff |
| Storage | local filesystem, versioned JSON manifests |
| CI | GitHub Actions |

Do **not** introduce without a recorded decision in `docs/DECISIONS.md` and
owner approval: a database, a web server, a GUI, containers, distributed
processing, cloud infrastructure, or any paid dependency.

## 2b. Composition engine invariants

Added in M0.2. Breaking any of these is a correctness or safety regression, not
a style question.

- **Integer frames are authoritative for video, integer samples for audio.**
  Never accumulate float seconds; over a three-minute video that drifts into a
  dropped frame and A/V desync. Convert at the FFmpeg boundary only.
- **A scene must render independently.** It is the unit of caching, so scene
  *N*'s output may not depend on *N−1*'s. This is why shot spans are
  scene-relative, why audio is mixed at assembly, and why cross-scene
  crossfades do not exist (D-018).
- **Never silently loop, freeze, stretch, trim or pad media.** Every such
  behaviour must be an explicitly requested policy or a validation error. A
  non-error short-source policy must preserve the shot's duration.
- **Cache keys are content, never timestamps.** A timestamp is not evidence
  about content. Any new input that can change rendered bytes must be added to
  the key, or the cache will serve stale frames.
- **Validate a cache entry before reusing it.** A render killed mid-write leaves
  a short file; serving it is silent corruption.
- **Never let a failed render replace a valid output.** Produce under a
  temporary name, verify with ffprobe, then move.
- **A scene-only render must say that assembly was not updated.** Silence there
  means an operator ships a stale video.
- **No distortion.** Never emit an unconstrained `scale=W:H`; every fit path
  uses `force_original_aspect_ratio`.

## 3. Code quality

- Every public function, class and module carries a docstring explaining *why*,
  not just what.
- Type-annotate everything. `from __future__ import annotations` at the top of
  each module.
- Business logic lives in library modules; `cli.py` only resolves inputs,
  delegates, and formats output.
- Raise a `PolymationError` subclass with an actionable message and a defined
  exit code. Never raise a bare `Exception`, and never swallow one.
- Error messages state what was wrong, what was expected, and what to do next.
- Named constants, not magic numbers. Channel-specific values (colours, fonts,
  durations) come from `channels/<id>/*.yaml`, never hardcoded in source.
- `ruff check` and `ruff format --check` must pass. Do not add `# noqa` to
  silence a finding without a comment explaining why the rule is wrong here.

## 4. Testing

- Every behavioural change needs a test that would fail without it.
- Tests use `tmp_path` and the `workspace` fixture. A test must never read or
  write the repository's own `projects/` directory.
- **No test may touch the network.** There is no mocking layer for network calls
  because there are no network calls.
- FFmpeg integration tests execute real FFmpeg, marked `@pytest.mark.ffmpeg` and
  guarded by `ffmpeg_required`. A skip is honest; it is not a pass. **Never
  replace rendering verification with a mock.**
- Assert on **decoded pixels and samples**, not on log output: extract a frame
  and check its colour, decode the audio and measure its envelope, count frames
  with `ffprobe -count_frames`.
- When a test guards an invariant, add a second test proving the guard can
  fail. A guard that cannot fail is not a guard — two of them in this repository
  silently passed everything until that second test was written.
- Do not write tests whose only purpose is to raise a coverage number. A test
  that asserts nothing meaningful is worse than no test.
- Report actual counts and actual results. Never estimate or project them.

## 5. Human approval gates

The project owner — not an agent — decides:

- architecture changes and new dependencies;
- all creative and editorial content;
- spending of any kind;
- media rights determinations;
- anything that publishes.

Approvals are recorded as `ApprovalRecord`s bound to an artefact checksum. When
the artefact changes, the approval stops applying. Do not write code that
approves on a human's behalf, infers approval, or treats a production stage as
evidence that a stage was approved.

## 6. Copyright safeguards

- **Never add copyrighted third-party material to this repository**, including
  game footage or screenshots, anime footage or stills, commercial music, and
  downloaded artwork, fonts or stock media.
- Fixtures are generated from code in `src/polymation/rendering/fixtures.py`.
  If a demonstration needs a new asset, generate it.
- A rights *basis* is a claim about provenance, never permission. Code must not
  auto-approve `user_supplied`, `proposed_fair_use` or `unknown` material.
  Unknown rights stay unresolved until a human resolves them.
- **Never make an automated copyright determination.** The platform records
  evidence so a human can decide.
- See `docs/MEDIA_RIGHTS.md`.

## 7. Credentials and security

- M0.1 requires no credentials. Do not add any.
- Never commit secrets. `.env` is git-ignored; `.env.example` holds only
  placeholders and comments.
- Never read a credential into a manifest, a log line or an error message.
- Invoke external tools with an explicit `list[str]` argument vector. Never
  build a shell command string, and never pass `shell=True`.
- **No filesystem path may enter an FFmpeg filter string.** Sources and overlay
  images are `-i` arguments. `FFmpegPlan.leaked_paths()` enforces this.
- **No operator-supplied text may enter an FFmpeg filter string.** Render text
  to pixels with `rendering/text.py` instead. `drawtext` needs escaping that
  differs between the graph parser and the filter, which on untrusted text is a
  command-injection-shaped bug.
- Build filter expressions from numbers, arithmetic and fixed keywords only, and
  avoid commas inside them so there is no argument escaping at all.
- Every stored path is project-relative and passes through
  `polymation.validation.paths`. Nothing may be read or written outside the
  project directory, including via symlink.
- Do not add a listening socket, published port or network client. If a future
  milestone requires one, that is an architecture decision for the owner.

## 8. No paid APIs, no publishing

- No paid service, API key or metered dependency may be introduced without the
  owner's explicit, recorded authorization. Expected external cost is **$0**.
- No publishing functionality of any kind: no YouTube API, no upload, no social
  posting, no analytics beacon. Not even behind a flag.

## 9. Accurate reporting

This section is as important as the code.

- **Never claim something works unless it was executed and observed.** "Rendering
  was validated" requires an actual render with actual ffprobe output.
- **Never claim CI passed unless a workflow run completed successfully.** Report
  local validation and remote CI validation separately, and label them.
- Report real test counts, real lint output and real timings. If a test was
  skipped, say which and why.
- **Disclose incomplete work.** If something is partially done, stubbed, or
  untested, state that plainly in the summary — not only in a doc.
- When a dependency is unavailable, document the blocker, give exact
  installation commands, and mark the dependent acceptance criteria as NOT
  EXECUTED rather than inferring that they would pass.
- Do not create fake integrations or placeholder functions that return plausible
  data. A missing capability must fail loudly.
- Keep `docs/DEVELOPMENT_STATUS.md`, `docs/COMPOSITION.md` and the acceptance
  matrices truthful and current as part of the change that affects them.
- **Measure before optimising.** The assembly cache exists because a benchmark
  showed warm renders spending 112s of 115s in assembly; do not add
  optimisations ahead of evidence.
- **Benchmark on a fresh workspace.** Reusing one has twice produced misleading
  numbers here: a warm render that looked slow, and a cache miss that looked
  like a hit.

## 10. Repository hygiene

- Generated media, local projects, credentials, caches and rendered video stay
  out of version control. Check `.gitignore` before adding a path.
- Do not overwrite or delete unrelated work. Do not create a public remote or
  push to an external service without explicit authorization.
- One logical change per commit, with a message that explains the reasoning.
