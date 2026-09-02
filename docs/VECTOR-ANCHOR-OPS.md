# Vector Anchor — Operations

Optional semantic retrieval channel for MNEMOS. Dormant by default.

## Enable
1. `pip install -r requirements-vec.txt`
2. `python scripts/vectors/fetch_model.py`   # once, online; offline thereafter
3. Build the index after staging notes: `python scripts/vecidx.py build /tmp`
4. Activate: set `RECALL_VEC=1`, or let it auto-activate at >= 1000 notes.

## Guarantees
- Markdown is the only source of truth; `.idx/` is a rebuildable cache (safe to delete).
- With `RECALL_VEC=0`, or no index, or numpy/model absent, recall is byte-identical to lexical-only.
- Freshness: only notes whose content hash changed are re-embedded.
- BQ is a coarse prefilter above 10k notes only; the final score is always float cosine.
