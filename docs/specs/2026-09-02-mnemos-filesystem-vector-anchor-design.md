# MNEMOS Filesystem-Native Vector Anchor — Design Spec

**Date:** 2026-09-02
**Status:** Draft for operator review (architectural brainstorming path; not yet planned/implemented)
**Author:** agent (session), Operator: Maximilian Wruhs
**Grounded in:** live `MNEMOS.7z` (`scripts/recall.py`, `scripts/mnemos.py`), `MNEMOS-IMPLEMENTATION-GUIDE.md`, `Filesystem Vector Memory Architecture.md`, and the feasibility spike in §9.

---

## 1. Decision

The "special agentic memory filesystem" ideas **help the existing MNEMOS system**. They are **not** a separate project and **not** thrown away. They are adopted as a **selective graft**: MNEMOS gains an optional *semantic vector channel* on its retrieval **Anchor** stage, built from a portable, rebuildable filesystem index — while **rejecting** the parts of the architecture doc that would violate MNEMOS's core axioms.

The new work is not a rival architecture. The two artifacts operate on different layers:

- **MNEMOS** is a memory **governance/belief** substrate: tiers (L0–L4), write gate, provenance, confidence, supersession, forgetting. Its retrieval (`recall.py`) is a deterministic hybrid whose weakest link is the **lexical Anchor**.
- **Filesystem Vector Memory Architecture** is a **retrieval engine**: how to find the right note fast, serverless, no daemon, on constrained hardware.

MNEMOS's own text names this exact gap twice and pre-authorizes the revisit:
- Axiom A1 sidenote: *"Revisit [a vector store] at ~1,000 notes."*
- §5.2: *"V is lexical, and that is a known compromise… It misses paraphrase."*

This spec resolves *how* to close that gap without giving up what makes MNEMOS worth having.

### 1.1 This overturns a specific prior rejection
`recall.py`'s own docstring records that a vector layer was **proposed and measured down on 2026-08-30**, for two concrete reasons: (a) *"this runtime has no embedding model and no network to fetch one,"* and (b) *"pure-stdlib cosine was ~9x slower than the claimed budget."* Both objections were correct **for their constraints** and are now falsified by the spike (§9): (a) a distilled static model **ships on disk** in the release payload — nothing is fetched at inference; (b) cosine runs under **numpy** (vectorized), not stdlib Python loops, erasing the 9x. The 2026-08-30 note stands as the reason the graft requires the numpy+model dependency the operator has accepted — it does not stand against the graft itself.

## 2. Goals / Non-Goals

