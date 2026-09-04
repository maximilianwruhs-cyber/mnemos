# Decide the certified runtime contract

- **Status:** Resolved
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Calibrate safe semantic abstention](calibrate-safe-semantic-abstention.md)

## Question

Given a viable frozen model and abstention rule, finalize the deep runtime interface and state machine: candidate records, protected hits, `probe()` statuses, `decide()` outcomes, deterministic tie-breaking, failure mapping, model/manifest compatibility, activation, observability without text logging, and the clean removal of raw-vector final scoring.

## Findings

Evidence: [`evidence/certified-runtime-contract-report-v1.md`](../evidence/certified-runtime-contract-report-v1.md)

**Answer — Strict fallback-to-lexical contract finalized. Status: TERMINAL_NO_GO_CONTRACT.**

- **Resolution:** The deep runtime contract has been finalized as a pure fallback wrapper, completely bypassing active ONNX inference:
  - **`probe()` status:** Returns `LEXICAL_ONLY` immediately, keeping ONNX sessions inert.
  - **`decide()` outcome:** Returns standard BM25 results with score ties broken alphabetically by `note_id`.
  - **Observability:** Emits a silent, telemetry-safe internal event: `semantic_recall_bypassed_lexical_fallback`. No query or note text is logged.
  - **Removal of Vector Scoring:** Raw-vector matrix calculations are completely removed from production paths, avoiding non-deterministic runtime errors.

Signed by: `[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-RUNTIME-CONTRACT]`
