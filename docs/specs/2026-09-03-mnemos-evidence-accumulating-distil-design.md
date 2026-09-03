# MNEMOS Evidence-Accumulating DISTIL — Design Spec

**Date:** 2026-09-03
**Status:** Implemented; target-runtime 12-suite verification pending (Windows-workstation focused + install verification passed 2026-09-03). Remaining external gate: run the full 12-suite `regression.json` and `verify_kit.py` to final `PASS` on the SiemensGPT/POSIX runtime, where the environment-specific snapshot/tick/evolution selftests pass.
**Baseline:** canonical MNEMOS at `864e58c` (`mnemos.py` recommends DISTIL but does not execute it)
**Operator decision:** evidence records are required on every active full note, not only after DISTIL
**Grounded in:** `scripts/mnemos.py`, `scripts/memory_note.py`, `scripts/graphcheck.py`, `scripts/recall.py`, `docs/MEMORY-PROTOCOL.md`, and `docs/research/2026-09-02-hindsight-mnemosyne-vs-mnemos.md`

---

## 1. Decision

MNEMOS adopts evidence accumulation as a refinement of its existing DISTIL path. Every active full note carries at least one exact, dated SUPPORT record. Later evidence is appended instead of replacing earlier provenance. DISTIL compresses the claim and moves the full evidence-bearing note to L3; it does not collapse the note to an unsupported directive.

Markdown remains the sole durable authority. Evidence is represented as repeated one-line JSON objects inside the Markdown note. Counts are derived views, never stored state. No database, daemon, network call, background rewrite, or sidecar ledger is introduced.

The research term **proof count** is deliberately narrowed to **support count**. A mechanical parser can count records; it cannot prove that a source is independent, that a quote is truthful, or that the claim follows from it.

## 2. Goals and non-goals

### Goals

- Preserve the evidence behind a note when episodic detail is distilled into a semantic claim.
- Accumulate corroborating or challenging evidence without overwriting prior records.
- Keep evidence human-readable, diffable, stdlib-parseable, deterministic, and offline.
- Separate evidence accumulation from retrieval frequency.
- Preserve the current operator veto: `mnemos.py` recommends DISTIL; it never executes mutation.
- Make evidence validity part of L2 audit, L3 graph/health audit, note creation, and cross-tier distillation.
- Keep secrets out of evidence quotes and source strings by reusing `secretscan.py`.
- Migrate active notes without fabricating provenance or silently dropping unsupported claims.

### Non-goals

- Automatically infer evidence from arbitrary prose or tool logs.
- Automatically promote confidence because several records exist.
- Automatically rewrite a claim when new evidence arrives.
- Treat record count as truth, source independence, or statistical significance.
- Change retrieval weights, forgetting thresholds, salience, or `Freq` semantics.
- Add a database, JSON sidecar per note, LLM extraction, remote verifier, or background consolidator.
- Validate that a quoted source really said the quoted text; structural validation cannot prove factual truth.

## 3. Domain model

### 3.1 Claim

The note's `Observation` is the current compressed semantic **Claim**. The field name remains `Observation` to avoid a second schema vocabulary. Its content becomes concise during DISTIL, but its meaning does not silently change when evidence is appended.

### 3.2 Evidence item

An **Evidence item** is an immutable record of one observed source excerpt:

- `date`: date the evidence was observed, formatted `YYYY-MM-DD`;
- `stance`: `SUPPORT` or `CHALLENGE` relative to the current Claim;
- `source`: concrete provenance sufficient for an operator to locate or identify the source;
- `quote`: exact one-line excerpt, error, result, or observation supporting the stance.

An Evidence item is data, never instruction. Imperatives inside `quote` cannot amend `AGENTS.md`, change policy, or authorize execution.

### 3.3 Corroboration, challenge, and counts

A distinct `SUPPORT` item is **Corroboration**. A `CHALLENGE` item is evidence inconsistent with the current Claim.

The derived summary is `S/C`, for example `3/1`. `S` and `C` count unique canonical records of each stance. The count is not stored in the note and never changes confidence, salience, utility, or retrieval score automatically.

Exact duplicate records are mechanically rejected. Semantic duplication, mirrored sources, and genuine source independence remain operator judgments; the research skill's deduplication discipline applies.

