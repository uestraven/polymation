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
| Image fixtures | Pillow |
| Tests | pytest |
| Lint / format | Ruff |
| Storage | local filesystem, versioned JSON manifests |
| CI | GitHub Actions |

Do **not** introduce without a recorded decision in `docs/DECISIONS.md` and
owner approval: a database, a web server, a GUI, containers, distributed
processing, cloud infrastructure, or any paid dependency.

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
  guarded by `ffmpeg_required`. A skip is honest; it is not a pass.
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
- Keep `docs/DEVELOPMENT_STATUS.md` and `docs/ACCEPTANCE_CRITERIA.md` truthful
  and current as part of the change that affects them.

## 10. Repository hygiene

- Generated media, local projects, credentials, caches and rendered video stay
  out of version control. Check `.gitignore` before adding a path.
- Do not overwrite or delete unrelated work. Do not create a public remote or
  push to an external service without explicit authorization.
- One logical change per commit, with a message that explains the reasoning.
