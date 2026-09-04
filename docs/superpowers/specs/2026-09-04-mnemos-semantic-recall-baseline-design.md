# MNEMOS Semantic Recall Baseline Design

**Status:** Approved in chat on 2026-09-04; implementation not started  
**Scope:** quality-first train/dev baseline for candidate retrieval and zero-shot reranking  
**Parent design:** [`2026-09-04-mnemos-semantic-recall-release-decision-design.md`](2026-09-04-mnemos-semantic-recall-release-decision-design.md)  
**Evidence corpus:** `scripts/fixtures/vector-semantics/corpus-v2/`, corpus hash
`f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`

## 1. Decision

Build one staged, reproducible baseline with three separately reported levels:

- **B0-current — production text reference:** the current ASCII-oriented `recall.py` tokenizer
  and BM25 scorer over the complete note pool of one split.
- **B0-unicode — strong lexical baseline:** identical BM25 math with one minimal stdlib Unicode
  tokenizer, isolating tokenization quality from semantic retrieval.
- **B1 — bounded candidate union:** protected exact-identifier hits plus a train-selected
  tokenizer/quota policy combining deterministic and Potion candidates, capped at 20.
- **B2 — zero-shot reranking:** the unadapted, pinned mmarco cross-encoder reranks B1's
  unprotected candidates.

B0-current, B0-unicode, and B2 are descriptive baselines. **Only B1 is a pre-training gate:**
a reranker cannot recover relevant notes absent from its candidate set. Policy selection uses train only; one unchanged dev
run validates the selected policy. Certification stays sealed until the final model and abstention
threshold are frozen.

This is a quality baseline. Windows timings are diagnostic and cannot satisfy the certified Linux
x86-64 latency gate.

## 2. Constraints and non-goals

- Never load `corpus-v2/certification/` during baseline selection or evaluation.
- Score every query against the complete note pool of its split.
- Keep MNEMOS core stdlib-only. Model dependencies live in a dedicated optional baseline
  environment.
- Do not change `recall.search()` or activate semantic ranking in production.
- Do not train, calibrate, choose an abstention threshold, or inspect certification results.
- Do not invent links, dates, salience values, headings, or titles absent from corpus-v2.
- Do not expose note IDs, labels, families, provenance, or rationales to either semantic model.
- A setup failure, missing query, or skipped record is not a quality result.

## 3. Components

### `scripts/semantic_baseline.py`

One standalone evaluator owns:

1. frozen-input preflight;
2. corpus-to-recall adaptation;
3. B0 deterministic ranking;
4. B1 quota evaluation and policy selection;
5. B2 ONNX inference;
6. query-level metrics and scenario-group bootstrap;
7. canonical report and manifest emission.

It imports existing `recall.py`, `vecidx.py`, and `corpus_lint.py` rather than cloning their
retrieval or corpus-loading rules. Heavy dependencies are imported only in their B1/B2 paths.

### `scripts/fetch_reranker.py`

A separate, explicit online setup command downloads only the approved files from the pinned model
revision, verifies size and SHA-256, and writes a local artifact manifest. The baseline evaluator
contains no download path and operates on local files only.

### Versioned artifacts

```text
scripts/fixtures/vector-semantics/baseline-v1/
  config.json
  model-manifest.json
  selected-policy.json
  train-report.json
  dev-report.json
  manifest.json

docs/wayfinder/mnemos-semantic-recall-release-decision/evidence/
  semantic-baseline-v1.md
```

All committed JSON is UTF-8, key-sorted canonical JSON with LF and a trailing newline. The
baseline manifest hashes the config, artifact manifest, selected policy, and both reports.
`.gitattributes` pins this directory to LF.

## 4. Frozen input preflight

A run starts only when all of these checks pass:

1. corpus manifest declares `corpus_version: v2`, `frozen: true`, and the pinned corpus hash;
2. `corpus_lint.load_split()` loads only the requested `train` or `dev` split with
   `certified_run=False`;
3. config contains no path or option naming the certification split;
4. local Potion files match their content hashes;
5. B2 model files, model revision, runtime provider, and dependency versions match the model
   manifest;
6. every expected query and note is processed exactly once.

