# MNEMOS Vector Anchor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MNEMOS retrieval an optional, dormant-by-default semantic vector channel built from a portable, rebuildable filesystem index, without changing today's lexical behavior until deliberately activated.

**Architecture:** A new self-contained module `scripts/vecidx.py` embeds each note with a distilled static model (numpy-only inference, model shipped on disk), storing a consolidated `.idx/vectors.bin` + `.idx/vectors.json` derived cache keyed by the exact document keys `recall.build_corpus` produces. `recall.search()` gains an optional `stage` argument; when a built index is present and the vector channel is enabled, it adds a normalized `vec` term to its existing lexical/graph/recency/salience score. Markdown stays the sole source of truth; delete `.idx/` and it rebuilds.

**Tech Stack:** Python 3.12 (stdlib for MNEMOS core), `numpy` + `model2vec` (`tokenizers` transitively) for the vector extra only, a local `potion-base-8M` static model.

**Spec:** `docs/specs/2026-09-02-mnemos-filesystem-vector-anchor-design.md` (read it alongside this plan)

## Global Constraints

- **No network egress at inference.** The model loads only from the local dir `scripts/vectors/potion-base-8M/`; fetching happens once, offline thereafter.
- **No background daemon.** All work is synchronous, invoked by build/query commands.
- **Markdown is the sole source of truth.** `.idx/` is derived, git-ignored, and fully rebuildable from notes.
- **Backward compatibility is exact.** `recall.search(query, docs)` called *without* `stage` MUST be byte-identical to today (weights `0.45,0.25,0.10,0.20`; existing tests unchanged and passing).
- **Dormant by default.** The vector channel is used only when (a) an index is present, (b) `vecidx.available()` is true, and (c) `RECALL_VEC=1` OR corpus size ≥ `VEC_ACTIVATE_N` (1000). `RECALL_VEC=0` hard-disables.
- **BQ is never the final scorer.** Binary quantization is only an optional coarse prefilter with mandatory float rescore (spike Arm 4 collapsed to MRR 0.42).
- **Determinism.** Embeddings are deterministic given the pinned model; result ties still break on document id.
- **New deps confined to the vector extra.** MNEMOS core stays stdlib-only; numpy/model2vec live behind lazy imports and `available()`.

---

### Task 0: Repository and ignore setup

**Files:**
- Create: `C:/Users/z005a5ff/Projects/memnos/.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: a git repo so later tasks can commit; ignore rules for derived/large artifacts.

- [ ] **Step 1: Initialize git (project is not yet a repo)**

```bash
cd /c/Users/z005a5ff/Projects/memnos && git init
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.idx/
_extracted/
scripts/vectors/potion-base-8M/
.cache/
```

- [ ] **Step 3: Commit the baseline**

```bash
git add .gitignore docs/ "Filesystem Vector Memory Architecture.md" MNEMOS-IMPLEMENTATION-GUIDE.md MNEMOS.7z
git commit -m "chore: init repo, ignore derived vector artifacts"
```

---

### Task 1: Vector dependency manifest and availability probe

**Files:**
- Create: `C:/Users/z005a5ff/Projects/memnos/requirements-vec.txt`
- Create: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py`
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_vecidx.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `vecidx.MODEL_DIR: Path`, `vecidx.available() -> bool` (True iff numpy+model2vec import and `MODEL_DIR` exists).

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_vecidx.py
import importlib.util, sys
from pathlib import Path
import unittest

MODULE = Path(__file__).with_name("vecidx.py")
spec = importlib.util.spec_from_file_location("vecidx", MODULE)
vecidx = importlib.util.module_from_spec(spec)
sys.modules["vecidx"] = vecidx
spec.loader.exec_module(vecidx)


