# Establish production candidate recall

- **Status:** Resolved
- **Type:** Prototype
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Build the frozen evidence corpus](build-the-frozen-evidence-corpus.md)

## Question

On the full evidence note pool, what bounded union of deterministic BM25/graph/recency/salience evidence and Potion similarity produces at most 20 candidates while meeting >=99% overall gold Recall@20, 100% safety-slice recall, and exact-token protection? If no policy passes, what candidate-stage limitation causes the semantic route to stop?

## Findings

Evidence: [`evidence/semantic-baseline-v1.md`](../evidence/semantic-baseline-v1.md)

**Answer — the baseline candidate policy failed to clear its gates. Status: CANDIDATE_NO_GO.**

- **Train Phase:** Evaluated a grid of 14 candidate union policies. Potion retrieval (BM25 + 20-zero or 16-4 candidate slots) was checked. `unicode_nfc-4-16` / `unicode_nfc-0-20` reached 83/84 recall, but the strictly lexical `current_ascii-20-0` policy was rejected because its true deterministic recall was **71/84** overall and **59/66** safety (did not clear the error-free requirement).
- **Selected Policy:** **`current_ascii-16-4`** (16 lexical slots, 4 semantic slots, `current_ascii` tokenizer) was chosen and frozen based on Train metrics (83/84 overall, 66/66 safety, 6/6 identifier).
- **Dev Phase Gate Failure:** Evaluating `current_ascii-16-4` on the dev split yielded:
  - Overall B1 Recall@20: **148/156 = 94.9%** (Gate: **>= 155/156**, FAILED).
  - Safety Recall@20: **100/108 = 92.6%** (Gate: **108/108**, FAILED).
  - Identifier Recall@20: **12/12 = 100%** (Gate: **12/12**, PASSED).
- **Abstention (B2):** B2 zero-shot reranking was **not run** because the candidate retrieval pool did not clear the B1 candidate gates.
- **Root Cause & Limits:** The candidate stage fails primarily on German compound-word queries in natural phrasing (`q-webhook-retry-de-1`, `q-canary-promote-de-0`, etc.) and complex domain patterns. Since candidate generation fails to deliver gold notes to the top 20, semantic reranking is structurally starved of the correct target notes at the input boundary.
- **Result:** This results in a firm baseline **NO-GO** for the current zero-shot lexical/semantic union. This evidence feeds directly into upstream domain-adaptation decision gates to see if fine-tuning is required or if the semantic route is structurally blocked.
