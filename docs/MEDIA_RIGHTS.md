# Media rights

**This document is not legal advice.** It is the project's operating policy and
a description of what the software enforces. Rights decisions belong to the
project owner, who may need actual legal advice for some of them.

---

## 1. The governing rule

> **A rights basis is a claim about where material came from. It is never
> permission to use it.**

Everything else follows from that sentence. The software records provenance so a
human can decide; it never decides.

A second rule, equally load-bearing:

> **The software makes no automated copyright determination — ever.**

There is no code path that infers fair use, judges a licence's applicability, or
concludes that material is safe to publish. Automated copyright determination is
explicitly out of scope, and should remain so beyond M0.1.

---

## 2. The two fields

Provenance and permission are separate fields on `RightsRecord`, so no caller
can read one as the other.

### `RightsBasis` — where it came from

| Basis | Meaning | Can reach `approved`? |
|---|---|---|
| `original_work` | Created by or for Polymation | Yes, after human review |
| `licensed` | Covered by a licence we hold | Only with a recorded `ApprovalRecord` **and** a `license_identifier` |
| `public_domain` | Believed out of copyright | Yes, after human review |
| `user_supplied` | Supplied by the operator, provenance unverified | Only with a recorded `ApprovalRecord` |
| `proposed_fair_use` | An *argument* for fair use, not a conclusion | Only with a recorded `ApprovalRecord` |
| `unknown` | No information | **Never** |

### `ReviewStatus` — what a human decided

| Status | Meaning | Publishable |
|---|---|---|
| `unreviewed` | No human has looked at it | No |
| `approved` | A human cleared it for use | Yes |
| `restricted` | Usable only under stated conditions | Not without honouring `usage_restrictions` |
| `rejected` | A human refused it | No — and it should be removed from the project |

`unreviewed` is **unresolved**. `restricted` is **resolved but not cleared** — a
decision was made, and it was conditional.

---

## 3. What the code enforces

These are model-level invariants in `src/polymation/schemas/rights.py`. An
invalid combination cannot be constructed, so it cannot be persisted.

1. **`unknown` can never be `approved`.** Unknown rights are an unanswered
   question. Approving one would be a fabrication.
2. **`user_supplied`, `proposed_fair_use` and `licensed` require an attached
   `ApprovalRecord`** before `review_status` may be `approved`. A bare status
   change is rejected.
3. **`licensed` requires a `license_identifier`.** A licence you cannot name is
   not a licence you can rely on.
4. **`attribution_required` requires `attribution_text`.** An attribution
   obligation with no attribution to give is a trap.
5. **Every import produces `unreviewed`** — whatever basis is claimed, including
   for fixtures this project generates itself. Nothing self-approves.
6. **Every asset must carry a rights record.** An asset without one is a
   validation *error*, not a warning.
7. **An asset with no rights record is reported as unresolved**, never as
   cleared. A missing record is an absence of information.
8. **`rejected` material still present in a project is a validation error.**

### Consequence, stated plainly

The technical demonstration — whose assets this repository's own code generates
— renders with four unresolved-rights warnings. That is correct. We built those
files, and a human still has not reviewed them. The alternative would be
software that learns to approve its own work.

---

## 4. Approval and invalidation

An `ApprovalRecord` binds a human decision to **the exact bytes reviewed**, via
`artifact_checksum`. `ApprovalRecord.covers()` returns true only if the decision
was `approved`, the artefact reference matches, and the checksum matches.

So when an approved artefact changes, its hash changes and the approval stops
applying. `polymation validate` reports this as `approval_invalidated`. The same
mechanism catches a modified asset file: `asset_checksum_mismatch` means the
bytes are no longer the bytes anyone approved.

**M0.1 limitation, stated openly:** the platform stores and verifies approval
records but provides no command to *create* one. Approvals must currently be
added by editing `approvals.json` and `rights.json` by hand. That gap is real.
It was left rather than filled with an auto-approve shortcut, because a
convenient approval path is exactly the wrong thing to build first.

---

## 5. Operating rules for material

### Prefer original work

Original graphics, diagrams, typography, animation and music are the default.
They are unambiguous, reusable, and make the channel's visual identity its own.
`src/polymation/rendering/fixtures.py` generates all demonstration assets from
code for this reason.

### Never add third-party material to this repository

Not game footage or screenshots, not anime footage or stills, not commercial
music, not downloaded artwork, not downloaded fonts, not stock media. This
applies to fixtures, tests, documentation and examples. `.gitignore` excludes
generated media, but the rule is about intent, not configuration.

### Game and anime footage

Treat as copyrighted unless proven otherwise. Any editorial use requires:

- a documented source and rights holder;
- a specific, written editorial justification — what the clip demonstrates and
  why nothing original would do;
- a recorded human review as `proposed_fair_use` or `licensed`;
- the shortest excerpt that makes the point.

"Other channels do this" is not a basis. Neither is "it's only a few seconds".

### Music

No commercial music. Original composition, properly licensed library music with
a recorded `license_identifier`, or silence.

### User-supplied material

Material the operator hands to the tool is `user_supplied` and **never
auto-approved**, even when the operator believes they own it. Belief is not
provenance; the review step is where it gets written down.

### Fonts

Check availability and licence before use. The brand's proposed families are
*proposed*, not acquired. The tooling reports substitution loudly rather than
implying brand fidelity — see `docs/DECISIONS.md` D-009.

### AI-generated material

Flagged with `ai_generated=True`. Its rights position is unsettled in several
jurisdictions, so it is reviewed like any third-party material, and nothing
about the flag shortens that review. No AI generation is implemented in M0.1.

---

## 6. Reporting

```bash
polymation rights report PROJECT_ID          # grouped by review status
polymation rights report PROJECT_ID --json   # machine-readable
polymation validate PROJECT_ID               # rights findings among the rest
```

`blocks_publication` is true whenever anything is unresolved, restricted or
rejected. It is an **input** to a human decision, not a clearance determination —
the report says so in its own output.

Publication readiness is deliberately reported as `null` by M0.1. No publishing
functionality exists, and none may be added without the owner's explicit
authorization.

---

## 7. Pre-publication checklist (for the human)

Before any video leaves the machine:

- [ ] `polymation rights report` shows every asset `approved`.
- [ ] Every `approved` record for third-party material has an `ApprovalRecord`
      with a real approver and a checksum matching the current file.
- [ ] Every `attribution_required` asset is actually attributed on screen or in
      the description, correctly.
- [ ] No `restricted` asset is used in a way its `usage_restrictions` forbid.
- [ ] No `rejected` or `unknown` material remains in the project.
- [ ] `polymation validate --strict` is clean.
- [ ] Fonts and music are licensed for the actual use.
- [ ] The owner — not the software — has decided this is publishable.