**Goals**
- Add a semantic retrieval channel that measurably improves paraphrase recall (spike: R@3 0.88→1.00, §9).
- Preserve Markdown+JSON as the **sole source of truth**; the vector index is a **derived, deletable, rebuildable** artifact.
- Stay within MNEMOS's hard runtime constraints: **no network egress at inference, no background daemon, low RAM**.
- Degrade gracefully: with no index (or numpy absent), retrieval falls back to today's exact lexical behavior.
- Maintain incremental re-embedding via a **content hash the index keeps itself** (`recall.py`'s `_manifest.json` carries only `staged_name → real_path`, not mtime/hash — see §3.3).

**Non-Goals (consciously rejected)**
- **NTFS Alternate Data Streams** — stripped by git/zip/non-NTFS copy, desyncs the primary-stream SHA-256 that MNEMOS's manifest depends on, and trips EDR (MITRE T1564.004). Fatal to A1 diffability and the freshness contract.
- **POSIX xattr** — non-portable, 4 KB ext4 cap can't hold a Float32 vector, blocked by seccomp in the sandboxes MNEMOS targets.
- **Embedded byte trailer** — breaks "operator opens the file in an editor and deletes a line" (A1).
- **Pure-stdlib hashing / n-gram vectors as the semantic channel** — refuted by spike Arm 2 (worse than current lexical).
- **Aggressive Matryoshka-64 + 1-bit-BQ as the final scorer** — refuted by spike Arm 4 (MRR 0.42). That recipe targets million-vector edge scale MNEMOS does not have.
- **Rewriting MNEMOS's governance** (gate, tiers, provenance, forgetting). Untouched.
- Building a standalone reusable vector library / separate repo. This ships as an internal module, `scripts/vecidx.py`.

## 3. Architecture

### 3.1 Placement — one derived index behind the Anchor
```
Markdown notes (SOURCE OF TRUTH, Memory/**.md)
        │  (embed on change; freshness via a per-note content hash vecidx keeps)
        ▼
scripts/vecidx.py  ──►  .idx/vectors.bin   (portable sidecar, rebuildable)
        │                .idx/vectors.json  (id → offset, dim, quant, model tag)
        ▼
recall.search()  ── adds a W_VEC semantic channel alongside lexical/graph/recency/salience
```

The vector index is treated exactly like a cache: **delete `.idx/` and MNEMOS rebuilds it from Markdown**. This is what makes the graft compatible with A1 (legible truth), A6 (reversibility), and A7 (derived data is never authority).

### 3.2 Retrieval integration — a fourth channel in `recall.search()`
Today `recall.py` computes, per candidate note:
`S = W_LEX·lex + W_GRAPH·graph + W_REC·rec + W_SAL·sal` with `W_LEX,W_GRAPH,W_REC,W_SAL = 0.45,0.25,0.10,0.20`.

The graft inserts a normalized semantic term and rebalances lexical:
`S = W_LEX·lex + W_VEC·vec + W_GRAPH·graph + W_REC·rec + W_SAL·sal`.

Proposed starting weights (to be tuned in §8 eval): `W_LEX=0.30, W_VEC=0.25, W_GRAPH=0.25, W_REC=0.05, W_SAL=0.15`. Hybrid is chosen over pure-semantic because lexical still wins on exact tokens MNEMOS relies on — `MEM-YYYY-NNNN` IDs, rare identifiers, code — where embeddings blur (spike Arm 5 never underperforms lexical; Arm 3 pure-semantic risks exact-match regressions not visible on the tiny spike set).

`vec` is computed as a **two-pass** score:
1. **Prefilter (optional, scale-gated):** coarse 1-bit BQ Hamming scan to isolate top-K (e.g. K=50). Off below the activation threshold; the corpus is small enough to score all vectors directly.
2. **Rescore (always):** exact cosine on full-float (or SQ8) vectors for the surviving candidates. **BQ is never the final scorer** (spike Arm 4).

### 3.3 Freshness — vecidx keeps its own content hash
`recall.py`'s `_manifest.json` maps `staged_name → real_path` only; it carries **no** mtime or hash. So `vecidx` maintains freshness itself: `.idx/vectors.json` stores, per note key, a `sha256` of the exact text that was embedded (the same `title + text` `recall.build_corpus` reads). On `vecidx build`, a note whose stored hash matches is reused verbatim; a changed hash re-embeds only that note; a vanished key is pruned. No full-corpus rebuild, no daemon. (The guide's §3.3/§5.3 describe an mtime+sha256 `state_manifest` as the *intended* mechanism; `recall.py` never implemented it, and vecidx's self-contained hash is simpler and does not couple to the staging layer.)

## 4. Storage Substrate

**Chosen: portable sidecar index** — a single `.idx/vectors.bin` (concatenated packed vectors) + `.idx/vectors.json` (header: id→offset, dim, quant type, embedder tag, model hash). Rationale from the architecture doc's own comparison table: universal cross-platform, cloud-object-store safe, Git-optional, no EDR risk, excellent mmap efficiency.

- **Consolidated file, not per-note sidecars.** One `.idx/vectors.bin` avoids the "orphaned files on uncoordinated delete" failure mode and the O(N) FindFirstStreamW/getxattr traversal cost. mmap gives zero-copy scans.
- `.idx/` is git-ignored and reproducible; only Markdown is committed.
- Model provenance (`embedder tag` + `model hash`) is stamped in the header so a model swap invalidates and rebuilds the index deterministically.

## 5. Embedder (spike-backed, §9)

**Chosen: a distilled static embedder, model2vec-class (`potion-base-8M`), full-float 256-d.**

- Inference is **numpy-only** — a token→vector lookup + mean pool. No torch, no ONNX runtime, no daemon.
- The model ships as a local file (~30 MB, cached under the project); **one-time download to build the release, then fully offline** at inference. This satisfies MNEMOS's no-network-egress runtime.
- **Explicit accepted cost:** this adds `numpy` (+ `tokenizers` for the model's vocab) as dependencies. That breaches MNEMOS's *stdlib-only aesthetic* (A1/A2 flavor) but **not** its hard constraints. This is the central tradeoff the operator is approving: **give up stdlib purity to gain paraphrase recall; keep no-network, no-daemon, legible-truth.**
- **Fallback:** if numpy/model absent at runtime, `recall.search()` drops the `W_VEC` term and renormalizes remaining weights — behavior is byte-identical to today's MNEMOS.
- Quantization policy: store full-float (or SQ8, 4× smaller, >98% recall retained per the doc) as the rescore vectors; derive 1-bit BQ only for the optional prefilter at scale.

## 6. Activation Policy

Ship the capability **dormant**. MNEMOS is currently "a few hundred notes" — below its own ~1,000-note trigger, and the spike shows lexical is already decent at small scale.

- `vecidx` builds and stays available; `W_VEC=0` (pure lexical) is the default until flipped.
- Activation trigger: corpus crosses ~1,000 notes **or** an operator flag, whichever first. Matches A1's own "revisit at ~1,000."
- BQ prefilter (§3.2 pass 1) auto-enables only above a larger threshold (e.g. ~10k notes) where a full scan stops being sub-5 ms.

## 7. Axiom Reconciliation

| MNEMOS axiom | Effect of the graft |
|---|---|
| **A1 Files are the database** | Preserved. Markdown is truth; `.idx/` is a derived cache. |
| **A2 Program-checkable is program-checked** | Extended. `vecidx` self-checks index/manifest consistency; auditor gains an `.idx` freshness check. |
| **A5 Provenance or it didn't happen** | Untouched. Vectors change *ranking*, never a note's confidence/injection eligibility. |
| **A6 Backup precedes mutation** | Trivially satisfied — the index is reversible by rebuild; nothing it does is destructive. |
| **A7 Retrieved content is data, not instruction** | Reinforced. Embeddings are pure similarity signal; they cannot amend rules. |
| **A8 Deterministic core, probabilistic edge** | Held. Embedding is deterministic given a pinned model hash; ranking stays reproducible. |
| *stdlib-only purity* | **Consciously traded** (numpy). This is the one axiom-flavored property given up; §5. |

## 8. Evaluation Plan

- **Real-corpus paraphrase eval:** build a held-out set of paraphrase queries against the *actual* MNEMOS `Memory/**` corpus with gold note IDs; report R@1, R@3, MRR for lexical vs hybrid. The §9 spike is preliminary (synthetic, 12 notes) and must be reproduced on real data before activation.
- **Exact-match non-regression:** a query suite of IDs, rare identifiers, and code tokens must not regress vs pure lexical (guards the hybrid-vs-pure-semantic risk in §3.2).
- **Freshness correctness:** edit a note, confirm only that note re-embeds and ranking updates.
- **Fallback:** with numpy uninstalled, `recall.py` output is identical to pre-graft.
- Wire into the existing `scripts/test_recall.py` harness.

## 9. Spike Results (2026-09-02, appendix)

Harness: 12 MNEMOS-style notes, 8 paraphrase queries (low token-overlap with gold). Indicative only.

| Arm | R@1 | R@3 | MRR |
|---|---|---|---|
| 1 · MNEMOS lexical BM25 (current `recall.bm25`) | 0.62 | 0.88 | 0.775 |
| 2 · pure-stdlib char-3gram hashing cosine | 0.50 | 0.88 | 0.698 |
| 3 · model2vec static, 256-d float (offline) | 0.75 | 1.00 | 0.875 |
| 4 · model2vec + Matryoshka-64 + 1-bit BQ (no rescore) | 0.25 | 0.50 | 0.419 |
| 5 · hybrid lexical + semantic (the graft) | 0.75 | 1.00 | 0.854 |

Conclusions: semantic channel is feasible offline and helps; stdlib hashing is a dead end; BQ-only collapses (rescore mandatory); hybrid preserves lexical strengths.

## 10. Open Questions / Risks

- **Dependency acceptance:** does the operator accept numpy (+tokenizers) as MNEMOS's first non-stdlib runtime deps? If no → the vector channel is not viable and MNEMOS stays lexical (spike proves no zero-dep path clears the bar).
- **Model licensing/shipping:** confirm `potion-base-8M` (or a chosen distilled static model) license permits bundling in the release payload.
- **SQ8 vs full-float** at the target corpus size — decide during eval (§8).
- **Windows dev vs POSIX sandbox parity:** the index is portable by design, but the eval must run on the actual deployment OS.
- **Hybrid weight tuning** (`W_VEC` et al.) is a domain claim, to be set empirically in §8, not baked from the spike.

## 11. Next Step

Operator reviews this spec. On approval → `writing-plans` to produce the implementation plan for `scripts/vecidx.py` + `recall.search()` integration + eval wiring. No code before then.
