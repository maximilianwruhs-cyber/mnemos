#!/usr/bin/env python3
"""vecidx.py - derived, rebuildable semantic vector index for MNEMOS recall.

Markdown stays the source of truth; this module writes a .idx/ cache that
recall.py may join at query time. All heavy deps (numpy, model2vec) are lazy
and gated behind available(); MNEMOS core stays stdlib-only.
"""
from __future__ import annotations
import hashlib
import importlib.util
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
