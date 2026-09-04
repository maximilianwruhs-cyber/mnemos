# MNEMOS Semantic Recall Release-Decision Wayfinding Design

**Date:** 2026-09-04  
**Status:** Approved in chat; pending written-spec review  
**Scope:** Evidence and decisions required for a semantic-recall GO/NO-GO, plus one minimal safety correction that keeps uncertified semantics inert. Production reranker implementation is outside this effort.

## 1. Destination

Reach an evidence-backed release decision for MNEMOS semantic recall:

- **GO:** an approved, implementation-ready specification for a certified offline retriever; or
- **NO-GO:** a documented decision to retain deterministic lexical recall, backed by the same frozen evidence protocol.

The route must not presume that model adaptation succeeds.

## 2. Ground truth at map creation

Independent reproduction established:

- `potion-base-8M` places all five existing gold documents at rank 1 or 2 in the merged 15-document fixture, but ranks a hard negative first in three cases.
- mDeBERTa NLI scores 4/5 in the useful premise direction on isolated three-document cases.
- Potion plus NLI scores 5/5 only when each case competes against its own three documents.
- Against the merged 15-document pool, no tested Potion/base-plus-NLI weight ranks every positive first; two positives fall to NLI rank 8.
- Adding the tested multilingual retrieval cross-encoder yields zero valid three-signal weight combinations on that merged pool.
- The current semantic corpus has five queries and fifteen documents. It can falsify a proposed fusion, but cannot certify a production retriever or prove that fine-tuning will succeed.

Therefore the current Vector Anchor is mechanically implemented, but its real-model semantic behavior is **FAILED**, not `NOT VERIFIED`. Raw vector similarity may support candidate discovery; it is not certified as a final ranking signal.

## 3. Fixed constraints

- **Data boundary:** de-identified hybrid corpus. Locally derived failure patterns and synthetic English/German contrasts are allowed; raw memories are never exported or bundled.
- **Certified runtime:** Linux x86-64, CPython 3.12, CPU-only, no network, no daemon. Windows is a bundle-build and integrity-check environment only.
- **Error policy:** safety-first abstention. Weak semantic evidence preserves deterministic ranking.
- **Footprint:** optional installed semantic companion no larger than 150 MB.
- **Progression:** gate-first evidence ladder with one declared model candidate and one frozen certification attempt.

## 4. Domain language

- **Deterministic ranking:** BM25, graph, recency, and salience ordering that requires no model.
- **Candidate retrieval:** a bounded selector that uses deterministic relevance and may use Potion similarity to find up to 20 documents.
- **Semantic assertion:** a certified reranker replacing order within an eligible, unprotected candidate subset.
- **Eligible query:** a natural-language query with at least two unprotected candidates and no protected exact-token winner; only eligible queries count toward semantic assertion coverage.
- **Abstention:** an explicit decision not to apply semantic ordering; final output remains byte-identical to deterministic ranking.
- **Certification corpus:** frozen, de-identified held-out evidence never used for training, candidate selection, threshold calibration, or architecture tuning.
- **Scenario group:** one underlying intent or incident and all its paraphrases. A group belongs to exactly one split.
- **Certified artifact:** model, tokenizer, runtime contract, thresholds, and evidence whose hashes match one certification manifest.

Loadable is not synonymous with certified.

## 5. Gate-first route

1. **Safety correction:** make current semantic status truthful and remove count-based automatic activation before further model work.
2. **Evidence foundation:** freeze the corpus contract, build split artifacts, and freeze numeric gates.
3. **Candidate gate:** prove that the production candidate path finds gold notes at the required rate.
4. **One-model viability prototype:** adapt one declared multilingual model within the runtime and footprint constraints.
5. **Abstention gate:** calibrate on development data and evaluate once on the untouched certification corpus.
6. **Release decision:** write the implementation-ready runtime/package specification only after every gate passes; otherwise close with NO-GO evidence.

No production reranker module, package integration, or release activation proceeds before semantic viability passes. A failed model does not authorize weaker fixtures, changed thresholds, or indefinite model hopping.

## 6. Corpus contract

The certification corpus contains at least 200 de-identified queries. Each language represents 40-60% of the corpus, and every contrast family below appears in at least 15 certification queries (queries may carry multiple contrast labels):

- ordinary paraphrase;
- success versus failure;
- permit versus prohibit;
- apply versus roll back;
- online versus offline;
- current versus superseded;
- mutate versus inspect/document;
- cause versus coincidental topic overlap;
- exact IDs, rare identifiers, and code tokens.

Each query records one or more acceptable gold note IDs, labeled hard negatives, language, contrast family, scenario group, and provenance class. Scenario groups are assigned to train, development, or certification before augmentation. Broad contrast families occur across splits through different scenarios; paraphrases of one scenario never cross splits.

Certification evaluates each query through the actual candidate generator against the full certification note pool, never only its local positive/hard/easy triple. The artifact is immutable and content-hashed. Training-set size remains evidence-driven rather than fixed prematurely.

## 7. Acceptance gates