The current corpus manifest omits the contract-required `frozen: true`. Before baseline execution,
`corpus_lint.compute_manifest`, `check_manifest`, `corpus_build --emit-manifest`, and their tests
must emit and require this field. This is a metadata correction: the six corpus data files,
`corpus_version`, per-file hashes, and `corpus_hash` remain unchanged.

The baseline computes content hashes for Potion instead of using `vecidx._model_fingerprint()`;
that implementation includes filesystem mtime and is not a stable artifact identity. The local
Potion artifact currently resolves to:

| File | SHA-256 |
|---|---|
| `config.json` | `f68ab920d7257faf6cbb4c8da5d96cc41dbbe7842b7043d92f0c2c3d3deef942` |
| `modules.json` | `0858e4a5e4c99ece0f93eae7660195497a2667a7cfca3dc3223b68df19097056` |
| `tokenizer.json` | `273ca9e28ec6990aea6206b0364443754d87e87a5dd28e94026ea9999ba3bf62` |
| `model.safetensors` | `f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2` |

## 5. Corpus adapter and B0

For each split, adapt every note to the structure consumed by `recall.py`:

- document identity/path: note ID, used only for joining and deterministic tie-breaking;
- searchable text: `note.text` only;
- title: empty;
- mentions/self IDs: empty sets;
- created: absent;
- salience: `recall.DEFAULT_SALIENCE`.

The note ID must never enter `text`, `title`, or `tokens`; otherwise scenario words embedded in IDs
would leak relevance.

Build two token views over the same documents:

- **B0-current (`current_ascii`):** `recall.tokens(note.text)` and
  `recall.tokens(query.text)` unchanged. This deliberately records today's behavior: the
  `[A-Za-z0-9…]` regex fragments or drops words containing `ä`, `ö`, `ü`, or `ß`.
- **B0-unicode (`unicode_nfc`):** NFC + casefold, then a stdlib Unicode word regex preserving
  internal `.`, `_`, and `-`; apply the same minimum length and stop-word rules as `recall.tokens`.
  BM25 constants and all later scoring rules stay identical.

Both variants use the same BM25 parameters, normalization, filtering, and stable ordering as
`recall.search(..., limit=20)`. With no authored graph/date/salience evidence, graph is zero and
recency/salience are constant; they cannot affect relative ordering. A zero-lexical-overlap note
remains excluded rather than receiving an arbitrary ID-based rank.

Report both variants at K=1, 3, and 20, plus MRR and zero-candidate rate. Their artifact names are
`deterministic_current_ascii` and `deterministic_unicode_nfc`, never `production_hybrid`:
corpus-v2 cannot measure graph, recency, or salience quality. B0-unicode exists to prevent a known
German tokenization defect from making semantic methods look artificially strong; it does not
change production code in this baseline work.

## 6. Exact-identifier protection

Extract candidate identifier lexemes from the original query with ASCII identifier boundaries,
then normalize each lexeme with NFC and casefold. A lexeme is protected when it has length at
least two and any of these holds:

- it contains a digit;
- it starts with `--`;
- it contains `.`, `_`, `/`, or `:` internally.

A hyphen alone does not classify a natural-language compound as an identifier; hyphenated IDs in
the corpus also contain digits. This rule protects examples such as `KX-4471`, `H-17`, `EVT.908`,
`schema-0142`, paths, and CLI flags without protecting generic words or acronyms such as `API`.

A note is protected when its text contains an exact normalized token matching a protected query
lexeme. All matching notes retain their B1 relative order at the front of B2 output. If more than
20 notes match protected lexemes, candidate selection fails with `identifier_budget_overflow`;
it never silently truncates protected evidence.

For `identifier-tokens` queries, the evaluator also requires that at least one gold note contains
the exact protected lexeme. Absence is a corpus-contract error, not a retrieval miss.

## 7. B1 candidate union

### Candidate channels

- **Deterministic:** either B0-current or B0-unicode ordering, as named by the policy.
- **Potion:** cosine ordering over `vecidx.embed_query(query.text)` and embeddings of
  `note.text` only; score descending, then note ID.

The Potion index is rebuilt from the split's note texts for the run. Reusing an index created from
another split or artifact hash is forbidden.

### Tokenizer/quota policies

Evaluate the Cartesian product of both deterministic tokenizers and this ordered train grid,
where `L/V` means deterministic reserve / semantic fill within K=20:

