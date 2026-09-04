# Evidence — Frozen Certification Corpus (corpus-v2)

**Ticket:** build-the-frozen-evidence-corpus
**Status:** complete
**Date:** 2026-09-04
**Author:** inline authoring (subagent dispatch unavailable in this env; see note at end)
**Consumers:** `establish-production-candidate-recall`, `prove-domain-adaptation-viability`,
`calibrate-safe-semantic-abstention`, `issue-the-semantic-recall-release-decision`
**Scope:** the de-identified train / dev / certification artifacts under the resolved corpus
contract (`corpus-v2/CONTRACT.md`), plus the deterministic builder that emits them. This file
records counts, slice coverage, provenance classes, leakage checks, and content hashes so
downstream recall / abstention tickets consume the corpus without reopening data definitions.

---

## 1. Verdict

**corpus-v2 is frozen and passes every contract gate and floor.**

- Authoritative validator `scripts/corpus_lint.py` reports `corpus lint: PASS` with floors ON.
- **`corpus_hash = f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`**.
- The corpus is **builder-generated and deterministic**: two clean rebuilds produce byte-identical
  files and the identical `corpus_hash`. The freeze is guarded by a manifest whose `--check`
  recomputes every file hash byte-for-byte (tamper test in §7).

Source of truth is the builder + seeds, not the emitted JSONL:
- Seeds (human-authored, reviewable): `scripts/fixtures/vector-semantics/corpus-v2-seeds/scenarios.json`
- Builder (deterministic assembler): `scripts/corpus_build.py`
- Emitted corpus + manifest: `scripts/fixtures/vector-semantics/corpus-v2/`

### Reproduce

```
python scripts/corpus_build.py --coverage --lint     # rebuild + validate (floors ON)
python scripts/corpus_build.py --emit-manifest        # freeze manifest.json
python scripts/corpus_build.py --check                # verify byte-for-byte against manifest
```

---

## 2. Counts

76 scenario groups → 456 queries / 376 notes across the three splits. Each group is assigned to
**exactly one split** (contract split-isolation invariant), so no scenario leaks across splits.

| Split | Groups | Queries | Notes | Floor (queries / notes) | Margin |
|---|---|---|---|---|---|
| certification | 36 | **216** | **216** | 200 / 200 | +16 / +16 |
| dev | 26 | **156** | 104 | 150 / — | +6 |
| train | 14 | 84 | 56 | — | pool |
| **total** | **76** | **456** | **376** | | |

Note composition: 152 gold notes (1 en + 1 de per group) + 224 hard-negative notes. Cert groups
carry 2 hard negatives each (6 notes/group); dev and train carry 1 hard each (4 notes/group).
Targets sit **just over the floors** by design — no bloat.

---

## 3. Slice coverage (certification)

All 9 contrast families are represented; each is at 24 certification queries (**floor is 15**),
split exactly 12 en / 12 de.

| Contrast family | Cert queries | en | de | per-family-lang (floor 30%) |
|---|---|---|---|---|
| ordinary-paraphrase | 24 | 12 | 12 | 50% / 50% |
| success-vs-failure | 24 | 12 | 12 | 50% / 50% |
| permit-vs-prohibit | 24 | 12 | 12 | 50% / 50% |
| apply-vs-rollback | 24 | 12 | 12 | 50% / 50% |
| online-vs-offline | 24 | 12 | 12 | 50% / 50% |
| current-vs-superseded | 24 | 12 | 12 | 50% / 50% |
| mutate-vs-inspect | 24 | 12 | 12 | 50% / 50% |
| cause-vs-coincidence | 24 | 12 | 12 | 50% / 50% |
| identifier-tokens | 24 | 12 | 12 | 50% / 50% |

**Certification language balance: en 108 (50%) / de 108 (50%)** — inside the 40–60% band.

---

## 4. Provenance classes

The contract permits three provenance classes (no `raw`): `synthetic-contrast`,
`derived-failure-pattern`, `paraphrase-augmentation`. Distribution across the emitted records:

| Provenance | Notes | Queries |
|---|---|---|
| synthetic-contrast | 350 | 126 |
| paraphrase-augmentation | 0 | 304 |
| derived-failure-pattern | 26 | 26 |
| **total** | **376** | **456** |

Assignment rule (deterministic, in the builder): a group's gold notes and its first query in each
language carry the group's declared `provenance_gold`; every additional (paraphrase) query is
`paraphrase-augmentation`; every hard-negative note is `synthetic-contrast`.
`derived-failure-pattern` marks the four groups authored from real failure-diagnosis shapes
(broker/replica/pump/schema-direction), not synthetic contrasts.

