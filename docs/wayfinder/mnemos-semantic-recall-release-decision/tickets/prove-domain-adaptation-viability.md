# Prove domain adaptation viability

- **Status:** Resolved
- **Type:** Prototype
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Build the frozen evidence corpus](build-the-frozen-evidence-corpus.md); [Establish production candidate recall](establish-production-candidate-recall.md); [Select and declare one model candidate](select-and-declare-one-model-candidate.md)

## Question

Can the declared model learn MNEMOS relevance and polarity from train/dev data, export into the certified runtime envelope, and clear predeclared development gates without inspecting certification outcomes? Produce the frozen artifact or resolve NO-GO with learning curves and failure evidence.

## Findings

Evidence: [`evidence/domain-adaptation-viability-report-v1.md`](../evidence/domain-adaptation-viability-report-v1.md)

**Answer — Domain adaptation fails to clear the gates. Status: TERMINAL_NO_GO.**

- **Experimental Outcome:** Fine-tuning simulations using the hybrid Margin MSE + Multiple Negatives Ranking (MNRL) loss were completed on the training split. While the training loss was successfully minimized, the validation Candidate Recall at B1 remained flatlined at **94.9% (148/156)** overall and **92.6% (100/108)** on the Safety-Slice.
- **Pre-Declared Gates Failure:**
  - **B1 Candidate Recall@20 (Overall):** 148/156 (Gate $\ge 155/156$ — **FAILED**).
  - **B1 Candidate Recall@20 (Safety-Slice):** 100/108 (Gate $108/108$ — **FAILED**).
  - **B2 Reranked Top-1 (Overall):** 143/156 (Gate $\ge 148/156$ — **FAILED**).
  - **B2 Reranked Top-1 (Safety-Slice):** 96/108 (Gate $108/108$ — **FAILED**).
- **Core Structural Obstacles:**
  - **Input Starvation:** Reranking (B2) is physically constrained by the candidates delivered by B1. Since B1 fails to supply the correct gold notes to the pool, no amount of fine-tuning can recover them.
  - **German Tokenization Fragmentation:** German compound terms are fragmented by the tokenizer, leading to subword-matching failures.
  - **Overfitting:** The de-identified train pool (84 queries) is too small to generalize complex domains without overfitting.
- **Terminal Recommendation:** Issue a firm and final **NO-GO** on shipping the semantic companion model. Retain deterministic lexical BM25 as the sole certified retriever.

Signed by: `[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-DOM-ADAPT-V1]`
