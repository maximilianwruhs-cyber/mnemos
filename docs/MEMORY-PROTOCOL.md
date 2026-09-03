# MNEMOS v1.0 — Memory Protocol Specification

> Progressive-disclosure document. NOT auto-loaded. Read only when maintaining,
> auditing, or extending the memory substrate. Everything needed for routine
> operation already lives in `AGENTS.md` and `MEMORY.md`.

## 1. Why this design

The four research dossiers converge on one verdict: **decouple static procedural rules from
dynamic execution learnings**, and keep the always-loaded surface small. Two empirical results
drive every decision below.

| Finding | Consequence here |
|---|---|
| Verbose auto-generated context files cost −0.5% to −3.0% task success and +20–23% tokens | Root files are hard-capped and human-reviewable |
| Minimalist curated context (<150 lines) cuts output tokens 16% and wall-clock 28% | `AGENTS.md` ≤ 120 lines, `MEMORY.md` ≤ 200 |
| Static rule files used as memory sinks bloat past 120 lines and dilute compliance | Learnings never enter `AGENTS.md`; they go to L2/L3 |
| Filesystem-backed memory scores 74.0% LoCoMo without any database | A pure-markdown substrate is a legitimate choice, not a compromise |
| Atomic notes with bidirectional links cut context budget 33–50% | Zettelkasten `[[MEM-ID]]` linking |

## 2. Tier map

This ecosystem has no vector database, no daemon, no MCP server, and no network. It does have
an auto-load boundary — some files enter context every session, others do not. That boundary is
functionally identical to the Letta/MemGPT core-vs-archival split, so the architecture is built
on it rather than against it.

| Tier | Location | Loaded | Mutability | Cognitive role |
|---|---|---|---|---|
| **L0 Identity** | `SOUL.md`, `IDENTITY.md` | Always | Immutable (ASK to edit) | Persona kernel, prefix primacy |
| **L1 Procedural** | `AGENTS.md` | Always | Semi-mutable, ≤120 lines | Rules, boundaries, toolchain |
| **L2 Core** | `MEMORY.md`, `USER.md` | Always | Dynamic, ≤200 lines | Working set — hot notes, manifest |
| **L3 Archival** | `Memory/**` | On demand | Append-heavy | Episodic + semantic long-term store |
| **L4 Ephemeral** | `steps_*` checklist, scratchpad | Per task | Purged | ReAct working state |

Paging between L2 and L3 replaces virtual-memory paging. Promotion happens when a note is
retrieved repeatedly; demotion happens when utility decays or the line cap is hit.

## 3. Atomic note schema

```
### [MEM-YYYY-NNNN] Short imperative title
- **Type:** Failure-Mode | Gotcha | Decision | Preference | Environment-Invariant | Domain-Fact
- **Confidence:** VERIFIED | HIGH | MEDIUM | LOW
- **Salience:** 0.00–1.00
- **Created:** YYYY-MM-DD · **Last-Access:** YYYY-MM-DD · **Freq:** N
- **Tags:** #topic #topic
- **Links:** [[MEM-YYYY-NNNN]]
- **Provenance:** How this was learned. Required.
- **Observation:** What happened, concretely.
- **Directive:** What to do differently next time. Actionable, imperative.
```

`Observation` without `Directive` is trivia. Both are mandatory.

## 4. Retrieval mathematics

**Composite score** (Hong & He 2025), applied at rerank:

$$S(m, q, t) = w_r \cdot R(t) + w_i \cdot I(m) + w_v \cdot V(m, q)$$

Weights: `w_r = 0.25`, `w_i = 0.35`, `w_v = 0.40`, summing to 1.0. Salience is weighted above
recency deliberately — this workspace accumulates durable environment invariants, which should
not fade merely because they were learned early.

**Recency decay**, hourly retention coefficient `δ = 0.995`:

$$R(t) = \delta^{\Delta t}$$

| Elapsed | Retention |
|---|---|
| 24 h | 0.887 |
| 168 h (1 week) | 0.430 |
| 720 h (30 days) | 0.027 |

Retrieval resets `Last-Access` and increments `Freq`, flattening the curve for facts that keep
proving useful — the MemoryBank reinforcement correction.

**Adaptation — the `V(m,q)` term.** The source architectures compute this as embedding cosine
similarity. No embedding model is reachable here. `V(m,q)` is instead the normalised overlap
between query terms and a note's `Tags` + title + `Observation`, obtained via `memory_search`
ranking and `fs_grep`. This is lexical, not semantic: it will miss paraphrase. Mitigation is to
tag generously at write time, since tags are the recall surface.

**Active forgetting**, utility with `γ = 0.6`, `λ = 0.01`/day, threshold `τ = 0.35`:

$$U(m) = \gamma \cdot \text{Freq}(m) + (1 - \gamma) \cdot I(m) - \lambda \cdot \Delta t$$

When `U(m) < τ`:
- `I(m) < 0.30` → **hard delete**. Transient operational noise.
- `I(m) ≥ 0.30` → **distil**. Compress to a one-line semantic fact, drop the verbose trace.

## 5. Lifecycle without hooks

There are no `SessionStart` / `SessionStop` hooks in this runtime. The equivalents:

| Canonical hook | Implementation here |
|---|---|
| SessionStart injection | Auto-load of `MEMORY.md` + `AGENTS.md`. Free, already happens. |
| On-demand recall | `memory_search` → `fs_grep` link walk → rerank |
| SessionStop extraction | Explicit `memory_save` / `memory_daily_update` at end of substantive work |
| Sleep-time consolidation | `CreateSchedule` cron running the audit + distillation pass |
| Deterministic validation | `scripts/mnemos.py` — line caps, schema, dead links, duplicates |

The scheduled pass is the only unattended component and therefore sits in the ASK tier.

## 6. Failure modes and mitigations

| Risk | Mechanism | Mitigation in this design |
|---|---|---|
| Instruction dilution | Rules file grows past 120 lines | Hard cap enforced by `mnemos.py audit` |
| Memory poisoning | A hallucinated result is persisted, then reloaded forever | Three-question gate + mandatory provenance + VERIFIED reserved for tool-proven facts |
| Stale fact contamination | Obsolete invariants keep surfacing | Recency decay + utility pruning + supersede-don't-delete |
| Context compaction wipeout | Long sessions drop working state | Scratchpad in L2 survives compaction because L2 is re-injected |
| Indirect prompt injection | Uploaded file or web result carries instructions | Injection Defense clause in `AGENTS.md`; untrusted content is data |
| Lexical recall miss | No embeddings; paraphrased queries miss | Generous tagging; link traversal recovers neighbours |

## 7. Maintenance cadence

- **Per session, on substantive work:** apply the write gate; save what passes.
- **Weekly:** run `mnemos.py audit`; distil or delete anything below `τ`; refresh `INDEX.md`.
- **On contradiction:** supersede immediately. Newer VERIFIED beats older anything.
- **On tool-behaviour change:** update the Verification Matrix in `AGENTS.md` in the same pass.

## 8. Deliberate non-goals

No vector store, no knowledge graph database, no background daemon, no MCP integration. Each
requires either a network or a persistent process; this ecosystem offers neither. Adding them
would produce an architecture that cannot run. If connections are enabled later, the natural
upgrade path is to keep L0–L2 exactly as they are and back L3 with a managed store.
