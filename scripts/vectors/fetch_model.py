#!/usr/bin/env python3
"""Run ONCE with network to materialize the static model locally.
Thereafter MNEMOS runs fully offline. Ships nothing at runtime but the dir."""
from pathlib import Path
from model2vec import StaticModel

DEST = Path(__file__).resolve().parent / "potion-base-8M"
StaticModel.from_pretrained("minishlab/potion-base-8M").save_pretrained(str(DEST))
print("model saved to", DEST)
