# Vector Anchor — Operations

Status: **FAILED as a ranking signal, retired.** Deterministic lexical+graph
recall is the release default and the only path that scores results.

## Why it is retired
A static-embedding (`potion-base-8M`) vector channel was prototyped as a fourth
scoring term in `recall.search()`. Independent reproduction on the merged
15-document fixture placed a hard negative first in three of five gold cases:
raw cosine similarity is not a safe final ranking signal. Its real-model
semantic quality is therefore **FAILED**, not merely "not verified". Per the
2026-09-04 semantic-recall release-decision map, no uncertified model may score
final results.

## What changed
- `recall.search()` no longer accepts a `stage` argument and never blends a
  vector term; results are always the deterministic BM25 + graph + recency +
  salience ranking.
- The `RECALL_VEC` environment flag and the corpus-size (`>= 1000` notes)
  auto-activation are removed. No environment variable or corpus size can
  activate raw vector scoring.
- Result rows no longer carry a `vec` field and `route` is never `vector`.

## What remains
- `scripts/vecidx.py` still builds and loads a derived, deletable `.idx/` cache
  (`python scripts/vecidx.py build /path/to/stage`). It is a rebuildable
  candidate-discovery artifact only; `recall.search()` does not consult it.
- Markdown remains the sole source of truth; `.idx/` is safe to delete.

## Reactivation
Semantic ranking returns only through a certified reranker that passes the
acceptance gates in
`docs/superpowers/specs/2026-09-04-mnemos-semantic-recall-release-decision-design.md`
under an explicit `MNEMOS_SEMANTIC_RECALL=1` plus certified-probe contract.
Until that GO decision, this channel stays inert.