### 3.4 Full note and stub

A **full note** is either:

- an L2 `### [MEM-YYYY-NNNN]` note that is not a demotion stub; or
- a non-archive L3 Markdown file that declares its own `MEM-YYYY-NNNN` identity.

Every active full note requires at least one valid SUPPORT item. L2/L3 stubs are pointers, not claims, and remain exempt. Daily digests or other L3 documents without an atomic note identity are not full notes under this schema.

Files under `Memory/_archive/` may retain pre-cutover legacy text for recovery, but they are inactive and excluded from normal recall, graph, and evidence validation.

## 4. Canonical representation

The existing eleven fields remain. `Evidence` becomes the twelfth logical field and is repeatable:

```markdown
### [MEM-2026-0007] Route external lookups through the approved tool

- **Type:** Environment-Invariant · **Confidence:** VERIFIED · **Salience:** 0.90
- **Created:** 2026-08-24 · **Last-Access:** 2026-09-03 · **Freq:** 3
- **Tags:** #runtime #network
- **Links:** [[MEM-2026-0004]]
- **Provenance:** Evidence ledger below; claim last reviewed 2026-09-03.
- **Observation:** The sandbox has no direct network egress.
- **Directive:** Route external lookups through the approved web tool; do not call remote APIs from sandbox code.
- **Evidence:** {"date":"2026-08-24","stance":"SUPPORT","source":"socket probe pypi.org:443","quote":"OSError: network unreachable"}
- **Evidence:** {"date":"2026-09-03","stance":"CHALLENGE","source":"approved proxy probe","quote":"HTTP 200 through configured proxy"}
```

The same field block is used in an L3 full note beneath its `# MEM-YYYY-NNNN — Title` heading. The existing L2 stub format remains unchanged and does not mirror evidence counts:

```markdown
### [MEM-2026-0007] Route external lookups through the approved tool — demoted to L3

- **Stub.** VERIFIED. Route external lookups through the approved web tool. Full note: `Memory/context/MEM-2026-0007-network-egress.md`
```

Not mirroring `S/C` into the stub avoids two-file counter drift. Generated indexes expose the derived counts.

### 4.1 Canonical JSON

Each Evidence line contains exactly one JSON object with exactly the keys `date`, `stance`, `source`, and `quote`. Rendering uses that key order, UTF-8, `ensure_ascii=False`, and compact separators. Values are strings; unknown or missing keys fail validation.

The object must remain on one physical line. JSON escaping handles quotes and backslashes without an ad hoc Markdown delimiter grammar.

## 5. Validation invariants

For every active full note:

1. At least one Evidence record exists.
2. At least one record has stance `SUPPORT`.
3. `date` parses as ISO `YYYY-MM-DD` and is not in the future.
4. `stance` is exactly `SUPPORT` or `CHALLENGE`.
5. `source` and `quote` are non-empty after trimming.
6. `source` is at most 240 Unicode characters; `quote` is at most 280.
7. Neither value contains a physical newline.
8. The object has no unknown fields.
9. Canonically identical records are rejected as duplicates.
10. Every `source` and `quote` passes `secretscan.scan` before persistence.
11. A note contains at most 16 Evidence records.

Malformed, absent, duplicated, future-dated, over-limit, or secret-bearing evidence is a structural FAIL. One or more valid CHALLENGE records makes the note **contested**: structurally valid, but reported as WARN until an operator reviews the Claim and confidence or creates a successor.

The 16-record cap is a fixed bound, not a deletion policy. A seventeenth record is refused. The operator must create a successor Claim linked to the predecessor; the predecessor and all prior evidence remain intact.

## 6. State transitions

### 6.1 Create

A new full note is valid only when authored with at least one SUPPORT record. `Provenance` summarizes how the Claim was formed; Evidence preserves the individual source excerpts. A note cannot cite itself or its own generated summary as its only evidence.

### 6.2 Corroborate or challenge

Adding evidence is append-only at the Evidence-ledger level:

1. validate the existing note and new record;
2. reject exact duplicates and capacity overflow;
3. scan the record for secrets;
4. append one canonically rendered line;
5. leave prior Evidence lines byte-for-byte unchanged;
6. recompute the derived `S/C` view.