```text
current_ascii × {20/0, 16/4, 12/8, 10/10, 8/12, 4/16, 0/20}
unicode_nfc  × {20/0, 16/4, 12/8, 10/10, 8/12, 4/16, 0/20}
```

This is exactly 14 predeclared policies; no tokenizer or quota is added after train results.

For each query and policy:

1. add protected exact hits in deterministic order; they consume the 20-item budget and count
   toward reserve `L` when they are present in that deterministic channel;
2. add unique candidates from the policy's B0 variant until reserve `L` is reached;
3. add unique Potion candidates in score order until the result reaches 20;
4. if Potion is exhausted before 20, backfill with remaining candidates from that B0 variant;
5. resolve score ties by note ID and retain no duplicate note ID.

For `0/20`, protected exact hits remain mandatory and Potion fills the remaining budget. For
`20/0`, Potion is not consulted.

### Train-only selection

A policy is eligible only if train achieves:

- 100% any-gold Recall@20 on every safety-family query;
- 100% any-gold Recall@20 on `identifier-tokens` queries;
- zero dropped protected hit.

Choose among eligible policies by this total order:

1. highest overall any-gold Recall@20;
2. `current_ascii` over `unicode_nfc` when quality is identical (no production tokenizer change);
3. larger deterministic reserve `L` (least semantic dependency);
4. earlier position in the fixed tokenizer/quota grid.

If no policy is eligible, emit `CANDIDATE_NO_GO` and do not evaluate B2.

Freeze the selected tokenizer and `L/V`, config hash, train report hash, corpus hash, and Potion
hashes in `selected-policy.json` before loading dev.

### Dev gate

Run the frozen policy once over all 156 dev queries. B1 passes only when:

- overall any-gold Recall@20 is at least 99% (at least 155/156);
- safety-family any-gold Recall@20 is exactly 100%;
- `identifier-tokens` any-gold Recall@20 is exactly 100%;
- zero protected hit is dropped.

Safety families are:

- `success-vs-failure`;
- `permit-vs-prohibit`;
- `apply-vs-rollback`;
- `online-vs-offline`;
- `current-vs-superseded`;
- `mutate-vs-inspect`;
- `cause-vs-coincidence`.

`identifier-tokens` is its own hard gate. `ordinary-paraphrase` contributes to overall recall but
not the safety denominator. A dev failure emits `CANDIDATE_NO_GO`; B2 does not run.

## 8. B2 zero-shot reranking

### Pinned model

Use the broad x86-64 AVX2 quantized artifact, not an AVX-512-specific build:

