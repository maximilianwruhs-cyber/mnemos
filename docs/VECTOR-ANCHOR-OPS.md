# Vector Anchor — Operations

Optional semantic retrieval channel for MNEMOS. Dormant by default.

## Enable
1. `pip install -r requirements-vec.txt`
2. `python scripts/vectors/fetch_model.py`   # once, online; offline thereafter
3. Stage the query runtime: `cp scripts/recall.py scripts/vecidx.py /c/tmp/` (vecidx.py must sit next to recall.py so `import vecidx` succeeds at query time)
4. Build the index after staging notes: `python scripts/vecidx.py build /tmp`
5. Activate: set `RECALL_VEC=1`, or let it auto-activate at >= 1000 notes.
6. Confirm the channel is live: a recall result shows `route=vector` or a non-zero `vec=` value; if every line shows no `vec=` token the channel is dormant/failed-closed to lexical.

## Guarantees
- Markdown is the only source of truth; `.idx/` is a rebuildable cache (safe to delete).
- With `RECALL_VEC=0`, or no index, or numpy/model absent, recall is byte-identical to lexical-only.
- Freshness: only notes whose content hash changed are re-embedded.
- BQ is a coarse prefilter above 10k notes only; the final score is always float cosine.

## Known limitations (before activation)
- Query-side index-coverage/freshness is not yet enforced: after staging new notes or editing existing ones, rebuild the index (`vecidx.py build`) before relying on the vec channel — at >=1000 notes an out-of-date index can demote newly-staged notes. Follow-up.
- The BQ coarse prefilter above 10k notes repacks per query; a build-time packed-bits cache is a follow-up.
