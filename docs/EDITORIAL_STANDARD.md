# Editorial standard

What a Polymation video must be, and what the software must therefore support.
This is the reference `docs/PROJECT_PLAN.md` M0.4 reviews a pilot against.

---

## 1. Remit

Polymation covers **classic Japanese role-playing games and retro anime**,
primarily **1990–2009**, looking for the shared creative DNA between them:
storytelling, world-building, character development, visual design, animation
technique, soundtrack craft, and the historical and industrial context that
produced them.

The channel's subject is **craft**, not trivia. A video should leave a viewer
understanding *how* something was made or *why* it works, not merely that it
exists.

| Format | Duration | Purpose |
|---|---|---|
| Standard | 120–300 s | A complete argument with evidence |
| Short | 30–60 s | One hook, one idea, one payoff |

Enforced as a warning by `polymation validate`, not a hard error — an
out-of-band duration is an editorial judgement, not a corrupt file.

---

## 2. Principles

**Curiosity.** Start from a real question, not a ranking. "Why does Chrono
Trigger's world feel continuous?" is a video. "Top 10 SNES RPGs" is not.

**Accuracy.** Every factual claim is sourced, and a claim and its source are
stored together (`FactualClaim`). Where the record is genuinely contested, say
so on screen rather than picking the convenient version. Prefer primary sources:
developer interviews, production materials, contemporaneous press. Wiki text and
fan-compiled trivia are leads, not citations.

**Brevity.** Two to five minutes forces a single argument. Cut anything that
does not serve it. Restating the premise is not development.

**Originality.** The analysis must be ours. Observations borrowed from existing
video essays or articles are either credited explicitly or cut. Reaching the
same conclusion as someone else independently is fine; reproducing their framing
is not.

**Nostalgia, used honestly.** Affection for the era is the reason the channel
exists. It is not an argument. "This was better" needs evidence about craft, not
a feeling about childhood.

**Meaningful creative analysis.** The standing test for any script: *does this
tell a viewer something they could not get from the box art and a plot summary?*
If not, it is not finished.

---

## 3. Content pillars

| Pillar | `content_pillar` | What it is |
|---|---|---|
| Story and World Deep Dives | `story_world_deep_dive` | How a specific work builds its world, characters or narrative structure |
| Design and Craft Breakdowns | `design_craft_breakdown` | Mechanics, art direction, animation technique, sound design, UI |
| Era Spotlights and Comparisons | `era_spotlight` | Cross-work and cross-medium patterns, industrial and historical context |
| Short-Form Hooks | `short_form_hook` | One sharp observation, 30–60 s |

Subjects: `jrpg`, `anime`, `crossover` (where the two genuinely intersect),
`industry_history`.

`crossover` requires an actual connection — shared staff, shared studio, a
documented influence, a technique that moved between media. Two works being
contemporaneous is not a crossover.

---

## 4. Structure

A standard video:

1. **Hook** (0–15 s) — the question, concretely. No channel intro before it.
2. **Framing** (15–45 s) — why the question is interesting, what will be shown.
3. **Development** (45 s – 4 min) — two to four beats, each with evidence.
4. **Payoff** (final 20–40 s) — the answer, and what it implies more broadly.

A short: hook in the first three seconds, one idea, one payoff. No preamble.

A narration scene is one beat. It may need several visual shots, or one shot may
span several beats — the schemas keep narration scenes and visual shots separate
precisely so the edit is not forced to match the script one-to-one.

---

## 5. Narration

- Written to be spoken. Read it aloud; if it trips, rewrite it.
- Plain language. A technical term earns its place by being defined once and
  then used.
- No manufactured hype, no rhetorical questions used as filler, no "but what if
  I told you".
- Japanese titles, names and terms are pronounced correctly. Every one gets a
  `PronunciationNote`. Where a work has both an original and a localised title,
  use the one the audience knows and give the other once.
- Narration is `draft` until a human approves it. AI-drafted narration, if ever
  used (M2), stays `draft` until then — approval is a human act.

---

## 6. Visuals

- Every shot earns its place. Stock footage that merely fills time is cut.
- Archival 4:3 material is **pillarboxed, never stretched**. The renderer's
  `contain` fit mode guarantees this, and the demonstration exists partly to
  prove it.
- On-screen text is legible at phone size: large enough, high enough contrast,
  on screen long enough to read twice.
- Comparisons are fair. Matched capture conditions, no cherry-picked worst case
  against a best case.
- Brand colours and typography come from `channels/polymation/brand.yaml`. If a
  brand font is unavailable, the tooling says so rather than implying fidelity.

---

## 7. Rights discipline

The editorial standard and the rights policy are not separable: a video that
cannot be shown is not finished. Full policy in `docs/MEDIA_RIGHTS.md`.

- Prefer original material: our own graphics, diagrams, typography, music.
- Third-party material requires a documented basis *and* a recorded human
  review. "Everyone else uses it" is not a basis.
- Credit where credit is required, visibly and accurately.
- The software never decides a copyright question. It records evidence so a
  human can.

---

## 8. Accuracy failures

When a published video is wrong:

1. Correct it in the description immediately.
2. If the error is load-bearing for the argument, pin a correction comment.
3. If the video's conclusion does not survive the correction, take it down.

Corrections are not an embarrassment; uncorrected errors are. Accuracy is a
principle, which means it outranks the publishing schedule.

---

## 9. What the channel will not make

- Ranked lists as a substitute for an argument.
- Reaction or commentary over someone else's video.
- Plot recaps with no analysis.
- Engagement-bait titles that the video does not deliver on.
- Anything assembled primarily from material we have no right to use.
- Mass-produced output. The goal is a reusable production engine, not volume.
