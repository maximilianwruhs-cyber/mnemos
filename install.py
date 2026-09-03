#!/usr/bin/env python3
"""Install the sanitized MNEMOS portable kit into a target workspace."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DIRECTORIES = [
    "Memory/context", "Memory/lessons", "Memory/decisions", "Memory/preferences",
    "Memory/daily", "Memory/_archive", "handoffs", "verifier/reports",
    "autonomy/state", "autonomy/audit", "autonomy/reports/pending",
    "autonomy/reports/processing", "autonomy/reports/archive",
    "autonomy/reports/rejected", "autonomy/tasks/pending",
    "autonomy/tasks/processing", "autonomy/tasks/archive", "autonomy/tasks/failed",
    "autonomy/handoffs/pending", "autonomy/handoffs/processing",
    "autonomy/handoffs/accepted", "autonomy/handoffs/rejected",
    "autonomy/artifacts", "autonomy/snapshots", "autonomy/evolution/pending",
    "autonomy/evolution/archive", "autonomy/evolution/rejected",
]


def copy_file(src: Path, dst: Path, force: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {dst}; use --force")
    shutil.copy2(src, dst)


def install(target: Path, force: bool = False) -> list[str]:
    target = target.expanduser().resolve()
    if target == ROOT or ROOT in target.parents:
        raise ValueError("target must be outside the package directory")
    target.mkdir(parents=True, exist_ok=True)
    writes: list[str] = []

    mappings = [
        (ROOT / "templates/root", target),
        (ROOT / "templates/memory", target / "Memory"),
        (ROOT / "templates/autonomy-state", target / "autonomy/state"),
        (ROOT / "scripts", target / "scripts"),
        (ROOT / "autonomy/config", target / "autonomy/config"),
        (ROOT / "verifier", target / "verifier"),
        (ROOT / "docs", target / "docs"),
    ]
    for src_root, dst_root in mappings:
        if not src_root.exists():
            raise FileNotFoundError(src_root)
        for src in sorted(p for p in src_root.rglob("*") if p.is_file()):
            dst = dst_root / src.relative_to(src_root)
            copy_file(src, dst, force)
            writes.append(dst.relative_to(target).as_posix())

    copy_file(ROOT / "docs/AUTONOMY-README.md", target / "autonomy/README.md", force)
    writes.append("autonomy/README.md")
    copy_file(ROOT / "docs/MEMORY-PROTOCOL.md", target / "Memory/PROTOCOL.md", force)
    writes.append("Memory/PROTOCOL.md")
    for src in sorted((ROOT / "templates/skills").glob("*.md")):
        dst = target / "Skills" / src.stem / "SKILL.md"
        copy_file(src, dst, force)
        writes.append(dst.relative_to(target).as_posix())

    for rel in DIRECTORIES:
        (target / rel).mkdir(parents=True, exist_ok=True)
    for rel in ("autonomy/audit/events.jsonl", "autonomy/audit/evolution.jsonl"):
        path = target / rel
        if path.exists() and not force:
            raise FileExistsError(f"refusing to overwrite {path}; use --force")
        path.write_text("", encoding="utf-8")
        writes.append(rel)

    receipt = {
        "schema_version": 1,
        "source_package": ROOT.name,
        "target": str(target),
        "files_written": sorted(set(writes)),
        "schedules_created": False,
        "operator_state_copied": False,
    }
    (target / "INSTALL-RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return sorted(set(writes))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        writes = install(args.target, args.force)
    except Exception as exc:
        print(f"INSTALL REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"INSTALLED {len(writes)} files into {args.target.resolve()}")
    print("No schedules were created. Customize root templates and re-probe the runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