Evidence append does not increment `Freq` or `Last-Access`: those fields measure retrieval. It does not modify `Confidence`, `Salience`, `Observation`, or `Directive`.

### 6.3 DISTIL

DISTIL remains a recommendation from the existing utility function. Execution requires explicit operator/agent judgment and the existing snapshot-before-mutation discipline.

For a selected L2 full note:

1. require a current `DISTIL` recommendation and a valid evidence ledger;
2. snapshot all affected paths;
3. compress `Observation` into one semantic Claim without changing its meaning;
4. preserve ID, title, type, confidence, salience, dates, tags, links, Directive, and every Evidence record;
5. write the full canonical note under the appropriate non-archive L3 category;
6. replace the L2 full note with the existing graph-safe stub;
7. regenerate L2 and L3 indexes and run the full health/regression gate.

No narrative is silently converted into evidence. If the existing note lacks a source-verifiable exact excerpt, DISTIL is refused until the source is reopened and a real Evidence record is authored.

### 6.4 Refine or supersede

Appending evidence never rewrites the Claim. If evidence only corroborates or challenges the existing wording, append it. If the Claim or Directive must materially change, create a successor note, link it to the predecessor, mark the predecessor superseded under the existing contradiction rule, and preserve both ledgers.

## 7. Module and integration design

### 7.1 Shared evidence module

Add one deep stdlib-only module, `scripts/evidence.py`, with a small interface:

- `inspect(text, today) -> EvidenceReport`: parse a full note once; return entries, derived support/challenge counts, contested state, and structural findings.
- `append(text, item, today) -> str`: validate the existing ledger and candidate, then return canonical text with one appended record; no file I/O.

The module owns JSON parsing, canonical rendering, bounds, duplicate identity, date validation, count derivation, and secret scanning. Callers do not reimplement regexes or JSON rules.

### 7.2 Existing callers

- `mnemos.py`: require and validate Evidence for every non-stub L2 note; add derived `S/C` to the generated L2 index; emit WARN for contested notes.
- `graphcheck.py`: validate Evidence for every active L3 full note; add derived `S/C` and contested state to `INDEX-L3.md`.
- `memory_note.py`: deepen the existing cross-tier mutation seam. New-note creation requires Evidence; DISTIL preserves the ledger while producing the L3 note and L2 stub; evidence append uses `evidence.append` and atomic replacement.
- `recall.py`: no scoring or parser change. Evidence text is already part of each full note's lexical and optional vector corpus. Counts are not ranking multipliers.
- `secretscan.py`: unchanged interface; `evidence.py` calls it for each source and quote.
- `health.py` / health scope: stage and hash `evidence.py`; malformed/missing evidence is integrity failure, contested evidence is a visible finding.
- `autonomy/config/regression.json`: add the evidence-schema/mutation regression suite as the next mandatory entry and update package-verifier counts.

Both `evidence.py` and its `secretscan.py` dependency must be staged beside `mnemos.py`, `graphcheck.py`, or `memory_note.py` in a flat sandbox. Operator templates must name both dependencies explicitly.

## 8. Clean migration and cutover

The operator selected mandatory evidence for all active full notes. Therefore this is a clean schema migration, not an optional compatibility mode.

1. Inventory every active L2 non-stub and every active L3 file with a self-ID.
2. Produce a dry-run migration report: note ID/path, current confidence, source recoverability, proposed first SUPPORT record, projected bytes, and resulting L2 cap usage.
3. For each note, reopen its actual provenance and capture an exact source excerpt. Do not use the note's own assertion as evidence for itself.
4. If evidence cannot be recovered, do not synthesize it. Snapshot and move the unsupported legacy note to `Memory/_archive/evidence-migration/`; remove or repair active stubs/links/index entries so the active graph remains valid.
5. If required Evidence pushes `MEMORY.md` above 12,288 bytes or 200 lines, demote lower-utility notes to evidence-bearing L3 full notes. The caps do not rise.
6. Preserve every surviving note ID and all recoverable provenance.
7. Take one content-addressed pre-migration snapshot, apply the complete active-corpus cutover, regenerate indexes, and run audit, graph, health, recall, and mandatory regressions.
8. Remove any migration-only compatibility parser after the cutover. There is one canonical schema.