| Gate | Pass criterion |
|---|---|
| Candidate retrieval | Gold Recall@20 >= 99% overall; 100% on safety contrasts |
| Final ranking | Top-1 >= 95%; Recall@3 >= 99% |
| Hard-negative safety | Zero opposite-intent Top-1 promotions |
| Exact-token safety | Zero regressions for IDs, code, and rare identifiers |
| Usefulness | Correct at least 50% of deterministic base-ranking misses; certification must expose at least 20 such misses |
| Regression budget | At most one new general Top-1 error per 100 queries |
| Abstention usefulness | Semantic assertion on at least 50% of eligible paraphrase queries |
| Determinism | Identical ranking and abstention across 20 repeated runs |
| Offline | Zero attempted network connections during load and inference |
| Performance | Warm end-to-end p95 <= 1 second at K=20; cold initialization <= 3 seconds |
| Footprint | Optional installed companion <= 150 MB incremental to core, including installed model, tokenizer, and runtime dependencies but excluding installer caches |

Reports include absolute counts and 95% confidence intervals; an observed 100% is not described as universal proof. Safety, exact-token, offline, determinism, and footprint failures are hard NO-GO results.

## 8. Conditional runtime architecture

This architecture is specified now as a boundary, not implemented before viability passes.

```text
recall.py
  deterministic_rank()  -> model-free final fallback
  candidate_select()     -> <=20 candidates from deterministic and Potion evidence
  search()               -> owns protected hits and final output
             |
             v
semrank.py
  probe()                 -> UNAVAILABLE / LOADABLE_UNCERTIFIED / CERTIFIED
  decide()                -> ASSERT(order) / ABSTAIN(reason)
             |
             v
certification.json
  artifact hashes, corpus hash, runtime contract, thresholds, evidence
```

Data flow:

1. Compute deterministic ranking.
2. Select at most 20 candidates. Exact selector policy is decided by candidate-recall evidence.
3. Protect exact IDs, code, and rare-token hits from displacement.
4. Pass immutable candidate records to `semrank.decide()`.
5. On `ASSERT`, reorder only the eligible candidate subset; deterministic score and document ID break ties.
6. On `ABSTAIN`, return deterministic output byte-identically. Potion-expanded candidates cannot enter final output without a semantic assertion.

Potion is candidate evidence only. It is not part of the deterministic fallback score.

## 9. Fail-closed and activation contract

Missing dependencies, model/manifest hash mismatch, uncertified artifacts, schema mismatch, unsupported runtime, inference exceptions, NaN/shape errors, or low confidence all produce `ABSTAIN`. No partial semantic order is accepted. Query and note text are never logged. Ranking cannot mutate memory or influence governance permissions.

Activation requires both:

```text
MNEMOS_SEMANTIC_RECALL=1
probe() == CERTIFIED
```

The `>=1000` count-based auto-enable is removed. The ambiguous raw-vector final-score path and `RECALL_VEC` activation are retired through a clean configuration cutover.

## 10. One-attempt certification lifecycle

Before training, a candidate declaration freezes:

- base model and redistribution license;
- model/tokenizer hashes and expected footprint;
- training objective and allowed sources;
- train/development/certification hashes;
- candidate-selection policy;
- abstention calibration method;
- every acceptance gate.

Training may iterate against train and development data only. After model and threshold freeze, certification runs once. Failure closes this candidate as NO-GO. Any future candidate is a new wayfinding effort with a fresh certification corpus if prior holdout results influenced its design.

## 11. Verification layers

1. Dataset schema, de-identification, scenario-group isolation, duplicate/leakage checks, and hashes.
2. Full-pool candidate Recall@20 and protected exact-token behavior.
3. Offline model adapter shape and numeric contracts.
4. Selective semantic accuracy, abstention, hard-negative slices, corrected base misses, and regressions.
5. End-to-end `recall.search()` against staged Markdown.
6. Failure injection: missing/corrupt artifacts, wrong manifest, missing dependency, inference error, NaN, and stale index all preserve deterministic output.
7. Clean Linux CPython 3.12 installation with network blocked, footprint and warm/cold latency measured.
8. Independent clean rerun reproducing artifact hashes and the certification verdict.

The semantic profile reports `PASS`, `FAILED`, or `NOT AVAILABLE`. Absent prerequisites never become semantic success.

## 12. Release artifacts

A **GO** produces the frozen de-identified corpus, certification manifest, evidence report, implementation-ready runtime/package specification, license notices, and offline dependency inventory.

A **NO-GO** preserves the same corpus hashes and evidence report, names every failed gate, retains deterministic recall, and ships no model or dormant production scaffolding.

## 13. Wayfinder map

The canonical decision map is [`docs/wayfinder/mnemos-semantic-recall-release-decision/MAP.md`](../../wayfinder/mnemos-semantic-recall-release-decision/MAP.md). Named tickets hold unresolved questions and their dependencies. This spec governs the route; ticket resolutions provide the evidence and decisions needed to reach its destination.

## 14. Out of scope

- Production reranker implementation before a GO decision; the minimal truthful/inert safety correction is the sole exception.
- Exporting or bundling raw memory text.
- Network embedding or reranking APIs.
- Background daemons, vector databases, or changes to MNEMOS governance.
- Windows inference certification.
- Using static embeddings as a certified final semantic score.
- Weakening frozen labels, thresholds, or the certification corpus after failure.
