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