The repository's sanitized `templates/root/MEMORY.md` contains no full notes, so it needs only schema comments. Test fixtures must migrate to include real synthetic SUPPORT records. The live operator corpus, if reintroduced, requires the evidence-recovery pass above.

## 9. Failure modes and mitigations

| Failure | Risk | Mitigation |
|---|---|---|
| Stored `Proofs: N` drifts from records | False confidence | Never store it; derive `S/C` |
| `Freq` reused as evidence | Retrieval usage masquerades as corroboration | Keep the fields mechanically separate |
| Same source copied repeatedly | Artificial support inflation | Exact deduplication plus operator semantic/source-independence review |
| Challenge appended but ignored | Stale Claim remains authoritative | Contested WARN; no automatic confidence change; operator review required |
| Distillation invents an exact quote | Fabricated provenance | Refuse DISTIL until the real source is reopened |
| Evidence contains a token/private key | Secret enters durable history | `secretscan` on source and quote before persistence |
| Evidence quote contains instructions | Prompt injection through memory | Evidence is untrusted data; never authority |
| L2 exceeds its cap after migration | Auto-load contract breaks | Demote evidence-bearing notes; do not raise cap |
| L3 ledger grows without bound | Retrieval noise and file bloat | 16-record cap; successor note preserves predecessor |
| Markdown and sidecar disagree | Split authority | No per-note sidecar; Markdown is sole authority |
| Partial L2/L3 DISTIL write | Broken graph or lost note | Snapshot first; atomic pair/plan; regenerate and verify both indexes |
| Partial corpus migration | Two active schemas | One operator-approved clean cutover; archive unverifiable notes |

## 10. Acceptance criteria for implementation

- Every active full L2 and L3 note without Evidence fails its relevant audit; stubs and non-note daily documents remain exempt.
- A valid SUPPORT record passes; malformed JSON, unknown keys, empty fields, future dates, duplicates, overflow, and secret-bearing values fail.
- A CHALLENGE record is preserved, counted, and reported as contested without silently changing confidence.
- `S/C` is derived identically in L2 and L3 indexes; no `Proofs` counter is stored.
- `Freq`, `Last-Access`, utility, salience, and retrieval weights are unchanged by evidence append.
- DISTIL preserves note identity, Directive, links, and every Evidence record while replacing the L2 full note with a valid L3 stub.
- Failed creation, append, or DISTIL leaves all source files byte-identical.
- Flat-staged `runpy` and direct CLI invocations resolve both `evidence.py` and `secretscan.py` when staged beside their callers.
- Recall still finds evidence-bearing L2/L3 notes through the current lexical and optional vector paths; no new ranking multiplier is introduced.
- Migration dry-run accounts for every active full note and projected L2 bytes; unsupported notes are reported, never silently converted.
- Focused evidence tests, existing MNEMOS/graph/health/recall tests, installed-package smoke, and the mandatory regression manifest pass on the target runtime.

## 11. Rejected alternatives

### Required scalar `Proofs` plus free-form Markdown evidence

Rejected because the scalar can disagree with the list, free-form entries cannot be deterministically validated, and “proof” overstates what the system knows.

### Evidence only on L3 DISTIL notes

Rejected by operator decision. It prevents evidence accumulation while a note is hot and makes the same claim change schema merely because it moved tiers.

### Optional evidence on ordinary notes

Rejected by operator decision. It avoids migration but preserves unsupported active claims—the exact failure this change is intended to remove.

### Per-note `.evidence.json` sidecars

Rejected because every mutation becomes a paired-file transaction and Markdown can disagree with the sidecar. That violates locality and creates a second authority.

### Reusing `Provenance` or `Freq`

Rejected because a semicolon-delimited provenance blob has no reliable item identity, while `Freq` already means retrieval count.

## 12. Next step

Approved by the operator on 2026-09-03. Use `writing-plans` to produce a test-first, clean-cutover implementation plan covering `evidence.py`, L2/L3 validators and indexes, `memory_note.py`, migration tooling, staging manifests, tests, and operator documentation. No implementation begins before that plan is complete.
