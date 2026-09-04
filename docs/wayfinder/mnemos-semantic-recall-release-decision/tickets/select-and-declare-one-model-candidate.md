# Select and declare one model candidate

- **Status:** Resolved
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Define the certification corpus contract](define-the-certification-corpus-contract.md); [Confirm model and runtime redistribution viability](confirm-model-and-runtime-redistribution-viability.md)

## Question

Which single adaptation family should receive the one viability attempt? Resolve the base artifact, license, hashes, objective, allowed train/dev sources, sampling policy, export format, expected footprint, candidate policy, calibration method, and frozen gates in a signed candidate declaration before training begins.

## Findings

Evidence: [`evidence/candidate-declaration-v1.md`](../evidence/candidate-declaration-v1.md)

**Answer — `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` has been officially declared and signed as Candidate v1.**

- **Base Model:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (Apache 2.0 license, pre-quantized INT8 ONNX x86-64, weight footprint **118.6 MB** + **17.1 MB** tokenizer = **135.7 MB** total, satisfying the <= 150 MB footprint limit).
- **Training Objective:** Hybrid Margin MSE / Multiple Negatives Ranking (MNRL) objective using query-gold-contrast combinations.
- **Allowed Data Boundaries:** Train on `corpus-v2/train/` + paraphrase seeds; validation on `corpus-v2/dev/`. Certification data `corpus-v2/certification/` remains strictly sealed.
- **Retrieval Policy:** Frozen to `current_ascii-16-4` (16 lexical slots, 4 semantic slots, max $K=20$).
- **Abstention Calibration:** Score margin threshold ($\Delta \text{score} < \tau$) calibrated on the dev split to fallback on BM25 during low confidence.
- **Frozen Dev Gates:** B1 overall Candidate Recall $\ge 155/156$ ($\ge 99\%$) and $108/108$ ($100\%$) Safety/Identifier Recall; B2 Reranked Top-1 overall $\ge 148/156$ ($\ge 94.8\%$) and $108/108$ ($100\%$) Safety.

This signed declaration completes the prerequisites, officially unblocking the training and validation phase.

Signed by: `[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-CANDIDATE-V1]`
