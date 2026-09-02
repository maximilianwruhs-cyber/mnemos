#!/usr/bin/env python3
"""vecidx.py - derived, rebuildable semantic vector index for MNEMOS recall.

Markdown stays the source of truth; this module writes a .idx/ cache that
recall.py may join at query time. All heavy deps (numpy, model2vec) are lazy
and gated behind available(); MNEMOS core stays stdlib-only.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "vectors" / "potion-base-8M"
IDX_DIRNAME = ".idx"

_MODEL = None


def available() -> bool:
    if importlib.util.find_spec("numpy") is None:
        return False
    if importlib.util.find_spec("model2vec") is None:
        return False
    return MODEL_DIR.exists()


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
