# Evidence — Model & Runtime Redistribution Viability

**Ticket:** confirm-model-and-runtime-redistribution-viability
**Status:** complete
**Date:** 2026-09-04
**Author:** inline research (subagent dispatch unavailable in this env; see note at end)
**Consumers:** `select-and-declare-one-model-candidate`, `decide-the-offline-companion-package`
**Scope:** legal (redistribution) + technical (installed footprint) envelope for the optional
semantic-recall companion. Certified target: Linux x86-64, CPython 3.12, CPU-only, offline,
ONNX-Runtime inference boundary (design §8), companion **<= 150 MB incremental to core,
including installed model + tokenizer + runtime deps, excluding caches** (design §7 Footprint).

---

## 1. Decisive verdict

**The <= 150 MB footprint gate is structurally INFEASIBLE for any XLM-RoBERTa-vocabulary
(250 K token) multilingual reranker on the onnxruntime boundary.**

The multilingual quality tier for de+en cross-encoders is dominated by XLM-R-based models.
Their 250 K-token vocabulary forces an embedding matrix of ~96 M parameters (250 000 x 384),
which is **~96 MB at int8 before a single transformer layer**. Add the mandatory runtime:

| Fixed floor (any XLM-R multilingual model) | Installed MB |
|---|---|
| int8 embedding matrix (250 K x 384, 1 byte/param) | ~96.0 |
| `tokenizer.json` (250 K-vocab BPE, primary-source size) | 17.1 |
| onnxruntime 1.20.1 (cp312 manylinux x86_64, uncompressed) | 37.9 |
| **Structural floor, 0 transformer layers** | **~151.0** |

The floor alone meets or exceeds 150 MB. Transformer layers, `config`, and the `tokenizers`
wheel are all additional. **No XLM-R-vocab multilingual reranker can satisfy the current gate.**

Only one license-clean, small candidate exists (`mmarco-mMiniLMv2-L12-H384-v1`); its real
installed companion is **~182.7 MB — over budget by ~33 MB** (section 3).

This is a GO/NO-GO-relevant boundary result: it does not depend on model choice within the
XLM-R family, and it is not fixable by picking a smaller layer count.

### Decision taken (2026-09-04) — Option 2

Chosen: the ONNX runtime, `tokenizers`, and numpy are the **optional runtime baseline** (the
vector/semantic extra environment), outside the stdlib-only MNEMOS core; the <= 150 MB budget
counts **model weights + tokenizer only**. Under this boundary `mmarco-mMiniLMv2-L12-H384-v1` is
**135.7 MB (118.6 + 17.1), under 150**. Design §7 Footprint gate updated accordingly. The
"infeasible" verdict below is against the *original* runtime-inclusive gate and is the reason the
boundary was moved; it is retained as the rationale.

---

## 2. License screen (redistribution)

Redistribution = we ship the weights inside the offline package. Verified from Hugging Face
model-card metadata (primary source: `https://huggingface.co/api/models/<repo>`).

| Candidate | Params | License | Ship weights? | de+en |
|---|---|---|---|---|
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | ~118 M | **apache-2.0** | **YES** | yes (14 langs) |
| `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli` | ~107 M | **mit** | **YES** | yes (XNLI-15) |
| `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` | ~278 M | mit | yes | yes (XNLI-15) |
| `Alibaba-NLP/gte-multilingual-reranker-base` | ~306 M | apache-2.0 | yes | yes (70+) |
| `BAAI/bge-reranker-v2-m3` | ~568 M | apache-2.0 | yes | yes |
| `jinaai/jina-reranker-v2-base-multilingual` | ~278 M | **cc-by-nc-4.0** | **NO — non-commercial** | yes |

`jina-reranker-v2` is ruled out on license: CC-BY-NC-4.0, card states "licensed for research and
evaluation... For commercial usage, please refer to Jina AI's APIs." Not shippable in a product.

