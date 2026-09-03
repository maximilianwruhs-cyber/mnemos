#!/usr/bin/env python3
"""graphcheck.py - cross-tier link integrity for the MNEMOS substrate.

`mnemos.py` validates L2 (MEMORY.md) in isolation: schema, caps, and links
*between notes that live in MEMORY.md*. It is blind to L3. A stub whose
`Memory/...md` target was renamed or deleted still passes, because nothing
ever opens the target. This closes that hole.

Checks
------
C1  every L2 stub names an L3 path, and that path exists
C2  every [[MEM-xxxx]] inside an L3 file resolves to a real L2 note
C3  every Memory/context/MEM-*.md file corresponds to an L2 note or stub
C4  orphan L3 files: reachable from no L2 note, INDEX, or AGENTS reference
C5  back-link asymmetry: L2 stub -> L3 file, but the file omits its own ID

Sandbox contract
----------------
The sandbox cannot walk the FileStore, so the caller stages every substrate
file flat into a directory and supplies a manifest mapping
    staged filename -> real store path
as JSON at <stage>/_manifest.json.

No argparse: MEM-2026-0007 - the harness pre-populates sys.argv with junk,
so this reads configuration from the manifest and constants only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

STAGE = Path("/tmp")
MANIFEST = STAGE / "_manifest.json"

NOTE_RE = re.compile(r"^### \[(MEM-\d{4}-\d{4})\]\s+(.+?)\s*$", re.M)
STUB_RE = re.compile(r"^\s*-\s+\*\*Stub\.\*\*", re.M)
LINK_RE = re.compile(r"\[\[(MEM-\d{4}-\d{4})\]\]")
L3PATH_RE = re.compile(r"(Memory/[\w./-]+\.md)")
MEMID_RE = re.compile(r"(MEM-\d{4}-\d{4})")

OK, BAD = "  [OK  ]", "  [FAIL]"


def load_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        print("FATAL: no manifest at", MANIFEST, file=sys.stderr)
        raise SystemExit(2)
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def read(name: str) -> str:
    p = STAGE / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def parse_l2(text: str) -> dict[str, dict]:
    notes: dict[str, dict] = {}
    hits = list(NOTE_RE.finditer(text))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[m.end():end]
        cut = body.find("\n## ")
        if cut >= 0:
            body = body[:cut]
        notes[m.group(1)] = {
            "title": m.group(2).strip(),
            "body": body,
            "stub": bool(STUB_RE.search(body)),
            "l3": L3PATH_RE.findall(body),
            "links": LINK_RE.findall(body),
        }
    return notes


def main() -> int:
    manifest = load_manifest()
    by_path = {v: k for k, v in manifest.items()}          # real path -> staged name
    l2_text = read(by_path.get("/MEMORY.md", "MEMORY.md"))
    notes = parse_l2(l2_text)

    l3_paths = sorted(p for p in manifest.values()
                      if p.startswith("/Memory/") and p.endswith(".md")
                      and "/_archive/" not in p
                      and p not in ("/Memory/INDEX.md", "/Memory/PROTOCOL.md",
                                    "/Memory/INDEX-L3.md"))

    findings: list[tuple[bool, str, str]] = []
    referenced: dict[str, set[str]] = {p: set() for p in l3_paths}

    # ---- C1: any note declaring an L3 path -> target exists ---------------
    for nid, n in notes.items():
        if n["stub"] and not n["l3"]:
            findings.append((False, nid, "stub declares no L3 path"))
            continue
        for rel in n["l3"]:
            full = "/" + rel.lstrip("/")
            if full in referenced:
                referenced[full].add(nid)
                findings.append((True, nid, f"stub target resolves -> {rel}"))
            else:
                findings.append((False, nid, f"DANGLING stub target -> {rel}"))

    # ---- C5: back-link symmetry -------------------------------------------
    for nid, n in notes.items():
        for rel in n["l3"]:
            full = "/" + rel.lstrip("/")
            staged = by_path.get(full)
            if not staged:
                continue
            if nid not in read(staged):
                findings.append((False, nid, f"L3 file omits its own ID ({rel})"))

    # ---- C2: [[MEM-]] inside L3 resolves ----------------------------------
    for path in l3_paths:
        staged = by_path[path]
        body = read(staged)
        for target in set(LINK_RE.findall(body)):
            if target in notes:
                referenced[path].add(target)
                findings.append((True, path, f"link {target} resolves"))
            else:
                findings.append((False, path, f"unresolved link {target}"))

    # ---- C3: Memory/context/MEM-*.md has an L2 counterpart -----------------
    for path in l3_paths:
        if "/context/" not in path:
            continue
        ids = MEMID_RE.findall(Path(path).name)
        for mid in ids:
            if mid in notes:
                referenced[path].add(mid)
                findings.append((True, path, f"has L2 counterpart {mid}"))
            else:
                findings.append((False, path, f"no L2 note for {mid}"))

    # ---- C4: orphans -------------------------------------------------------
    prose = "".join(read(by_path[p]) for p in ("/AGENTS.md", "/Memory/INDEX.md")
                    if p in by_path)
    orphans = []
    for path in l3_paths:
        if referenced[path]:
            continue
        if path.lstrip("/") in prose:
            referenced[path].add("AGENTS/INDEX prose")
            continue
        if "/daily/" in path:            # daily digests are intentionally unlinked
            continue
        orphans.append(path)

    # ---- report ------------------------------------------------------------
    print("=" * 74)
    print("MNEMOS CROSS-TIER GRAPH CHECK")
    print("=" * 74)
    print(f"  L2 notes: {len(notes)}   L3 files: {len(l3_paths)}")
    print()
    fails = [f for f in findings if not f[0]]
    for ok, who, msg in findings:
        print(f"{OK if ok else BAD} {who:<46} {msg}")
    print()
    if orphans:
        print("--- ORPHAN L3 FILES (reachable from nothing) ---")
        for o in orphans:
            print("  !", o)
    else:
        print("  no orphan L3 files")
    print()

    # ---- emit the L3 registry ---------------------------------------------
    reg = ["# Memory Graph Index - L3 (archival tier)", "",
           "> Regenerated by `scripts/graphcheck.py`. Do not hand-edit.",
           "> `mnemos.py` covers L2; this covers everything under `Memory/`.", "",
           "| File | Category | Reached by |", "|---|---|---|"]
    for path in l3_paths:
        cat = path.split("/")[2] if len(path.split("/")) > 3 else "-"
        src = ", ".join(sorted(referenced[path])) or "_(unreferenced)_"
        reg.append(f"| `{path}` | {cat} | {src} |")
    reg += ["", "## Counters", "",
            f"- L3 files: {len(l3_paths)}",
            f"- Orphans: {len(orphans)}",
            f"- Cross-tier failures: {len(fails)}", ""]
    registry = "\n".join(reg)
    (STAGE / "INDEX-L3.out.md").write_text(registry, encoding="utf-8")

    print("=" * 74)
    print("REGISTRY (write verbatim to /Memory/INDEX-L3.md)")
    print("=" * 74)
    print(registry)

    print("=" * 74)
    print(f"  cross-tier failures: {len(fails)}   orphans: {len(orphans)}")
    print("  VERDICT:", "PASS" if not fails and not orphans else "FAIL")
    return 0 if (not fails and not orphans) else 1


if __name__ == "__main__":
    raise SystemExit(main())
