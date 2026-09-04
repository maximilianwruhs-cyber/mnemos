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
- [Define the certification corpus contract](tickets/define-the-certification-corpus-contract.md): pin the approved principles as `corpus-v2/CONTRACT.md`, enforced by `scripts/corpus_lint.py` and proven by `scripts/test_corpus_lint.py` — normalized note-pool with gold-ID query records superseding the v1 smoke fixture, synthetic-only de-identification, a >= 0.60 cross-scenario leakage bar, a canonical hashed manifest, and a sealed one-shot certification loader.
- [Confirm model and runtime redistribution viability](tickets/confirm-model-and-runtime-redistribution-viability.md): the ONNX runtime, `tokenizers`, and numpy are the optional runtime baseline outside the stdlib-only core; the `<= 150 MB` footprint budget counts model weights + tokenizer only (design §7 updated, Option 2). Carry `mmarco-mMiniLMv2-L12-H384-v1` (apache-2.0, ships x86-64 int8 ONNX, 135.7 MB) as the single model candidate; `jina-reranker-v2` (non-commercial) and the `>150 MB` int8 models (`gte`/`mDeBERTa`/`bge`) are ruled out.

## Not yet specified

- Exact candidate-union policy and whether K=20 is sufficient on representative data.
- Exact base model, training objective, sampling policy, and quantization format.
- Confidence signal and calibration method for abstention.
- Final offline wheel layout for the certified companion.
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