---

## 5. Leakage check

**Gate:** every certification record must stay **below 0.60 token-Jaccard** against every
train/dev record of a *different* scenario group (contract cross-split leakage rule; tokens are
`[^\W_]+`, NFC, casefold).

**Result: max observed cert-vs-(train∪dev) Jaccard = 0.467 — under the 0.60 gate with margin.**
Zero pairs at or above 0.50. This is enforced independently by `corpus_lint` (part of the PASS)
and was pre-verified during authoring; one German `mutate-vs-inspect` query pair initially sat at
exactly 0.60 and was reworded to drop the shared `schreibgeschützt prüfen` phrasing, moving the
maximum down to 0.467.

---

## 6. Non-lexical positives

**Every one of the 456 queries carries at least one content token absent from the union of its
gold notes' tokens (en ∪ de).** A pure keyword match therefore cannot trivially resolve any
query to its gold; the query must be answered semantically. `corpus_lint` enforces this
(`qtok ⊄ gold_tokens`) and reports 0 failures.

Design corollary from the contract: every query's `gold` is **both** the en and de gold note
(any-of), so the certification set exercises **cross-lingual** retrieval directly — the intended
strength of the carried mMiniLMv2 candidate.

---

## 7. Content hashes & determinism

Frozen manifest: `scripts/fixtures/vector-semantics/corpus-v2/manifest.json`
(`corpus_version: v2`).

| File | SHA-256 |
|---|---|
| certification/notes.jsonl | `13f3c3a4a0be9f2c1e544b10bb7eef8c4c04ab88b1568ef15411f4838e759f3c` |
| certification/queries.jsonl | `b9f2e7befc0adf7ec3e199d6f8394b3aa5c4a33ec16430434ea1dced1f12445f` |
| dev/notes.jsonl | `46a04b07352b70aed727f557eeb39592b1d8b99bca23b2750a8ba5795beb2cad` |
| dev/queries.jsonl | `9e0f6235600f556b4fc5e0cfbabeca128cf696643b150bbccc3a8c78a2610cfb` |
| train/notes.jsonl | `f7f4749212e755ccf24943cbd1dcf6684ef70199aceba8134c84fa964203a9ef` |
| train/queries.jsonl | `340926619263b1c609f34faad1b2cc80d33c27b78601aa41487a73e38fb5dc40` |
| **corpus_hash** | **`f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`** |

Records are written as canonical bytes (`json.dumps(sort_keys=True, ensure_ascii=False,
separators=(",",":"))`, id-sorted, LF, trailing newline), so the hash is stable across platforms
regardless of authoring order.

**Freeze guard is real (tamper test):** corrupting a single declared file hash in `manifest.json`
and re-running `--check` exits non-zero with `[FAIL] manifest.json file_hashes mismatch (declared
!= recomputed)`; re-emitting restores `PASS` (exit 0). The manifest cannot silently drift from the
corpus.

---

## 8. De-identification

Content is **fully synthetic**: hand-authored infrastructure/operations scenarios (installers,
brokers, schemas, firewalls, backups, quotas, TLS, alarms) with invented identifiers
(e.g. `KX-4471`, `RT-90A`, `H-17`, `schema 0142`). No real personal data, credentials, or
customer records are present. The optional de-identification denylist
(`de-id-denylist.txt`) is absent, and `corpus_lint`'s denylist scan is therefore vacuously clean.

---

## 9. Authoring method

- **Native bilingual authoring, no machine translation.** Each scenario's en and de text was
  authored directly; the builder only wires records, assigns deterministic IDs, and emits
  canonical bytes — it never generates or translates language content.
- **German orthography: real umlauts (ü/ä/ö/ß), NFC.** Chosen by the maintainer over ASCII
  transliteration for native-speaker fidelity; the validator tokenizes NFC+casefold, so umlauts
  are consistent throughout.
- **Deterministic IDs:** notes `n-{group}-gold-{lang}` / `n-{group}-hard{i}-{lang}`; queries
  `q-{group}-{lang}-{j}`. Easy negatives are the gold notes of other same-split groups (guaranteed
  unrelated domain, no separate negatives bank).

---

*Method note: per session policy, subagent dispatch was unavailable in this environment
(configured model returned `model_not_supported`), so the corpus was authored and validated
inline against the same deliverable contract — the authoritative gate is `scripts/corpus_lint.py`,
which is unaffected by authoring method.*
