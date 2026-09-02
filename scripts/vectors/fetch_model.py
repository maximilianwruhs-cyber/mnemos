#!/usr/bin/env python3
"""Run ONCE with network to materialize the static model locally.
Thereafter MNEMOS runs fully offline. Ships nothing at runtime but the dir."""
from pathlib import Path

DEST = Path(__file__).resolve().parent / "potion-base-8M"


def main():
    if DEST.exists():
        print("model already present at", DEST)
        return 0
    from model2vec import StaticModel
    StaticModel.from_pretrained("minishlab/potion-base-8M").save_pretrained(str(DEST))
    print("model saved to", DEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