Runtime deps (redistributable):
- **onnxruntime** — MIT (Microsoft). cp312 manylinux_2_28 x86_64 wheel exists.
- **tokenizers** — Apache-2.0 (Hugging Face). cp39-abi3 manylinux2014 x86_64 wheel (runs on 3.12).
- **numpy** — part of the optional vector extra (`requirements-vec.txt`: "MNEMOS core needs none of
  these"), **not** the stdlib-only core; belongs to the shared runtime baseline, not the budget.

---

## 3. Footprint accounting (installed, incremental to core)

All model sizes: Hugging Face tree API (`/api/models/<repo>/tree/main`), primary source.
All wheel sizes: PyPI JSON API (`/pypi/<pkg>/<ver>/json`); "installed" = sum of uncompressed
wheel members (computed by unzipping the wheel), which is the on-disk footprint.

### 3a. Front-runner — `mmarco-mMiniLMv2-L12-H384-v1` (apache-2.0)

This repo already ships **pre-quantized int8 ONNX for x86-64** (no self-quantization needed):
`onnx/model_qint8_avx512_vnni.onnx` = 118.6 MB (also `_avx512`, `_quint8_avx2` variants, all 118.6 MB).

| Component | Installed MB | Source |
|---|---|---|
| Model int8 ONNX (`model_qint8_avx512_vnni.onnx`) | 118.6 | HF tree |
| `tokenizer.json` | 17.1 | HF tree |
| `config.json` + special tokens | ~0.05 | HF tree |
| onnxruntime 1.20.1 (uncompressed) | 37.9 | PyPI unzip |
| tokenizers 0.21.0 (uncompressed) | 9.1 | PyPI unzip |
| numpy | (runtime baseline) | not in model+tokenizer budget |
| **Total incremental** | **~182.7** | **over by ~33 MB** |

Substituting `sentencepiece.bpe.model` (5.1 MB) for `tokenizer.json` saves 12 MB ->
~170.7 MB, still over budget.

### 3b. `multilingual-MiniLMv2-L6-mnli-xnli` (mit)

fp32 `model.onnx` = 428.1 MB; **no pre-quantized int8 in the repo** — we would self-quantize.
Estimated int8 ~107 MB (same 250 K-vocab embedding floor, 6 layers). Companion estimate:
107 + 17.1 + 37.9 + 9.1 = **~171.1 MB — over budget**, and requires our own quantization step
(reproducibility burden on the frozen-artifact hash).

### 3c. Ruled out on size (int8 model alone > 150 MB)

| Candidate | fp32 ONNX MB | int8 (~1 B/param) MB | Verdict |
|---|---|---|---|
| `gte-multilingual-reranker-base` (306 M) | ~1224 | ~306 | model alone 2x over |
| `mDeBERTa-v3-base-mnli-xnli` (278 M) | ~1112 | ~278 | model alone ~1.85x over |
| `bge-reranker-v2-m3` (568 M) | ~2272 | ~568 | model alone ~3.8x over |

---

## 4. Shortlist & ruled-out (summary)

**Viable on license + multilingual, but OVER the 150 MB gate:**
- `mmarco-mMiniLMv2-L12-H384-v1` — ~182.7 MB (best-provisioned: ships x86-64 int8 ONNX).
- `multilingual-MiniLMv2-L6-mnli-xnli` — ~171 MB (needs self-quantization).

**Ruled out — license:** `jina-reranker-v2-base-multilingual` (CC-BY-NC-4.0).

**Ruled out — size (int8 model alone > 150 MB):** `gte-multilingual-reranker-base`,
`mDeBERTa-v3-base-mnli-xnli`, `bge-reranker-v2-m3`.

**No candidate clears the current 150 MB gate.**

---

## 5. Options this evidence puts in front of the release decision

The <=150 MB gate and a shippable multilingual semantic companion are, on this evidence,
mutually exclusive. The decision must pick one:

1. **Raise the footprint cap to ~200 MB.** `mmarco-mMiniLMv2-L12-H384-v1` then fits with margin
   (~182.7 MB). Cleanest path to a real semantic companion; requires editing the §7 Footprint gate.
2. **[CHOSEN] Treat the ONNX runtime + `tokenizers` (with numpy) as the optional runtime baseline,
   not part of the model+tokenizer budget.** Then the counted footprint = model + tokenizer =
   118.6 + 17.1 = **135.7 MB, under 150**. The stdlib-only MNEMOS core is unchanged; the runtime is
   the optional vector/semantic extra environment.
3. **Small-vocab (non-XLM-R) multilingual model.** Escapes the 96 MB embedding floor, but no
   strong de+en reranker at small vocab was found; quality is unproven -> high risk against the §7
   recall gates.
4. **NO-GO on semantic; keep deterministic recall.** The design's documented safe fallback. Zero
   footprint, zero redistribution risk; forfeits semantic usefulness.

Recommendation for the model-candidate ticket: proceed to evaluate **`mmarco-mMiniLMv2-L12-H384-v1`**
as the one candidate (it is the only license-clean model that both fits a plausibly-raised cap and
ships ready-made x86-64 int8 ONNX), and force options 1 vs 2 vs 4 to be decided explicitly before GO.

---

## 6. Primary sources

- Model metadata + file sizes: `https://huggingface.co/api/models/<repo>` and
  `.../tree/main[/onnx]` for each candidate above (accessed 2026-09-04).
- Wheel sizes: `https://pypi.org/pypi/onnxruntime/1.20.1/json`,
  `https://pypi.org/pypi/tokenizers/0.21.0/json`, `https://pypi.org/pypi/numpy/2.1.3/json`;
  installed size = sum of uncompressed wheel members.
- onnxruntime license: MIT — `https://github.com/microsoft/onnxruntime` (LICENSE).
- tokenizers license: Apache-2.0 — `https://github.com/huggingface/tokenizers` (LICENSE).

## 7. Open uncertainties

- **int8 estimates for 3b/3c** (multilingual-L6, gte, mDeBERTa, bge) are param-count x 1 byte;
  actual dynamic-quantization output can differ a few %. The front-runner (3a) uses a
  **measured** shipped int8 file, so its verdict is exact; the ruled-out-on-size models are all
  so far over that the margin swamps quantization error.
- **Recall quality is not evaluated here** — this ticket is license + footprint only. Whether
  `mmarco-mMiniLMv2-L12-H384-v1` clears the §7 recall/abstention gates is the separate
  `establish-production-candidate-recall` / `prove-domain-adaptation-viability` work.
- **onnxruntime install size** measured for 1.20.1; a newer pin could shift ~a few MB.

---

*Note: the AFK subagent (`ModelRuntimeResearch`) could not run — the configured subagent model
(`gemini-2.5-pro`) returns HTTP 400 `model_not_supported` in this Copilot environment. This
evidence was produced inline on a supported model, to the same deliverable contract.*
