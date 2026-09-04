# Define the certification corpus contract

- **Status:** Closed
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** Unassigned
- **Blocked by:** None

## Question

Turn the approved corpus principles into an unambiguous schema and authoring protocol: de-identification boundary, scenario groups, language and contrast coverage, relevance labels, hard-negative taxonomy, split assignment, provenance, duplicate/leakage controls, immutable hashes, and the process that keeps certification unseen during training and calibration.

## Resolution

**Closed.** The approved principles (design §6) are turned into a pinned,
machine-checked contract.

- **Artifact:** `scripts/fixtures/vector-semantics/corpus-v2/CONTRACT.md` — the
  authoritative schema and authoring protocol.
- **Validator:** `scripts/corpus_lint.py` — verification layer 1: schema,
  controlled vocabulary, de-identification, referential integrity, scenario-group
  isolation, duplicate and cross-split leakage, canonical hashing/manifest,
  coverage floors, and the certification loader guard. CLI: structure/floors,
  `--emit-manifest`, `--check`.
- **Proof:** `scripts/test_corpus_lint.py` — 19/19 green; CLI smoke: exit 0 on a
  clean corpus, exit 1 on manifest drift and on unmet floors.

Concrete decisions settled by the grilling, beyond the §6 principles:

- Normalized note pool plus query records referencing gold **note IDs** (not v1
  inline triples); `corpus-v2/` supersedes `corpus-v1`, which remains only a
  mechanics smoke fixture.
- Provenance enum `derived-failure-pattern | synthetic-contrast |
  paraphrase-augmentation`; no `raw` in any split. Synthetic-only, no verbatim
  span of eight or more tokens; optional `de-id-denylist.txt` enforced.
- Cross-scenario leakage is a hard error at >= 0.60 normalized-token Jaccard.
- Explicit authored split assignment adds a development >= 150-query floor;
  unmet floors are met by adding scenarios, never by reassignment.
- Canonical id-sorted minified JSONL with per-file sha256 and a rollup
  `corpus_hash` in `manifest.json`; `corpus_lint --check` recomputes byte-for-byte.
- `load_split(root, split, certified_run=False)` seals `certification`; a single
  post-freeze certification read; no training/calibration config may reference it.
