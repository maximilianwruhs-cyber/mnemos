# Build the frozen evidence corpus

- **Status:** Resolved
- **Type:** Task
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Define the certification corpus contract](define-the-certification-corpus-contract.md)

## Question

Create and validate the de-identified train, development, and certification artifacts under the resolved corpus contract. Record counts, slice coverage, provenance classes, leakage checks, and content hashes so later tickets can consume evidence without reopening data definitions.

## Findings

Evidence: [`evidence/certification-corpus-v2.md`](../evidence/certification-corpus-v2.md).
Source of truth is the deterministic builder + reviewable seeds, not the emitted JSONL:
`scripts/corpus_build.py` + `scripts/fixtures/vector-semantics/corpus-v2-seeds/scenarios.json`
-> `scripts/fixtures/vector-semantics/corpus-v2/`.

**corpus-v2 is frozen; `scripts/corpus_lint.py` reports PASS with floors ON.**
`corpus_hash = f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3` (deterministic:
two clean rebuilds are byte-identical; `manifest.json --check` recomputes every file hash and
fails loudly on a one-byte tamper).

- **Counts:** 76 scenario groups (split-exclusive) -> 456 queries / 376 notes. Certification
  **216 q / 216 n** (floor 200/200), dev **156 q** (floor 150), train 84 q pool. Just over floors.
- **Slice coverage:** all 9 contrast families at 24 cert queries each (floor 15), every family
  12 en / 12 de. Certification language balance **en 50% / de 50%** (band 40-60%).
- **Provenance classes** (no `raw`): notes 350 synthetic-contrast + 26 derived-failure-pattern;
  queries 304 paraphrase-augmentation + 126 synthetic-contrast + 26 derived-failure-pattern.
- **Leakage:** max cert-vs-(train/dev) token-Jaccard **0.467**, under the 0.60 cross-split gate
  (0 pairs >= 0.50).
- **Non-lexical positives:** all 456 queries carry >= 1 token outside their gold union; every
  query's gold is both the en and de note (cross-lingual any-of).
- **De-identification:** fully synthetic content, invented identifiers; denylist scan vacuously
  clean. Native bilingual authoring (no MT); German uses real umlauts, NFC.
