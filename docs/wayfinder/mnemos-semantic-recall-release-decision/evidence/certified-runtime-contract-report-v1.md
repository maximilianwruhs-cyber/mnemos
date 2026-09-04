# Certified Runtime Contract Specification

**Status:** `TERMINAL_NO_GO_CONTRACT`  
**Date:** 2026-09-04  
**Signee:** MNEMOS Release Wayfinder Agent  

---

## 1. Executive Summary

Given the terminal **`TERMINAL_NO_GO`** and **`OBZOLETE_FALLBACK_FREEZE`** resolutions, the deep runtime interface and state machine are finalized as a **strict fallback-to-lexical contract**. 

The runtime contract is configured to ensure that all semantic scoring is kept entirely inert. No ONNX model inference is loaded, no vector matrices are allocated, and no raw-vector final scoring is performed. Standard lexical BM25 matching serves as the single certified production retriever.

---

## 2. API Lifecycle and State Machine

The approved `probe()` and `decide()` boundary functions are specified to act as pass-through wrappers that immediately bypass semantic evaluation:

### `probe(query: str, note_pool: list[Note]) -> ProbeStatus`
- **Behavior:** Immediately returns `ProbeStatus.LEXICAL_ONLY` or equivalent fallback status.
- **ONNX Session Loader:** Never invoked (keeps the model file-system access completely inert).
- **Latency:** $\approx 0.0\ \text{ms}$ overhead.

### `decide(query: str, candidates: list[Note]) -> DecideResult`
- **Behavior:** Immediately bypasses semantic scoring and returns the raw BM25 candidates list sorted deterministically by lexical overlap, score-ties broken alphabetically by `note_id`.
- **Confidence Signal margin:** Bypassed (as threshold $\tau = \infty$ forces instant fallback).
- **Observability:** Logs a single, silent, telemetry-safe internal event: `semantic_recall_bypassed_lexical_fallback`. Text-based logging of query or note contents is strictly prohibited to prevent data leakage.

---

## 3. Structural Invariant Protection

1. **Elimination of Raw-Vector Scoring:**
   All temporary or experimental static-vector scoring mechanisms are completely removed from production paths. No float matrices are processed at runtime.
2. **Deterministic Tie-Breaking:**
   To guarantee byte-identical outputs across environments, all score ties are resolved lexicographically using the canonical UTF-8 byte order of the `note_id`.
3. **Failure Mapping:**
   Any attempt to force-initialize semantic recall when model files or baseline dependencies are missing will fail-safe gracefully, raising no user-visible errors and falling back to lexical matching.

---

Signed on behalf of MNEMOS core:  
`[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-RUNTIME-CONTRACT]`
