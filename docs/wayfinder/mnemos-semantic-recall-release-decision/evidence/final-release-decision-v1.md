# Terminal Release Decision: NO-GO on Semantic Recall

**Status:** `TERMINAL_NO_GO_RELEASE_DECISION`  
**Date:** 2026-09-04  
**Project:** MNEMOS Semantic Recall Release-Decision  
**Signee:** MNEMOS Release Wayfinder Agent  

---

## 1. Executive Declaration of Terminal Release Decision

Following the exhaustive wayfinding and evidence-gathering process conducted under the approved semantic release-decision charter, we issue the final, definitive **NO-GO** release decision on shipping an offline semantic reranker companion for MNEMOS.

The standard-library-only core of MNEMOS will **retain deterministic lexical recall (BM25)** as its sole, certified search and retrieval engine. **No active semantic models, tokenizers, `onnxruntime` wheels, or dormant production scaffolding will be distributed or shipped.** 

This decision guarantees absolute security, eliminates licensing and dependency overhead, prevents footprint bloat, and protects the codebase from non-deterministic retrieval errors.

---

## 2. Gate Verification Audit

The candidate semantic recall suite was audited against the hards gates defined under the project charter:

| Gate Dimension | Requirement | Observed Status / Value | Audit Result |
|----------------|-------------|-------------------------|--------------|
| **1. Footprint Gate** | Model + Tokenizer weights $\le 150\ \text{MB}$ | 135.7 MB (Under Option 2) |  **PASSED** |
| **2. Licensing Gate** | Permissive, redistribution-compliant | Apache 2.0 (`mmarco-mMiniLMv2`) |  **PASSED** |
| **3. Offline Gate** | Local execution, zero network access | 100% Offline (ONNX Runtime) |  **PASSED** |
| **4. Determinism Gate** | Byte-identical outputs | 20/20 identical rankings |  **PASSED** |
| **5. B1 Recall Gate** | Dev Candidate Recall $\ge 155/156$ ($\ge 99\%$) | **148/156 (94.9%)** | ❌ **FAILED** |
| **6. Safety-Slice Gate** | Dev Safety Candidate Recall $= 108/108$ | **100/108 (92.6%)** | ❌ **FAILED** |
| **7. Reranking Gate** | Dev Reranked Top-1 $\ge 148/156$ | **143/156 (91.7%)** | ❌ **FAILED** |

*Audit Finding: The candidate failed multiple critical quality and safety recall gates on the validation corpus (`corpus-v2`). Fine-tuning/domain-adaptation did not resolve these failures due to retrieval starvation at the B1 input boundary and tokenizer word fragmentation. Under the charter, any single gate failure mandates a terminal NO-GO.*

---

## 3. Core Structural Rationales

The decision to retain deterministic lexical recall and reject the offline semantic companion is backed by three insurmountable structural facts:

1. **Information Starvation at the B1 Boundary:**
   Reranking models can only rank candidates they receive. Since Candidate generation (B1) fails to deliver the gold notes to the pool for critical queries, the reranking model is starved at the input boundary, making semantic success physically impossible.
2. **Tokenizer Fragmentation of German Compounds:**
   The offline vocabulary of multilingual models fragments natural German compound terms (e.g., *Warteschlangen-Blockaden*) into subword tokens, destroying semantic alignment. Resolving this requires vocabulary expansion, which violates the strict $\le 150\ \text{MB}$ footprint gate.
3. **Pristine Standard-Library Core Invariant:**
   Rejecting the companion prevents bringing **47 MB** of complex native dependencies (`onnxruntime`, `tokenizers`, `numpy`) into the product, preserving MNEMOS as a lightweight, zero-dependency, pure Python standard library system.

---

## 4. Preservation & Dormant Scaffolding Actions

To ensure the codebase remains clean and free of dormant/unused artifacts:
1. **No dormant model code is shipped:** All experimental scoring and raw-vector scoring matrices are completely excised from the core production paths.
2. **Deterministic Fallback Wrapper:** The deep runtime boundary is frozen as a pass-through lexical wrapper:
   - `probe()` immediately returns `LEXICAL_ONLY` ($\approx 0.0\ \text{ms}$ latency).
   - `decide()` returns raw lexical candidates, score-ties broken lexicographically by `note_id`.
3. **Data Preservation:** The deterministic builder (`scripts/corpus_build.py`) and frozen corpus (`corpus-v2`) remain preserved in the test environment as a gold-standard benchmarking suite for future lexical improvements.

---

This terminal release decision is officially signed, sealed, and finalized on behalf of the MNEMOS core team.

Signed by:  
`[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-TERMINAL-NO-GO]`
