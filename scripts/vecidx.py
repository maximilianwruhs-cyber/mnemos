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
import warnings
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
        # model2vec opens config.json without closing; silence its ResourceWarning
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
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


def _model_fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("config.json", "modules.json"):
        p = MODEL_DIR / name
        if p.exists():
            h.update(p.read_bytes())
    st = (MODEL_DIR / "model.safetensors").stat()
    h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()


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
        with open(meta_p, encoding="utf-8") as f:
            prev_hashes = json.load(f).get("hashes", {})
    prev_row = {k: i for i, k in enumerate(prev_keys)}

    keys = sorted(docs)
    hashes = {k: content_hash(_embed_text_for(docs[k])) for k in keys}
    to_embed = [k for k in keys if hashes[k] != prev_hashes.get(k)]
    built = len(to_embed)
    reused = len(keys) - built
    pruned = len([k for k in prev_keys if k not in docs])

    fresh = set(to_embed)
    new_vecs = embed_texts([_embed_text_for(docs[k]) for k in to_embed]) if to_embed else None
    if new_vecs is not None:
        dim = int(new_vecs.shape[1])
    elif prev_mat is not None and prev_mat.size:
        dim = int(prev_mat.shape[1])
    else:
        dim = 0
    out = np.zeros((len(keys), dim), dtype="float32") if keys else np.zeros((0, 0), "float32")
    ni = 0
    for i, k in enumerate(keys):
        if k in fresh:
            out[i] = new_vecs[ni]; ni += 1
        else:
            out[i] = prev_mat[prev_row[k]]


    d, bin_p, meta_p = _idx_paths(stage)
    os.makedirs(d, exist_ok=True)
    out.tofile(bin_p)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump({"model": MODEL_DIR.name, "model_hash": _model_fingerprint(),
                   "dim": dim, "count": len(keys),
                   "keys": keys, "hashes": hashes}, f)
    return {"built": built, "reused": reused, "pruned": pruned}


def load(stage: str):
    if not available():
        return None
    import numpy as np
    _, bin_p, meta_p = _idx_paths(stage)
    if not (os.path.exists(bin_p) and os.path.exists(meta_p)):
        return None
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("model") != MODEL_DIR.name:
        return None
    if meta.get("model_hash") != _model_fingerprint():
        return None
    keys, dim = meta["keys"], meta["dim"]
    mat = np.fromfile(bin_p, dtype="float32")
    if mat.size != len(keys) * dim:
        return None
    mat = mat.reshape(len(keys), dim) if keys and dim else np.zeros((0, dim or 0), "float32")
    return keys, mat


VEC_FLOOR = 0.35
VEC_PREFILTER_N = 10000
VEC_PREFILTER_K = 50


def pack_bq(matrix):
    import numpy as np
    bits = (np.asarray(matrix, dtype="float32") >= 0.0).astype("uint8")
    return np.packbits(bits, axis=1)


def bq_prefilter(query_bits, doc_bits, k: int):
    import numpy as np
    x = np.bitwise_xor(doc_bits, query_bits[None, :])
    ham = np.unpackbits(x, axis=1).sum(axis=1)
    return list(np.argsort(ham, kind="stable")[:k])


def search_vectors(query, keys, matrix, floor: float = VEC_FLOOR):
    if not keys or matrix is None or matrix.size == 0:
        return {}
    q = embed_query(query)
    if len(keys) > VEC_PREFILTER_N:
        qbits = pack_bq(q[None, :])[0]
        cand = bq_prefilter(qbits, pack_bq(matrix), k=VEC_PREFILTER_K)
    else:
        cand = range(len(keys))
    out = {}
    for i in cand:
        c = float(matrix[i] @ q)          # float rescore is always the final score
        if c >= floor:
            out[keys[i]] = c
    return out


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) == 2 and argv[0] == "build":
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

