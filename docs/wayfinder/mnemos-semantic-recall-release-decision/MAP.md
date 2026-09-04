# MNEMOS Semantic Recall Release Decision

## Destination

Reach an evidence-backed release decision for MNEMOS semantic recall: either an approved implementation-ready specification for a certified offline retriever, or a documented NO-GO retaining deterministic lexical recall. Production reranker implementation is outside this map; one minimal safety correction may keep uncertified semantics inert while the map is worked.

## Notes

- Canonical design: [`MNEMOS Semantic Recall Release-Decision Wayfinding Design`](../../superpowers/specs/2026-09-04-mnemos-semantic-recall-release-decision-design.md).
- Certified target: Linux x86-64, CPython 3.12, CPU-only, offline, no daemon.
- Data boundary: de-identified hybrid; raw memories are neither exported nor bundled.
- Error policy: safety-first abstention; optional companion <=150 MB installed.
- The map is planning by default. Prototype and task tickets may create evidence needed for a decision. The truthful/inert safety correction is permitted; production reranker implementation remains outside the destination.
- Local Markdown tracker: each file under `tickets/` declares status, type, assignee, and blockers. The frontier is every `Open` ticket with `Assignee: Unassigned` whose blockers are all `Closed`.
- Refer to tickets by title, not filename.

## Decisions so far

- [Set the semantic release-decision charter](tickets/set-the-semantic-release-decision-charter.md): use a gate-first, one-candidate evidence ladder with frozen full-pool certification, safety-first abstention, explicit certified activation, and an honest GO/NO-GO terminal decision.

## Not yet specified

- Exact candidate-union policy and whether K=20 is sufficient on representative data.
- Exact base model, training objective, sampling policy, and quantization format.
- Confidence signal and calibration method for abstention.
- Final offline wheel layout and whether the installed 150 MB cap is feasible with the selected runtime.
- Exact runtime API details beyond the approved `probe()` and `decide()` boundary.

These items graduate into sharper tickets only when upstream evidence makes the question precise.

## Out of scope

- Production reranker implementation before GO; the minimal truthful/inert safety correction is the sole exception.
- Raw-memory export or inclusion in model/corpus artifacts.
- Network inference, background services, or a vector database.
- Core MNEMOS governance changes.
- Windows inference certification.
- Static-vector similarity as a final certified semantic score.
- A second model attempt under the same exposed certification set.