class AvailabilityTests(unittest.TestCase):
    def test_available_is_boolean(self):
        self.assertIn(vecidx.available(), (True, False))

    def test_model_dir_is_under_scripts_vectors(self):
        self.assertEqual(vecidx.MODEL_DIR.name, "potion-base-8M")
        self.assertEqual(vecidx.MODEL_DIR.parent.name, "vectors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/z005a5ff/Projects/memnos && python scripts/test_vecidx.py`
Expected: FAIL — `vecidx.py` does not exist / `MODEL_DIR` undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/vecidx.py
#!/usr/bin/env python3
"""vecidx.py - derived, rebuildable semantic vector index for MNEMOS recall.

Markdown stays the source of truth; this module writes a .idx/ cache that
recall.py may join at query time. All heavy deps (numpy, model2vec) are lazy
and gated behind available(); MNEMOS core stays stdlib-only.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "vectors" / "potion-base-8M"
IDX_DIRNAME = ".idx"


def available() -> bool:
    if importlib.util.find_spec("numpy") is None:
        return False
    if importlib.util.find_spec("model2vec") is None:
        return False
    return MODEL_DIR.exists()
```

- [ ] **Step 4: Write `requirements-vec.txt`**

```text
# Optional vector-anchor extra. MNEMOS core needs none of these.
numpy>=1.26
model2vec>=0.3
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python scripts/test_vecidx.py`
Expected: PASS (2 tests). `available()` returns False until Task 8 fetches the model — that is correct.

- [ ] **Step 6: Commit**

```bash
git add scripts/vecidx.py scripts/test_vecidx.py requirements-vec.txt
git commit -m "feat(vecidx): module skeleton + availability probe"
```

---

### Task 2: Content hash and embedding functions

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py`
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_vecidx.py`

**Interfaces:**
- Consumes: `available()`, `MODEL_DIR`.
- Produces:
  - `content_hash(text: str) -> str` (sha256 hex, stdlib only).
  - `embed_texts(texts: list[str]) -> "np.ndarray"` shape `(N, D)` float32, each row L2-normalized.
  - `embed_query(query: str) -> "np.ndarray"` shape `(D,)` float32, L2-normalized.
  - `VEC_DIM` is discoverable via `embed_query(...).shape[0]`.

- [ ] **Step 1: Write the failing tests (skip cleanly if model absent)**

```python
# add to scripts/test_vecidx.py
import math

@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class EmbedTests(unittest.TestCase):
    def test_content_hash_is_stable_and_stdlib(self):
        self.assertEqual(vecidx.content_hash("abc"), vecidx.content_hash("abc"))
        self.assertNotEqual(vecidx.content_hash("abc"), vecidx.content_hash("abd"))
        self.assertEqual(len(vecidx.content_hash("abc")), 64)

    def test_query_embedding_is_unit_norm(self):
        v = vecidx.embed_query("sandbox has no network egress")
        self.assertGreater(v.shape[0], 0)
        self.assertAlmostEqual(float((v * v).sum()) ** 0.5, 1.0, places=4)

    def test_similar_text_scores_higher_than_unrelated(self):
        q = vecidx.embed_query("cannot reach the internet from the sandbox")
        m = vecidx.embed_texts([
            "sockets to external hosts raise OSError; no outbound network",
            "the third floor coffee machine is broken",
        ])
        near, far = float(m[0] @ q), float(m[1] @ q)
        self.assertGreater(near, far)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_vecidx.py`
Expected: FAIL — `content_hash`/`embed_*` undefined (EmbedTests skip only if model absent; when model present they fail on missing functions).

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/vecidx.py
import hashlib

_MODEL = None


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_model():
    global _MODEL
    if _MODEL is None:
        from model2vec import StaticModel
        _MODEL = StaticModel.from_pretrained(str(MODEL_DIR))
    return _MODEL


def _normalize_rows(mat):
    import numpy as np
    mat = np.asarray(mat, dtype="float32")
    if mat.ndim == 1:
        mat = mat[None, :]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype("float32")


def embed_texts(texts):
    raw = _load_model().encode(list(texts))
    return _normalize_rows(raw)


def embed_query(query: str):
    return embed_texts([query])[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_vecidx.py`
Expected: PASS (EmbedTests skipped until Task 8, or pass now since the spike already cached the model in the HF cache — either outcome is green).

- [ ] **Step 5: Commit**

```bash
git add scripts/vecidx.py scripts/test_vecidx.py
git commit -m "feat(vecidx): content hash + normalized embeddings"
```

---

### Task 3: Incremental index build and load

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py`
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_vecidx.py`

**Interfaces:**
- Consumes: `embed_texts`, `content_hash`, `IDX_DIRNAME`. Enumerates docs via `recall.build_corpus(stage)` when `docs` is not passed.
- Produces:
  - `build(stage: str, docs: dict | None = None) -> dict` — writes `<stage>/.idx/vectors.bin` (float32, row order == key order) and `<stage>/.idx/vectors.json` (`{"model","dim","count","keys":[...],"hashes":{key:sha}}`); reuses vectors whose stored hash is unchanged, re-embeds changed keys, prunes vanished keys; returns `{"built":int,"reused":int,"pruned":int}`.
  - `load(stage: str) -> tuple[list[str], "np.ndarray"] | None` — `(keys, matrix)` or `None` if no index / unavailable.
- Embedding text per doc is `docs[key]["title"] + "\n" + docs[key]["text"]`.

- [ ] **Step 1: Write the failing tests**

```python
# add to scripts/test_vecidx.py
import tempfile, json, shutil

@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class IndexTests(unittest.TestCase):
    def _docs(self):
        return {
            "/Memory/a.md": {"title": "no network", "text": "sockets raise OSError"},
            "/Memory/b.md": {"title": "read only out dir", "text": "writes fail EROFS"},
        }

    def test_build_then_load_roundtrips_keys(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        stats = vecidx.build(d, docs=self._docs())
        self.assertEqual(stats["built"], 2)
        keys, mat = vecidx.load(d)
        self.assertEqual(set(keys), {"/Memory/a.md", "/Memory/b.md"})
        self.assertEqual(mat.shape[0], 2)

    def test_unchanged_docs_are_reused_not_reembedded(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        vecidx.build(d, docs=self._docs())
        stats = vecidx.build(d, docs=self._docs())
        self.assertEqual(stats["reused"], 2)
        self.assertEqual(stats["built"], 0)

    def test_changed_doc_reembeds_and_deleted_doc_prunes(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        vecidx.build(d, docs=self._docs())
        docs = {"/Memory/a.md": {"title": "no network", "text": "CHANGED body now"}}
        stats = vecidx.build(d, docs=docs)
        self.assertEqual(stats["built"], 1)
        self.assertEqual(stats["pruned"], 1)
        keys, _ = vecidx.load(d)
        self.assertEqual(keys, ["/Memory/a.md"])

    def test_load_missing_index_returns_none(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        self.assertIsNone(vecidx.load(d))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_vecidx.py`
Expected: FAIL — `build`/`load` undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/vecidx.py
import json, os


def _idx_paths(stage):
    d = os.path.join(stage, IDX_DIRNAME)
    return d, os.path.join(d, "vectors.bin"), os.path.join(d, "vectors.json")


def _embed_text_for(doc):
    return f'{doc["title"]}\n{doc["text"]}'


def build(stage: str, docs=None) -> dict:
    import numpy as np
    if docs is None:
        import recall
        docs = recall.build_corpus(stage)
    prev_keys, prev_mat = [], None
    loaded = load(stage)
    prev_hashes = {}
    if loaded is not None:
        prev_keys, prev_mat = loaded
        _, _, meta_p = _idx_paths(stage)
        prev_hashes = json.load(open(meta_p, encoding="utf-8")).get("hashes", {})
    prev_row = {k: i for i, k in enumerate(prev_keys)}

    keys = sorted(docs)
    hashes = {k: content_hash(_embed_text_for(docs[k])) for k in keys}
    to_embed = [k for k in keys if hashes[k] != prev_hashes.get(k)]
    built = len(to_embed)
    reused = len(keys) - built
    pruned = len([k for k in prev_keys if k not in docs])

    dim = (prev_mat.shape[1] if prev_mat is not None and prev_mat.size
           else embed_query(_embed_text_for(docs[keys[0]])).shape[0]) if keys else 0
    out = np.zeros((len(keys), dim), dtype="float32") if keys else np.zeros((0, 0), "float32")
    new_vecs = embed_texts([_embed_text_for(docs[k]) for k in to_embed]) if to_embed else None
    ni = 0
    for i, k in enumerate(keys):
        if k in to_embed:
            out[i] = new_vecs[ni]; ni += 1
        else:
            out[i] = prev_mat[prev_row[k]]

    d, bin_p, meta_p = _idx_paths(stage)
    os.makedirs(d, exist_ok=True)
    out.tofile(bin_p)
    json.dump({"model": MODEL_DIR.name, "dim": dim, "count": len(keys),
               "keys": keys, "hashes": hashes},
              open(meta_p, "w", encoding="utf-8"))
    return {"built": built, "reused": reused, "pruned": pruned}


def load(stage: str):
    if not available():
        return None
    import numpy as np
    _, bin_p, meta_p = _idx_paths(stage)
    if not (os.path.exists(bin_p) and os.path.exists(meta_p)):
        return None
    meta = json.load(open(meta_p, encoding="utf-8"))
    keys, dim = meta["keys"], meta["dim"]
    mat = np.fromfile(bin_p, dtype="float32")
    mat = mat.reshape(len(keys), dim) if keys and dim else np.zeros((0, dim or 0), "float32")
    return keys, mat
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_vecidx.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vecidx.py scripts/test_vecidx.py
git commit -m "feat(vecidx): incremental content-hash index build/load"
```

---

### Task 4: Vector similarity search with a relevance floor

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py`
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_vecidx.py`

**Interfaces:**
- Consumes: `embed_query`, `load`.
- Produces: `search_vectors(query: str, keys: list[str], matrix, floor: float = 0.35) -> dict[str, float]` — `{key: raw_cosine}` for rows with cosine ≥ `floor`. `VEC_FLOOR = 0.35` module constant.

- [ ] **Step 1: Write the failing test**

```python
# add to scripts/test_vecidx.py
@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class SearchTests(unittest.TestCase):
    def test_floor_excludes_unrelated_and_keeps_related(self):
        keys = ["/rel", "/unrel"]
        mat = vecidx.embed_texts([
            "no outbound network; sockets raise OSError",
            "coffee machine broken on floor three",
        ])
        hits = vecidx.search_vectors("cannot reach the internet", keys, mat, floor=0.2)
        self.assertIn("/rel", hits)
        self.assertGreaterEqual(hits["/rel"], 0.2)
        self.assertNotIn("/unrel", [k for k, v in hits.items() if v >= 0.5])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_vecidx.py`
Expected: FAIL — `search_vectors` undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/vecidx.py
VEC_FLOOR = 0.35


def search_vectors(query, keys, matrix, floor: float = VEC_FLOOR):
    if not keys or matrix is None or matrix.size == 0:
        return {}
    q = embed_query(query)
    sims = matrix @ q
    return {keys[i]: float(sims[i]) for i in range(len(keys)) if float(sims[i]) >= floor}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_vecidx.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vecidx.py scripts/test_vecidx.py
git commit -m "feat(vecidx): cosine search with relevance floor"
```

---

### Task 5: Wire the vec channel into recall.search (dormant default + fallback)

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/recall.py` (add constants near line 46; extend `search` at lines 260-281)
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_recall.py`

**Interfaces:**
- Consumes: `vecidx.load`, `vecidx.search_vectors`, `vecidx.available` (lazy import inside `search`).
- Produces: `recall.search(query, docs, limit=5, stage=None)`; module constants `VEC_WEIGHTS`, `VEC_ACTIVATE_N`; helper `recall._vec_enabled(count) -> bool`. Result rows gain a `"vec"` float. `stage=None` ⇒ identical to today.

- [ ] **Step 1: Write the failing tests**

```python
# add to scripts/test_recall.py, inside RecallTests
    def test_search_without_stage_is_unchanged(self):
        docs = self.corpus({"a.md": "# a\nwidget widget common\n",
                            "b.md": "# b\ncommon common common\n"})
        rows = recall.search("widget", docs)
        self.assertTrue(rows[0]["path"].endswith("a.md"))
        self.assertNotIn("vec", rows[0])  # legacy shape untouched when stage is None

    def test_vec_disabled_env_forces_lexical(self):
        import os
        os.environ["RECALL_VEC"] = "0"
        self.addCleanup(lambda: os.environ.pop("RECALL_VEC", None))
        self.assertFalse(recall._vec_enabled(100000))

    def test_vec_activation_threshold(self):
        import os
        os.environ.pop("RECALL_VEC", None)
        self.assertFalse(recall._vec_enabled(10))
        self.assertTrue(recall._vec_enabled(recall.VEC_ACTIVATE_N))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cp scripts/recall.py /tmp/recall.py && cp scripts/vecidx.py /tmp/vecidx.py && python scripts/test_recall.py`
Expected: FAIL — `_vec_enabled`/`VEC_ACTIVATE_N` undefined.

> Note: `test_recall.py` loads the module from `/tmp/recall.py` (its `MODULE_PATH`). Copy both files to `/tmp` before running so the lazy `import vecidx` inside `search` resolves.

- [ ] **Step 3: Add constants after line 53 in `recall.py`**

```python
# recall.py, after DEFAULT_SALIENCE
VEC_WEIGHTS = {"lex": 0.30, "vec": 0.25, "graph": 0.25, "rec": 0.05, "sal": 0.15}
VEC_ACTIVATE_N = 1000


def _vec_enabled(count):
    flag = os.environ.get("RECALL_VEC")
    if flag == "1":
        return True
    if flag == "0":
        return False
    return count >= VEC_ACTIVATE_N
```

- [ ] **Step 4: Replace `search` (lines 260-281) with the stage-aware version**

```python
def search(query, docs, limit=5, stage=None):
    qt = tokens(query)
    if not qt:
        raise ValueError("query has no usable tokens")
    lex = _normalise(bm25(docs, qt))
    adj = build_edges(docs)
    gph = graph_scores(lex, adj)
    rec = recency_scores(docs)

    vec_raw, vecn, use_vec = {}, {}, False
    if stage is not None:
        try:
            import vecidx
            if vecidx.available() and _vec_enabled(len(docs)):
                loaded = vecidx.load(stage)
                if loaded is not None:
                    keys, mat = loaded
                    present = [k for k in keys if k in docs]
                    if present:
                        idx = [keys.index(k) for k in present]
                        vec_raw = vecidx.search_vectors(query, present, mat[idx])
                        vecn = _normalise({k: max(0.0, v) for k, v in vec_raw.items()})
                        use_vec = True
        except Exception:
            use_vec = False  # any failure => exact lexical fallback

    if use_vec:
        w = VEC_WEIGHTS
    else:
        w = {"lex": W_LEX, "vec": 0.0, "graph": W_GRAPH, "rec": W_REC, "sal": W_SAL}

    rows = []
    for did, d in docs.items():
        g = gph.get(did, 0.0)
        v = vecn.get(did, 0.0)
        total = (w["lex"] * lex[did] + w["vec"] * v + w["graph"] * g
                 + w["rec"] * rec[did] + w["sal"] * d["salience"])
        row = {
            "id": did, "path": d["path"], "title": d["title"],
            "score": total, "lex": lex[did], "graph": g,
            "rec": rec[did], "sal": d["salience"],
            "route": "lexical" if lex[did] > 0 else (
                "graph" if g > 0 else ("vector" if v > 0 else "-")),
            "snippet": _snippet(d["text"], qt),
        }
        if use_vec:
            row["vec"] = vec_raw.get(did, 0.0)
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    keep = [r for r in rows if r["lex"] > 0 or r["graph"] > 0
            or (use_vec and r.get("vec", 0.0) >= vecidx.VEC_FLOOR)]
    return keep[:limit]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cp scripts/recall.py /tmp/recall.py && cp scripts/vecidx.py /tmp/vecidx.py && python scripts/test_recall.py`
Expected: PASS — all legacy tests unchanged (they pass no `stage`), plus the 3 new gating tests.

- [ ] **Step 6: Commit**

```bash
git add scripts/recall.py scripts/test_recall.py
git commit -m "feat(recall): optional dormant vec channel with exact lexical fallback"
```

---

### Task 6: End-to-end hybrid regression on a real staged corpus

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/test_recall.py`

**Interfaces:**
- Consumes: `recall.build_corpus`, `recall.search(..., stage=dir)`, `vecidx.build`.
- Produces: a regression proving a paraphrase-only note (zero lexical overlap) surfaces via the vec channel while exact behavior is preserved.

- [ ] **Step 1: Write the failing test**

```python
# add to scripts/test_recall.py
    def test_hybrid_surfaces_paraphrase_only_note(self):
        import os, importlib.util, sys, shutil, json, tempfile
        vspec = importlib.util.spec_from_file_location("vecidx", Path("/tmp/vecidx.py"))
        vecidx = importlib.util.module_from_spec(vspec); sys.modules["vecidx"] = vecidx
        vspec.loader.exec_module(vecidx)
        if not vecidx.available():
            self.skipTest("vector model not fetched (Task 8)")
        work = tempfile.mkdtemp(prefix="hybrid_", dir="/tmp")
        self.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
        files = {
            "net.md": "# net\nSockets to external hosts raise OSError; no outbound egress.\n",
            "cof.md": "# cof\nThe floor-three coffee machine is broken.\n",
        }
        for n, b in files.items():
            Path(work, n).write_text(b, encoding="utf-8")
        Path(work, "_manifest.json").write_text(
            json.dumps({n: f"/Memory/{n}" for n in files}), encoding="utf-8")
        docs = recall.build_corpus(work)
        vecidx.build(work, docs=docs)
        os.environ["RECALL_VEC"] = "1"
        self.addCleanup(lambda: os.environ.pop("RECALL_VEC", None))
        # query shares NO content token with net.md ("egress"/"sockets" absent)
        hits = recall.search("cannot reach the internet", docs, stage=work)
        self.assertTrue(hits and hits[0]["path"] == "/Memory/net.md", hits)
        self.assertGreater(hits[0]["vec"], 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cp scripts/recall.py scripts/vecidx.py /tmp/ && python scripts/test_recall.py`
Expected: FAIL if the vec join is wrong; SKIP only if the model is not yet fetched. After Task 8 it must PASS.

- [ ] **Step 3: No new implementation** — this test exercises Tasks 3-5. If it fails for a real reason, fix the code in those tasks, not the test.

- [ ] **Step 4: Run to verify (post Task 8) it passes**

Run: `cp scripts/recall.py scripts/vecidx.py /tmp/ && python scripts/test_recall.py`
Expected: PASS or SKIP (SKIP only pre-model).

- [ ] **Step 5: Commit**

```bash
git add scripts/test_recall.py
git commit -m "test(recall): hybrid surfaces paraphrase-only note end-to-end"
```

---

### Task 7: Optional BQ coarse prefilter for scale

**Files:**
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py`
- Test: `C:/Users/z005a5ff/Projects/memnos/scripts/test_vecidx.py`

**Interfaces:**
- Consumes: `numpy`.
- Produces:
  - `pack_bq(matrix) -> "np.ndarray[uint8]"` — sign-bit pack of full-D vectors (D→D/8 bytes).
  - `bq_prefilter(query_bits, doc_bits, k: int) -> list[int]` — row indices of the top-`k` smallest Hamming distances.
  - `VEC_PREFILTER_N = 10000`.
- Rescore stays float cosine (`search_vectors`); BQ only narrows candidates. **Never returns a final score.**

- [ ] **Step 1: Write the failing test**

```python
# add to scripts/test_vecidx.py
@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class BQTests(unittest.TestCase):
    def test_prefilter_keeps_the_true_neighbour(self):
        import numpy as np
        texts = ["no outbound network sockets oserror"] + \
                [f"unrelated filler topic number {i}" for i in range(20)]
        mat = vecidx.embed_texts(texts)
        qbits = vecidx.pack_bq(vecidx.embed_query("cannot reach internet")[None, :])[0]
        dbits = vecidx.pack_bq(mat)
        keep = vecidx.bq_prefilter(qbits, dbits, k=5)
        self.assertIn(0, keep)  # the true neighbour survives the coarse pass
        self.assertEqual(len(keep), 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_vecidx.py`
Expected: FAIL — `pack_bq`/`bq_prefilter` undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/vecidx.py
VEC_PREFILTER_N = 10000


def pack_bq(matrix):
    import numpy as np
    bits = (np.asarray(matrix, dtype="float32") >= 0.0).astype("uint8")
    return np.packbits(bits, axis=1)


def bq_prefilter(query_bits, doc_bits, k: int):
    import numpy as np
    x = np.bitwise_xor(doc_bits, query_bits[None, :])
    ham = np.unpackbits(x, axis=1).sum(axis=1)
    return list(np.argsort(ham, kind="stable")[:k])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_vecidx.py`
Expected: PASS.

- [ ] **Step 5: Wire prefilter into `search_vectors` only above the scale gate**

Replace `search_vectors` body:

```python
def search_vectors(query, keys, matrix, floor: float = VEC_FLOOR):
    if not keys or matrix is None or matrix.size == 0:
        return {}
    import numpy as np
    q = embed_query(query)
    if len(keys) > VEC_PREFILTER_N:
        qbits = pack_bq(q[None, :])[0]
        cand = bq_prefilter(qbits, pack_bq(matrix), k=max(50, floor and 50))
    else:
        cand = range(len(keys))
    out = {}
    for i in cand:
        c = float(matrix[i] @ q)          # float rescore is always the final score
        if c >= floor:
            out[keys[i]] = c
    return out
```

- [ ] **Step 6: Run both test files to verify green**

Run: `python scripts/test_vecidx.py && cp scripts/recall.py scripts/vecidx.py /tmp/ && python scripts/test_recall.py`
Expected: PASS/SKIP throughout.

- [ ] **Step 7: Commit**

```bash
git add scripts/vecidx.py scripts/test_vecidx.py
git commit -m "feat(vecidx): BQ coarse prefilter above scale gate, float rescore final"
```

---

### Task 8: Model fetch, CLI build, recall wiring, and portability doc

**Files:**
- Create: `C:/Users/z005a5ff/Projects/memnos/scripts/vectors/fetch_model.py`
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/vecidx.py` (add `main`/CLI)
- Modify: `C:/Users/z005a5ff/Projects/memnos/scripts/recall.py` (`main` passes `stage=STAGE`)
- Create: `C:/Users/z005a5ff/Projects/memnos/docs/VECTOR-ANCHOR-OPS.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `python scripts/vectors/fetch_model.py` materializes the model locally; `python scripts/vecidx.py build <dir>` builds an index; `recall.py` query path uses the index when active.

- [ ] **Step 1: Write the one-time fetch script**

```python
# scripts/vectors/fetch_model.py
#!/usr/bin/env python3
"""Run ONCE with network to materialize the static model locally.
Thereafter MNEMOS runs fully offline. Ships nothing at runtime but the dir."""
from pathlib import Path
from model2vec import StaticModel

DEST = Path(__file__).resolve().parent / "potion-base-8M"
StaticModel.from_pretrained("minishlab/potion-base-8M").save_pretrained(str(DEST))
print("model saved to", DEST)
```

- [ ] **Step 2: Fetch the model and verify availability flips True**

```bash
cd /c/Users/z005a5ff/Projects/memnos && python scripts/vectors/fetch_model.py
python -c "import sys; sys.path.insert(0,'scripts'); import vecidx; print('available:', vecidx.available())"
```
Expected: `available: True`.

- [ ] **Step 3: Add a `build` CLI to `vecidx.py`**

```python
# append to scripts/vecidx.py
def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "build":
        if not available():
            print("vector extra unavailable (need numpy+model2vec+model dir)", file=sys.stderr)
            return 2
        stats = build(argv[1])
        print("index:", stats)
        return 0
    print("usage: vecidx.py build <stage_dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Make `recall.main` pass the stage so live queries can use vectors**

In `recall.py` `main`, change the `rows = search(query, docs, limit)` call to:

```python
        rows = search(query, docs, limit, stage=STAGE)
```

- [ ] **Step 5: Full-suite run (both files + selftests)**

```bash
cd /c/Users/z005a5ff/Projects/memnos && cp scripts/recall.py scripts/vecidx.py /tmp/
python scripts/test_vecidx.py && python scripts/test_recall.py && python /tmp/recall.py --selftest
```
Expected: all PASS; `recall.py --selftest` still green (it passes no stage internally → lexical, unchanged).

- [ ] **Step 6: Write the ops doc**

```markdown
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
```

- [ ] **Step 7: Commit**

```bash
git add scripts/vectors/fetch_model.py scripts/vecidx.py scripts/recall.py docs/VECTOR-ANCHOR-OPS.md
git commit -m "feat(vecidx): model fetch, build CLI, recall wiring, ops doc"
```

---

## Self-Review

**1. Spec coverage**
- §1/§1.1 decision + prior-rejection overturn → framed in header + Task 8 offline model (no fetch at inference) and numpy cosine (Task 2/4). ✓
- §2 goals: semantic channel (T5), Markdown-as-truth + rebuildable (T3, ops doc), no-network/no-daemon (T8 local model), graceful fallback (T5 tests), self-kept content hash (T3). ✓
- §2 non-goals: ADS/xattr/trailer never implemented (only `.idx/` sidecar, T3); stdlib-hashing not used; BQ-only rejected (T7 rescore-final); governance untouched (recall-only edits). ✓
- §3.1 placement / §3.2 weights (`VEC_WEIGHTS`, floor) → T5. Two-pass (prefilter+rescore) → T7. ✓
- §3.3 self-kept content hash → T3. ✓
- §4 consolidated `.idx/vectors.bin`+`.json`, model tag stamped → T3. ✓
- §5 static embedder, numpy-only, offline, fallback → T2/T8. ✓
- §6 activation policy (dormant, RECALL_VEC, 1000 threshold) → T5 `_vec_enabled`. ✓
- §7 axioms preserved (derived cache, reversible) → T3 + ops doc. ✓
- §8 eval: exact-match non-regression (T5 legacy tests), paraphrase lift (T6), freshness correctness (T3), fallback (T5) → covered as regression tests; a **larger real-corpus R@k eval on `Memory/**` is deferred** and called out below.

**2. Placeholder scan** — no TBD/TODO; every code step carries real code. ✓

**3. Type consistency** — `build(stage, docs=None) -> {built,reused,pruned}`, `load(stage) -> (keys, matrix)|None`, `search_vectors(query, keys, matrix, floor) -> {key: cos}`, `search(query, docs, limit, stage)`, `_vec_enabled(count)`, `VEC_FLOOR`/`VEC_WEIGHTS`/`VEC_ACTIVATE_N`/`VEC_PREFILTER_N` — names match across tasks. ✓

**Known deferral (not a gap):** spec §8's *real-corpus paraphrase R@k benchmark* against the live `Memory/**` corpus is a measurement task to run after activation, not a build step; add it as a follow-up once the corpus nears the threshold. The spike (spec §9) is the interim evidence.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-09-02-mnemos-vector-anchor.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
