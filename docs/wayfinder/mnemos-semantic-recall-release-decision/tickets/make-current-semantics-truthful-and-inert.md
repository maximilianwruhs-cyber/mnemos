# Make current semantics truthful and inert

- **Status:** Closed
- **Type:** Task
- **Mode:** AFK
- **Assignee:** Main
- **Blocked by:** None

## Question

Apply and verify the minimal safety correction that changes current real-model semantics from `NOT VERIFIED` to `FAILED`, prevents count-based automatic vector activation, and leaves deterministic recall as the release default while wayfinding continues. Which exact code, configuration, tests, and operator documents must change so no stale path can activate raw vector scoring?

## Resolution

Retired the raw-vector final-score path through a clean configuration cutover:

- `recall.search()` dropped its `stage` argument and the vector blend; ranking is always deterministic BM25 + graph + recency + salience.
- Removed `RECALL_VEC` activation, the `>= 1000` count auto-enable (`VEC_ACTIVATE_N`), `VEC_WEIGHTS`, and `_vec_enabled`.
- `docs/VECTOR-ANCHOR-OPS.md` records the channel as FAILED and retired; `vecidx.py` mechanics remain a rebuildable candidate-discovery artifact only.

Evidence: `test_recall.py` 14/14 (no `stage` param, `RECALL_VEC=1` inert, retired symbols absent), `test_vecidx.py` 14/14, `recall.py --selftest` 9/9. Live-model proof: with `potion-base-8M` present, a real `.idx` built, and `RECALL_VEC=1`, the paraphrase "cannot reach the internet" no longer surfaces the zero-lexical-overlap note (no `route=vector`, no `vec`).
