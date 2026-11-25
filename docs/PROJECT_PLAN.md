# Project plan

**Only M0.1 is authorized.** Every milestone from M0.2 onward is *planned* and
subject to revision: scope, ordering and technology choices may all change once
the preceding milestone is reviewed. Nothing below M0.1 should be treated as a
commitment, and no agent may begin a later milestone without explicit
authorization from the project owner.

---

## M0.1 — Foundation and Pilot Workspace — **current**

Establish a clean, reproducible software foundation with a narrow vertical
slice through to rendering.

Delivers: repository and tooling; versioned Pydantic data contracts; project
initialization and manifest storage; media import with checksums, provenance and
rights records; project validation separating errors from warnings and
structural validity from render readiness; a 15-second original synthetic
rendering demonstration verified by ffprobe; CLI, tests, CI and documentation.

Explicitly not delivered: any content generation, any publishing, the real
rendering engine. See `docs/DEVELOPMENT_STATUS.md`.

---

## M0.2 — Core Video Composition and Rendering — *planned*

Turn the proof of concept into a usable renderer.

Anticipated: video-source shots alongside stills; transitions (cut, dissolve,
fade); motion (pan and zoom over stills); narration-track synchronisation so
shot timing follows audio; multi-track audio mixing with ducking; per-scene
render caching keyed on asset checksum plus shot fields; preview renders at
reduced resolution; deterministic output given identical inputs.

Open questions: whether to keep composing in one FFmpeg invocation or render
scenes separately and concatenate; how to represent a timeline that is no longer
a simple sequence.

---

## M0.3 — Branding and Visual Templates — *planned*

Production-quality visual language driven by configuration.

Anticipated: a template layer consuming `ChannelBrand`; title, lower-third,
quote, comparison and end cards; typography system with a resolved font
dependency (acquire properly licensed brand fonts, or choose families that are
reliably available); safe-area and legibility rules; caption styling.

Blocked on a decision the owner must make: the final brand palette, wordmark and
typefaces. Current values in `channels/polymation/` are provisional.

---

## M0.4 — Pilot Production and Creative Validation — *planned*

Produce one complete video end to end, by hand where necessary, to find out
whether the pipeline produces something worth watching.

Anticipated: a full human-written script and storyboard; genuinely cleared or
original visuals; a real narration recording; a finished render reviewed against
`docs/EDITORIAL_STANDARD.md`; a written retrospective on what the tooling got
wrong.

This is the milestone that validates the premise. Later milestones should not
start before it.

---

## M1 — Research and Topic Discovery — *planned*

Anticipated: topic backlog with editorial scoring; structured source capture
with citations; claim extraction into `FactualClaim` records; per-claim
verification status; a research brief that feeds the script stage.

Constraint that must survive into implementation: a claim without a source is
not usable, and verification is a human judgement.

---

## M2 — Script and Storyboard Generation — *planned*

Anticipated: assisted drafting of narration against the editorial standard;
shot-list proposal from narration; pronunciation note capture; revision history;
an explicit human approval gate before anything proceeds.

Requires an LLM dependency and therefore a cost decision the owner must make
first. AI-drafted narration must remain `draft` until a human approves it.

---

## M3 — Automated Narration and Asset Production — *planned*

Anticipated: text-to-speech narration with pronunciation overrides; original
graphic generation from templates; a rights-aware asset acquisition workflow.

Hard constraints to carry forward: every generated asset is marked
`ai_generated`; no acquisition path may auto-approve rights; no automated
copyright determination, ever.

---

## M4 — Integrated Production Pipeline — *planned*

Anticipated: one command driving research → script → storyboard → assets →
render, with checkpointing, resumability and approval gates that actually block.

---

## M5 — Packaging and Quality Assurance — *planned*

Anticipated: captions and transcripts; thumbnail generation; title and
description drafting; loudness normalisation; automated QA (legibility, audio
levels, duration conformance, rights completeness) as a release gate.

---

## M6 — Channel Launch and Audience Validation — *planned*

Anticipated: channel setup; a small batch of videos published **manually** by
the owner; audience and retention measurement; an editorial retrospective.

Deliberately manual. Publishing automation does not precede evidence that the
videos are worth publishing.

---

## M7 — Publishing and Analytics Automation — *planned*

Anticipated: YouTube API integration behind an explicit approval gate; scheduled
release; analytics ingestion; performance reporting against editorial decisions.

Requires: owner authorization, credential handling design, and a review of the
security posture that M0.1 deliberately avoided needing.

---

## M8 — Commercial Evaluation and Scaling — *planned*

Anticipated: cost-per-video accounting; throughput analysis; a decision on
whether multi-channel orchestration is justified; an honest assessment of
whether the engine should continue.

---

## Sequencing principles

1. **Creative validation gates automation.** M0.4 decides whether M1–M5 are
   worth building.
2. **No capability ships without its guard rails.** Rights tracking preceded
   asset import; approval invalidation preceded approval workflows.
3. **Paid dependencies require an explicit decision.** M2, M3 and M7 each need
   one. Expected cost through M0.4 is $0.
4. **A later milestone never pre-empts an earlier one.** No speculative
   scaffolding.