| Property | Value |
|---|---|
| repository | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` |
| revision | `1427fd652930e4ba29e8149678df786c240d8825` |
| ONNX | `onnx/model_quint8_avx2.onnx` |
| ONNX bytes / SHA-256 | `118620016` / `6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b` |
| tokenizer | `tokenizer.json` |
| tokenizer bytes / SHA-256 | `17082660` / `62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626` |
| `config.json` SHA-256 | `cc2cfe51aa3fd759d21d21acf5dfd6994aa67a3c9210636d22e143699d336c77` |
| `tokenizer_config.json` SHA-256 | `e7fbfbfa6347b4e414c1cee50d142e2c2f9a895dad68b068ae83a8b564c3837e` |
| `special_tokens_map.json` SHA-256 | `378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d` |
| max pair length | 512 tokens |

Artifact revision, sizes, and LFS SHA-256 values come from the Hugging Face model and tree APIs
at the pinned revision (accessed 2026-09-04); small-file SHA-256 values are computed over the raw
revision-pinned bytes.

Baseline-v1 uses a dedicated Python 3.12 environment pinned to:

```text
numpy==2.1.3
model2vec==0.9.0
onnxruntime==1.20.1
tokenizers==0.21.0
```

These are evaluation pins, not yet the final certified runtime contract.

### Inference

- Use `CPUExecutionProvider` only.
- Set ONNX Runtime intra-op and inter-op threads to one for reproducible ordering.
- Encode `(query.text, note.text)` as a pair; preserve the query and truncate only the note side
  at 512 total tokens.
- Feed only input names declared by the pinned graph; an unexpected graph signature is a setup
  error.
- Rank by the model's single raw output logit descending. Sigmoid is omitted because it is
  monotonic and adds no ranking information.
- Rerank only B1's unprotected candidates. Protected notes remain first in their B1 relative
  order. Break equal logits by B1 position, then note ID.

B2 has no training, label access, query-specific rule, score normalization across queries,
threshold, or abstention. It is a descriptive zero-shot baseline.

## 9. Metrics and statistics

A query is correct at K when any note in its `gold` array appears in the first K positions.
Always report absolute numerator/denominator before percentages.

### Candidate metrics (both B0 variants and B1)

- any-gold Recall@1/@3/@20;
- same-language and cross-language gold presence at K;
- both-golds-present rate at K;
- MRR;
- zero-candidate rate;
- protected-hit retention;
- paired B0-unicode−B0-current deltas;
- per-language, per-family, and per-provenance slices.

### Ranking metrics (B2)

- Top-1, Recall@3, MRR;
- the same metrics on the **eligible unprotected subset** (at least two unprotected B1 candidates
  and no protected exact-token winner);
- whether a gold outranks every labeled hard negative;
- hard-negative Top-1 counts by contrast family;
- **correction:** selected B0 Top-1 wrong, gold present in B1, B2 Top-1 correct;
- **regression:** selected B0 Top-1 correct, B2 Top-1 wrong;
- paired B2 deltas against B0-current, B0-unicode, and the selected B0 variant;
- per-language, per-family, and per-provenance slices.

Record Top1−Top2 and Top1−best-hard raw-logit margins for later abstention work, but select no
threshold from baseline-v1.

### Confidence intervals and determinism

Queries from one scenario group are correlated paraphrases. Compute deterministic 95% percentile
intervals by resampling **scenario groups**, not queries: 10,000 bootstrap samples from a pinned
PRNG seed. Use the same sampled groups for every paired delta. Hard gates use exact observed
counts, never confidence intervals.

Load dev once, then run B2 twenty times over those unchanged in-memory inputs. Each inference run
must produce the same ordered-note-ID hash for every query and the same aggregate ranking hash.
Raw float logs may be diagnostic; rank identity is the determinism contract.

## 10. Report states and error policy

Every run ends in exactly one state:

- `BASELINE_COMPLETE`: preflight complete, B1 dev gate passed, and the descriptive B2 report is
  complete; this is not model certification or release GO;
- `CANDIDATE_NO_GO`: train has no eligible policy or the frozen policy misses a dev B1 gate;
- `SETUP_ERROR`: input/hash/dependency/provider/schema/runtime failure.

`SETUP_ERROR` is never converted into a score. `CANDIDATE_NO_GO` carries complete B0/B1 failure
evidence. A changed config, policy, model, dependency pin, corpus hash, or metric definition creates
`baseline-v2`; it never overwrites baseline-v1.

## 11. Verification

### Behavioral tests

`python scripts/test_semantic_baseline.py` must cover:

- corpus IDs never enter searchable text;
- each query sees the full split note pool and any-of gold is respected;
- current ASCII behavior is preserved and the Unicode tokenizer keeps NFC German words whole;
- zero-overlap candidates remain absent under both B0 variants;
- all 14 tokenizer/quota policies, deduplication, backfill, reserve accounting, and stable ties;
- protected-token classification, exact matching, retention, and budget overflow;
- train-only policy selection and immutable dev policy;
- safety/identifier gate arithmetic, including the 155/156 boundary;
- metric counts and scenario-group bootstrap determinism;
- certification loader is never called by baseline commands;
- corpus, model, Potion, config, and report drift fail closed;
- a fake ONNX session proves pair ordering, truncation, protected-prefix behavior, and tie breaks.

### Real scenario

1. Repair and verify the corpus `frozen: true` invariant; all six existing file hashes and the
   corpus hash remain unchanged.
2. Create the pinned baseline environment and fetch/verify the mmarco files.
3. Run both B0 variants and all 14 B1 policies on train; freeze the winner.
4. Run dev B0/B1 once.
5. If B1 passes, run real ONNX B2 and its 20-repeat ranking-hash check.
6. Generate and hash the machine report, then derive the Markdown evidence from it.

Completion means the committed report is reproducible from its config and manifests, every input
query has an outcome, and the evidence states either a measured B1 pass plus honest B2 baseline or
an evidence-backed candidate-stage NO-GO. No production semantic code is added by this work.
