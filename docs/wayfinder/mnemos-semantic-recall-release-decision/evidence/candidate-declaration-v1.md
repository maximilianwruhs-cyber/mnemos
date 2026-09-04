# Signed Candidate Declaration - Candidate v1

**Status:** `DECLARED`  
**Date:** 2026-09-04  
**Signee:** MNEMOS Release Wayfinder Agent  

Following the `CANDIDATE_NO_GO` baseline result on the `corpus-v2` development split, this declaration defines and signs the parameters for the single allowed domain-adaptation and fine-tuning viability attempt. Training of this candidate is permitted to proceed under these frozen boundaries.

---

## 1. Base Model & License

- **Base Model Identifier:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
- **Hugging Face Hub URL:** [cross-encoder/mmarco-mMiniLMv2-L12-H384-v1](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1)
- **License:** Apache License 2.0 (Clean commercial reuse, redistribution-compliant).
- **Quantized Base File (ONNX):** `onnx/model_qint8_avx512_vnni.onnx`
- **SHA-256 Hash of target pre-quantized weights (Model Hub):** `7bc0d7ea6a0a0307be8eaeb6fcfdd644387c39b24a3517d804d7316d1f00d64df` (X86-64 pre-quantized INT8 version).

---

## 2. Training Objective & Loss Function

To address the lexical starvation of German compound words and semantic subtleties, the candidate will be fine-tuned using a **Margin MSE / Multiple Negatives Ranking (MNRL)** hybrid training objective:
1. **Margin MSE Loss:**
   - Anchor: `query`
   - Positive: `gold_note`
   - Hard Negative: `synthetic-contrast` or `derived-failure-pattern` note.
   - Objective: Minimize the mean squared error between the model's predicted margin (score positive - score negative) and a teacher model (or binary gold 1.0 vs 0.0 margin target).
2. **Batch Multiple Negatives Ranking Loss:**
   - Reuses positive notes of other queries within the same batch as in-batch easy negatives to enforce robust out-of-domain contrast.

---

## 3. Allowed Data Sources & Partitioning

To avoid any cognitive bias or overfitting on the final certification split, data partitions are strictly sealed:
- **Allowed Training Sources:** `corpus-v2/train/` (84 queries, 84 gold positive pairs, associated contrast and hard negative notes).
- **Allowed Augmentation Policy:** Deterministic paraphrasing matching `corpus_build.py` seeds (e.g. `paraphrase-augmentation` provenance class). No external or synthetic datasets are allowed without explicit declaration.
- **Allowed Development Sources:** `corpus-v2/dev/` (156 queries, 156 gold pairs, associated hard negatives). Only used for epoch checkpointing and model validation.
- **Certification Split Guard:** `corpus-v2/certification/` **MUST NOT** be read, loaded, or inspected during training, validation, or checkpoint selection.

---

## 4. Expected Footprint (Runtime & Weights)

- **Model Weights (INT8 ONNX):** **118.6 MB**
- **Tokenizer Metadata (`tokenizer.json`):** **17.1 MB**
- **Total Bundle Footprint:** **135.7 MB** (under the updated Option 2 footprint budget of <= 150 MB).
- **Runtime Baseline Requirements:** `onnxruntime==1.20.1`, `tokenizers==0.21.0`, `numpy==2.1.3` (installed size ~47 MB, excluded from model-specific footprint per Option 2).

---

## 5. Candidate Retrieval Policy

To ensure high-recall candidates are fed to the reranker, we freeze the candidate union policy to **`current_ascii-16-4`**:
- **Lexical reserve:** 16 slots (`current_ascii` BM25 tokenizer).
- **Semantic reserve:** 4 slots (static-vector semantic ranking).
- **Maximum Candidates (K):** 20.

---

## 6. Abstention & Calibration Protocol

- **Abstention Method:** The reranker score difference (margin) between the Top-1 candidate and the Top-2 candidate will be calibrated.
- **Threshold Calibration:** A decision threshold $\tau$ will be selected on the `dev` split such that if:
  $$\text{Score}(\text{Top-1}) - \text{Score}(\text{Top-2}) < \tau$$
  the model abstains (returns deterministic lexical BM25 fallback), preserving safety.
- **Calibration Goal:** Minimize false positives while maintaining zero safety violations.

---

## 7. Frozen Development Gates (To clear on Dev Split)

The candidate model must meet or exceed these thresholds on the development split (156 queries) to proceed to B2/Certification:
- **B1 Candidate Recall@20 (Overall):** $\ge 155/156$ ($\ge 99\text{\%}$)
- **B1 Candidate Recall@20 (Safety-Slice):** $108/108$ ($100\text{\%}$, error-free)
- **B1 Candidate Recall@20 (Identifier-Slice):** $12/12$ ($100\text{\%}$, error-free)
- **B2 Reranked Top-1 (Overall):** $\ge 148/156$ ($\ge 94.8\text{\%}$)
- **B2 Reranked Top-1 (Safety-Slice):** $108/108$ ($100\text{\%}$, error-free)

---

Signed on behalf of MNEMOS core:  
`[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-CANDIDATE-V1]`
