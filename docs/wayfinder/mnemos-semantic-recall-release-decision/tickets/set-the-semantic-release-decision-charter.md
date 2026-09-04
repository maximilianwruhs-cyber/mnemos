# Set the semantic release-decision charter

- **Status:** Closed
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** Main
- **Blocked by:** None

## Question

What destination, constraints, progression strategy, evidence standard, and conditional runtime boundary should govern the MNEMOS semantic-recall effort without presuming model adaptation succeeds?

## Resolution

The destination is an evidence-backed GO/NO-GO release decision. Use a gate-first evidence ladder; a de-identified hybrid corpus; Linux x86-64 CPython 3.12 offline certification; safety-first abstention; and an optional installed footprint no larger than 150 MB. Freeze a full-pool certification corpus and numeric gates before declaring one model candidate. Potion is candidate evidence only. A future certified runtime exposes `probe()` and `decide()` and activates only by explicit operator flag plus matching certification manifest. Failure retains deterministic ranking and ships no dormant model scaffolding.

Approved section by section by the operator on 2026-09-04. Full contract: [`MNEMOS Semantic Recall Release-Decision Wayfinding Design`](../../../superpowers/specs/2026-09-04-mnemos-semantic-recall-release-decision-design.md).
