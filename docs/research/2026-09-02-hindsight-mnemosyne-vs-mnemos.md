# Hindsight & Mnemosyne — How They Work, and What MNEMOS Should Take From Them

**Date:** 2026-09-02
**Status:** Research note (not a design decision). Feeds into `docs/specs/2026-09-02-mnemos-filesystem-vector-anchor-design.md` and future specs.
**Sources:** primary — `github.com/vectorize-io/hindsight` README (`raw.githubusercontent.com/vectorize-io/hindsight/main/README.md`), `github.com/mnemosyne-oss/mnemosyne` README (`raw.githubusercontent.com/mnemosyne-oss/mnemosyne/main/README.md`), and the live MNEMOS substrate under this repo (`MNEMOS-IMPLEMENTATION-GUIDE.md`, `docs/specs/2026-09-02-mnemos-filesystem-vector-anchor-design.md`, `docs/plans/2026-09-02-mnemos-vector-anchor.md`, `_extracted/MNEMOS/root/scripts/recall.py`, `scripts/mnemos.py`). No vendor secondary write-ups used; benchmark numbers below are self-reported by each project's own README unless noted.

---

## 1. How Hindsight works

Hindsight is a hosted-or-self-hosted **server** memory system (Postgres+pgvector or Oracle 23ai backend), not a library you embed silently. Core API is three verbs over a **bank** (an isolated memory store, one per user/agent/project):

- **`retain(bank_id, content, context?, timestamp?)`** — an LLM call extracts facts, entities, relationships, and temporal data from the input, normalizes them into canonical entities/time-series/search indexes, and routes them down a **world-facts** or **experiences** pathway.
- **`recall(bank_id, query)`** — runs four retrieval strategies **in parallel**: semantic (vector), keyword (BM25), graph (entity/temporal/causal links), temporal (time-range filter); merges with **reciprocal rank fusion**, then reranks with a **cross-encoder** model, then trims to a token budget.
- **`reflect(bank_id, query)`** — a deeper, LLM-driven synthesis pass over existing memories for open-ended questions ("what risks need mitigating"), distinct from a lookup.

Two structures sit above raw retain/recall:
- **Observations** — the background consolidator deduplicates related facts into evidence-backed beliefs, each keeping exact supporting quotes and a **proof count**; new evidence *refines* (strengthens/weakens/extends) an observation rather than overwriting it.
- **Mental models / knowledge pages** — a standing, named question ("what are this user's preferences?") whose answer is written once by an LLM and **rewritten in the background** as the bank learns more. Reading one is a plain DB read, no retrieval, no LLM call at read time — agents can boot from a settled answer instead of re-deriving it every session. Knowledge pages are the same mechanism with folder/wiki ergonomics and markdown projection to disk.

Operational surface: 25+ LLM providers, MCP server built in per-bank, LiteLLM-based two-line wrapper for existing OpenAI/Anthropic clients, Prometheus metrics, webhooks, admin CLI, an opt-in **Memory Defense** pass that scans every `retain` against 45 PII/secret patterns and redacts or blocks before storage, per-bank **disposition traits** (skepticism, literalism, empathy) that shape `reflect`, and multilingual-by-default entity/fact preservation.

