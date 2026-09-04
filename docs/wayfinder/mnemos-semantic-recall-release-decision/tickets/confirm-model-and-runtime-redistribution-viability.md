# Confirm model and runtime redistribution viability

- **Status:** Resolved
- **Type:** Research
- **Mode:** AFK
- **Assignee:** inline research (subagent env unavailable)
- **Blocked by:** None

## Question

Which compact multilingual cross-encoder or NLI-derived model families can legally and technically ship in an offline Linux CPython 3.12 companion no larger than 150 MB installed, including model, tokenizer, ONNX Runtime, tokenizers, license notices, and required native libraries? Verify claims against primary model, package, and license sources.

## Findings

Evidence: [`evidence/model-runtime-redistribution-viability.md`](../evidence/model-runtime-redistribution-viability.md)
(primary-source: HF tree API for model bytes, PyPI JSON + wheel-unzip for runtime bytes).

**Answer — no candidate clears the current 150 MB gate.** The multilingual quality tier is
XLM-RoBERTa-vocabulary (250 K tokens); that vocabulary alone is a ~96 MB int8 embedding floor.
With `tokenizer.json` (17.1 MB) + onnxruntime installed (37.9 MB) the **structural floor is
~151 MB before any transformer layer** — the gate is infeasible for this model family, not just
for a given size.

- **License-clean + multilingual, over budget:** `mmarco-mMiniLMv2-L12-H384-v1` (apache-2.0,
  ships x86-64 int8 ONNX) -> **~182.7 MB installed**; `multilingual-MiniLMv2-L6-mnli-xnli`
  (mit, self-quantize) -> ~171 MB.
- **Ruled out, license:** `jina-reranker-v2-base-multilingual` (CC-BY-NC-4.0, non-commercial).
- **Ruled out, size (int8 model alone > 150 MB):** `gte-multilingual-reranker-base` (306 M),
  `mDeBERTa-v3-base-mnli-xnli` (278 M), `bge-reranker-v2-m3` (568 M).

**Escalation — this forces a charter/spec decision (design §7 Footprint):** (1) raise cap to
~200 MB; (2) reclassify onnxruntime+tokenizers as core (like numpy) -> incremental 135.7 MB,
under 150; (3) small-vocab non-XLM-R model (quality unproven, high risk); (4) NO-GO on semantic,
keep deterministic recall. Recommend carrying `mmarco-mMiniLMv2-L12-H384-v1` as the single model
candidate and deciding 1 vs 2 vs 4 explicitly before GO.
