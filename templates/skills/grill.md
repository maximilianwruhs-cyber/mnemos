---
name: grill
description: Interrogate a vague request until it is unambiguous, then restate it as a spec. Use when the user runs /grill, or hands over a request whose scope, success criteria, or constraints are genuinely underdetermined.
---

# /grill — Ambiguity Interrogation

Ported from the pattern in `mattpocock/skills` (`/grill-me`), adapted to this runtime.
Source: https://github.com/mattpocock/skills — reimplemented, not copied.

## When this overrides the default

USER.md forbids opening with clarifying questions. This skill is the sanctioned
exception: the operator invoked it, so questioning **is** the deliverable.
Never invoke it implicitly to dodge work.

## Procedure

1. **Restate first.** One paragraph: what you believe is being asked, and the
   deliverable you would produce if forced to ship right now. This is the baseline —
   every question must be justified by a way the baseline could be wrong.

2. **Enumerate the unknowns.** Silently list every underdetermined dimension:
   - Scope — what is explicitly out?
   - Audience and register
   - Success criteria — what makes this correct vs. merely complete?
   - Constraints — format, length, tooling, data available in-store
   - Prior art — does something in the file store already solve part of this?

3. **Cut ruthlessly.** Discard any question that:
   - you can answer from context, USER.md, MEMORY.md, or the file store
   - has an obvious default you could state as an assumption instead
   - only changes cosmetics

4. **Ask, batched.** Maximum 5 surviving questions, delivered in one numbered pass.
   Each question carries your recommended answer, so the operator can reply "all defaults".
   Use `request_user_decision` when the choices are discrete; plain text otherwise.

5. **Emit the spec.** After the answers land, write a short contract before doing any work:
   - Objective (one sentence)
   - In scope / out of scope
   - Acceptance criteria (checkable, not vibes)
   - Stated assumptions for anything still unanswered

6. **Hand off.** If the work is non-trivial, chain into `/plan`. Otherwise execute directly.

## Failure modes

- Asking questions whose answers are already in the loaded context — inexcusable.
- Socratic drip-feed. One batch, not a conversation.
- Producing a spec longer than the deliverable it describes.
