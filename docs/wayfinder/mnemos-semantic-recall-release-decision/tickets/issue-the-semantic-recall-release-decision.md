# Issue the semantic-recall release decision

- **Status:** Closed
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Make current semantics truthful and inert](make-current-semantics-truthful-and-inert.md); [Decide the certified runtime contract](decide-the-certified-runtime-contract.md); [Decide the offline companion package](decide-the-offline-companion-package.md)

## Question

Do the frozen certification results satisfy every quality, safety, usefulness, determinism, offline, latency, and footprint gate? If yes, approve an implementation-ready runtime/package specification and GO evidence bundle. If not, record NO-GO, retain deterministic recall, name each failed gate, and ship no model or dormant production scaffolding.

## Findings

Evidence: [`evidence/final-release-decision-v1.md`](../evidence/final-release-decision-v1.md)

**Answer — Final, definitive NO-GO release decision issued. Status: CLOSED_TERMINAL_NO_GO.**

- **Decision:** Do **NOT** release or ship the offline semantic recall companion. Retain deterministic lexical recall (BM25) as the sole certified retriever.
- **Audit Findings:** The candidate model failed critical quality and safety gates on the development corpus:
  - **B1 Candidate Recall@20 (Overall):** 148/156 (Gate $\ge 155/156$ — **FAILED**).
  - **B1 Candidate Recall@20 (Safety-Slice):** 100/108 (Gate $108/108$ — **FAILED**).
  - **B2 Reranked Top-1 (Overall):** 143/156 (Gate $\ge 148/156$ — **FAILED**).
- **Core Rationales:**
  - **B1 Retrieval Starvation:** Missing gold notes at candidate generation cannot be recovered by reranking.
  - **German Tokenizer Fragmentation:** Subword tokenization destroys German compound-word semantics.
  - **No Dormant Scaffolding:** No active models or external runtime packages are loaded, preserving the stdlib-only core.
  - **Pass-through wrapper:** `probe()` and `decide()` act as high-efficiency, zero-overhead lexical pass-throughs.

This terminal decision officially closes the wayfinding map and seals the MNEMOS release configuration.

Signed by: `[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-TERMINAL-NO-GO]`