Benchmarks (Hindsight's own README, "as of January 2026," reproduced by third parties per their claim): state-of-the-art on LongMemEval among self-reported comparisons.

**What this buys, structurally:** an LLM-in-the-loop extraction+consolidation pipeline running against a real database, with background jobs continuously rewriting derived belief structures. It is a **service**, not a filesystem artifact — it assumes network egress, a running Postgres, and per-call LLM spend.

## 2. How Mnemosyne works

Mnemosyne is a **library + CLI + MCP server** backed by a **single SQLite file** — no server process required, `pip install` is the whole deployment. Its architecture is **BEAM** (Bilevel Episodic-Associative Memory):

- **Working memory** — hot context, TTL-eviction, auto-injected before LLM calls.
- **Episodic memory** — long-term store, `sqlite-vec` + FTS5 hybrid search.
- **TripleStore** — a temporal knowledge graph with explicit version chains (`kg.add(subj, pred, obj, valid_from=...)`, `kg.query(subj, as_of=...)`).

Hybrid scoring is a fixed linear blend, **all computed inside SQLite**, no external ANN service: `50% vector similarity + 30% FTS5 rank + 20% importance` (weights are env-configurable: `MNEMOSYNE_VEC_WEIGHT/FTS_WEIGHT/IMPORTANCE_WEIGHT`). Vectors use **MIB** (information-theoretic binarization): 384-dim float32 embeddings compress to 48 bytes (32×), scored with Hamming distance entirely in SQLite — no ANN index, no external vector DB, and no rescore-on-full-float step described for the default path (contrast with Hindsight and with MNEMOS's own vector-anchor spec, both of which treat pure-binary scoring as insufficient without a float rescore — see §4 below).

Storage/embedding footprint is tiered by install extra: core (`mnemosyne-memory`, ~50 MB, no local embeddings, point at a remote embedding API) → `[embeddings]` (~800 MB, adds `fastembed`) → `[all]` (~1.5 GB, adds `sentence-transformers` + a local LLM for consolidation). `mnemosyne sleep` runs consolidation explicitly (not silently backgrounded). `mnemosyne sync`/`sync-serve` provide bidirectional, delta-only, optionally client-side-encrypted (Fernet or PyNaCl SecretBox) sync between a local DB and a remote instance, with an append-only event log for auditability.

Benchmarks in Mnemosyne's own README are unusually candid about their limits: the BEAM 65.2%/Hindsight 73.4% comparison explicitly flags that judges differed (Llama 3.3 70B + DeepSeek V4 Flash judge vs. Llama-4-Maverick) and says "the 65.2% and 73.4% figures in the same row should be read with that in mind... Hindsight leads on this benchmark as published." The retrieval-only table shows flat Recall@10 = 20% from 100K to 10M messages — the README states outright that "the absolute 20% Recall@10 is low, and the flatness rather than the level is the result worth citing." This kind of self-scoring honesty is worth noting because MNEMOS already has a house norm for exactly this (VERIFIED/UNVERIFIED, §11.2 in the implementation guide) — nothing to import here, but external confirmation the norm is not eccentric.

## 3. How MNEMOS works today (baseline, for the comparison to mean anything)

Grounded in `MNEMOS-IMPLEMENTATION-GUIDE.md` and the live `_extracted/MNEMOS/root/scripts/*`:

- **Substrate:** plain Markdown + JSON, stdlib-only Python, no daemon, no network egress, no database (Axiom A1). This is a stated constraint of the runtime it was built in (SiemensGPT sandbox), not merely an aesthetic.
- **Tiers L0–L4:** `SOUL.md`/`IDENTITY.md` (immutable identity) → `AGENTS.md` (≤120 lines, procedural) → `MEMORY.md`/`USER.md` (≤200 lines/12 KB, hot working set) → `Memory/**` (archival, on-demand) → ephemeral scratch. Hard byte/line caps enforced mechanically (`doctor.py`, `mnemos.py`).
- **Note schema:** 11 required, machine-validated fields per note (`Type`, `Confidence`, `Salience`, `Created/Last-Access/Freq`, `Tags`, `Links`, `Provenance`, `Observation`, `Directive`). Confidence is `VERIFIED > HIGH > MEDIUM > LOW`; only VERIFIED/HIGH may auto-inject.
- **Write gate:** durable + actionable + non-inferable, all three required; ~2/3 of candidates rejected by design.
- **Retrieval (`recall.py`):** deterministic hybrid, no ML — `S = 0.45·BM25 + 0.25·graph(link-decay, 2 hops) + 0.10·recency(90-day half-life) + 0.20·salience`. No index to build; whole corpus (44 notes, ~79 KB) scored at query time in 1.5 ms.
- **Forgetting:** utility `U(m) = 0.6·Freq + 0.4·salience − 0.01/day·Δt`, threshold 0.35; below threshold, low-salience notes hard-delete, higher-salience notes **distil** to a one-line semantic fact (episodic→semantic). The auditor only *recommends* KEEP/DISTIL/DELETE/STUB — a human/agent executes.
- **Governance layer** (nothing comparable exists in Hindsight or Mnemosyne): content-addressed snapshot/restore (`snapshot.py`), a five-mechanism bounded self-modification pipeline (propose → snapshot → apply → probation → promote-or-rollback, `evolution.py`/`probation.py`), a code-level `HARD_IMMUTABLE` floor that a proposal can never edit (its own tests, the rollback script, the immutability config itself), append-only audit log verified by byte-prefix, and a `self_heal.py` reconciler against declared invariants.
- **Already-approved next step:** `docs/specs/2026-09-02-mnemos-filesystem-vector-anchor-design.md` (accepted 2026-09-02) grafts an optional semantic channel: `scripts/vecidx.py`, a distilled static `model2vec` embedder (numpy-only inference, no torch/ONNX, model ships on disk), a consolidated `.idx/vectors.bin` sidecar keyed by content hash, dormant until ~1,000 notes or an operator flag, and an explicit ban on binary-quantization-as-final-scorer because the project's own spike measured BQ-only collapsing to MRR 0.42 (rescore on float vectors is mandatory).

## 4. Side-by-side

| Dimension | Hindsight | Mnemosyne | MNEMOS (current) |
|---|---|---|---|
| Storage | Postgres+pgvector / Oracle 23ai, server | Single SQLite file | Markdown + JSON files |
| Deployment | Docker/Helm/pip/managed cloud | `pip install`, no server required | No install; files + Python stdlib scripts |
| Network at inference | Requires LLM API (or local model via 25+ providers) | Optional — can run fully offline with `[embeddings]`/`[all]` | **Zero**, hard constraint |
| Extraction | LLM-driven, every retain | Optional `extract=True`/`extract_entities=True` | None — human/agent authors the 11-field note directly |
| Retrieval fusion | 4-way parallel (semantic/BM25/graph/temporal) → reciprocal rank fusion → cross-encoder rerank | Fixed linear blend (50/30/20) inside SQLite | Fixed linear blend (45/25/10/20), soon +vector (30/25/25/5/15 proposed) |
| Vector compression | pgvector native (float), no stated binary path | MIB: 384-d float32 → 48 bytes, Hamming-only, no described rescore | SQ8/full-float mandated; 1-bit BQ explicitly banned as final scorer per its own spike |
| Consolidation | Background: observations (evidence+proof-count, refined not overwritten) + mental models (rewritten in background, instant read) | Explicit `mnemos sleep` command; not always-on background | Manual/scripted: DISTIL action, one-line semantic compression, auditor-recommended not auto-executed |
| Governance / rollback | None described | Snapshot/rollback for sync conflicts only | Full bounded self-modification: snapshot, probation, immutability floor, audit log |
| Secrets/PII handling | Opt-in Memory Defense, 45 patterns, redact-or-block at retain time | Not described in README | None currently |
| Multi-tenant isolation | Banks, disposition traits | Banks (`BankManager`) | Single-operator; no bank concept |
| Sync across machines | N/A (server is the shared instance) | Delta sync, optional client-side encryption, event log | None; single filesystem |
| Auditability of its own claims | Self-reported benchmarks | Explicitly flags stale/non-comparable benchmark rows | VERIFIED/UNVERIFIED discipline baked into every claim (A9/A10) |

## 5. What actually transfers to `memnos`, and what doesn't

MNEMOS's binding constraints are non-negotiable and rule out most of both systems wholesale: no network egress at inference, no background daemon, no database, stdlib-only core with heavy deps confined to an optional extra. Filtered through that:

### Adopt

1. **PII/secret scan at the write gate (from Hindsight's Memory Defense).** Cheap, stdlib-compatible (regex-only, no ML needed), and closes a real gap: MNEMOS notes are committed to git (`snapshot.py`/version control), so a secret or token written into a `Provenance`/`Observation` field persists in history indefinitely. A ~40-pattern regex scanner (API keys, tokens, private key headers, common credential shapes) run inside `mnemos.py`'s existing validation pass, rejecting or redacting before a note is accepted, is a direct, low-cost port. This is the single highest-value item here because it's a gap MNEMOS doesn't know it has, not a nice-to-have.

2. **Observations-style evidence accumulation (from Hindsight), applied to MNEMOS's existing DISTIL action.** Today, distillation collapses a cooling note to one line and drops the narrative. Hindsight's model — keep the belief, keep exact supporting quotes, keep a proof count, *refine* on new evidence instead of overwrite — is a strict refinement of MNEMOS's own Axiom A5 (provenance is load-bearing) and its existing "supersede, never silently delete" contradiction rule. Concretely: when `mnemos.py` recommends DISTIL, instead of collapsing to a single directive line, keep a small `Evidence:` list (each entry: date + one-line quote/provenance) alongside the compressed directive, and increment a proof count on repeated corroboration instead of just bumping `Freq`. This costs a few bytes per note, stays inside the existing schema/caps, and turns "distil" from lossy compression into a strengthening operation — closer to what the biological analogy MNEMOS already invokes (§5.3 sidenote) is actually describing.

3. **Mental-model-style precomputed answers (from Hindsight), mapped onto MNEMOS's already-existing `MNEMOS-CURRENT-STATE.md` pattern.** Hindsight's mental model is "a standing question, answered once, rewritten in the background, read with zero retrieval cost." MNEMOS already manually produces exactly this artifact (`docs/MNEMOS-CURRENT-STATE.md` supersedes the historical blueprint). The transferable idea isn't the mechanism (no LLM-rewrite-in-background allowed here) but the **pattern**: declare a small set of named standing questions (e.g., "what is the current L2 working set and why," "what regressions are outstanding") and let `tick.py`/`health.py` regenerate their answers deterministically each run, the same way `graphcheck.py` already regenerates `INDEX-L3.md`. This is additive to work already in `mnemos.py` (`INDEX.md` regeneration), not new infrastructure.

4. **Rank fusion over linear weight-tuning (from Hindsight's reciprocal rank fusion), as an alternative to the vector-anchor spec's proposed re-tuned weights.** The implementation guide itself flags its weights as "a domain claim, not a law" in two places (recency-vs-salience in §5.2, and again for the proposed `W_VEC` in the accepted spec §10). Reciprocal rank fusion (`1/(k+rank)` summed per channel) is scale-free and needs no weight-tuning eval pass — it would let `recall.search()` add the vector channel from the accepted spec without owning a new empirically-tuned constant. Worth evaluating alongside the spec's planned `W_LEX=0.30, W_VEC=0.25...` weights during its own §8 evaluation phase, not a reason to block that work.

5. **Mnemosyne's and Hindsight's own benchmark-honesty conventions are external confirmation, not new information** — both READMEs explicitly caveat stale/incomparable numbers the way MNEMOS's VERIFIED/UNVERIFIED discipline already requires. No action item; cite as validation if the operator ever wants evidence this norm is industry-aligned, not idiosyncratic.

### Reject, explicitly, with reasons

- **Hindsight's LLM-driven extraction on every `retain`, and its Postgres/Oracle backend.** Both require network egress and a running database — violates MNEMOS's hardest constraint outright, not a tuning tradeoff.
- **Hindsight's cross-encoder reranking stage.** Requires a second model beyond the embedder already accepted in the vector-anchor spec; at a "few hundred notes" corpus size the spike (§9 of the spec) shows plain cosine rescore already gets R@3 to 1.00 — a reranker buys nothing at this scale and adds a dependency + latency for no measured benefit.
- **Hindsight's/Mnemosyne's "bank" multi-tenant isolation and disposition traits.** MNEMOS is explicitly single-operator (`Operator: Maximilian Wruhs` threaded through every doc); there's no second tenant to isolate from. Revisit only if MNEMOS is ever asked to serve multiple operators.
- **Mnemosyne's MIB binary-vector-only scoring (Hamming distance, no described float rescore).** MNEMOS's own spike (Arm 4, §9 of the accepted spec) measured exactly this shape of approach — BQ without a rescore step — collapsing to MRR 0.42, and the spec now hard-bans BQ as a final scorer for that reason. This is a direct, evidence-backed conflict with Mnemosyne's approach as described in its README; don't adopt without re-running the spike against Mnemosyne's specific MIB scheme, and even then treat it as contradicting MNEMOS's own measured result until proven otherwise.
- **Mnemosyne's cross-instance sync protocol.** Solves a "multiple machines, one memory" problem MNEMOS doesn't have yet (single filesystem, single operator). If that need appears, its design (delta-only, event log, optional client-side encryption) is a reasonable reference architecture to study rather than build from scratch — but it's not an actionable item today.
- **Mnemosyne's install-tier heavy deps (`sentence-transformers`, local LLM via `ctransformers`) for local consolidation.** The already-accepted vector-anchor spec deliberately chose the lightest viable embedder (`model2vec`, numpy-only, no torch) for exactly the reason Mnemosyne's `[all]` tier (1.5 GB, 8 GB+ RAM recommended) would violate — MNEMOS runs in a constrained sandbox, not a desktop with headroom to spare.

## 6. Net assessment

Both Hindsight and Mnemosyne independently converge on the same retrieval shape MNEMOS's own accepted spec already lands on: hybrid lexical+semantic+graph fusion, with **explicit rejection of pure-binary-vector scoring without a float rescore** as a proven-bad shortcut. That MNEMOS's spike caught the same failure mode (Arm 4 vs. Mnemosyne's MIB design) before shipping is worth treating as validation of the vector-anchor spec's caution, not a reason to import Mnemosyne's approach.

The two items worth acting on independent of the vector-anchor work already in flight are **(1) a PII/secret write-gate scanner** (real gap, cheap, stdlib-only) and **(2) evidence-accumulating distillation** (a small schema extension that makes MNEMOS's existing DISTIL action strictly more faithful to its own provenance axiom). Everything else either requires infrastructure MNEMOS's runtime forbids, or solves a problem (multi-tenant, multi-machine, reranking at scale) MNEMOS does not currently have.
