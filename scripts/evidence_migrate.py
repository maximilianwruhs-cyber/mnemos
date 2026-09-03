#!/usr/bin/env python3
"""Deterministic, side-effect-free migration dry-run planner for MNEMOS.

Given the current L2 (``MEMORY.md``) text, the active L3 corpus, and an
operator-authored decision file, project the evidence-schema migration and
report whether it is ready to apply. The planner NEVER writes MEMORY or L3
files, NEVER invents a quote or source (evidence text comes only from the
decision file), and NEVER appends past a corrupt ledger.

Exit status (CLI): 0 when the report is ready, 1 for a complete blocked
report, 2 for an I/O / JSON / manifest fatal error.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
import graphcheck  # noqa: E402
import mnemos  # noqa: E402

MAX_L2_LINES = 200
MAX_L2_BYTES = 12288

L2_PATH = "/MEMORY.md"
ARCHIVE_ROOT = "Memory/_archive/evidence-migration"
EXCLUDED_L3_NAMES = {"INDEX.md", "INDEX-L3.md", "PROTOCOL.md"}


@dataclass(frozen=True)
class MigrationRow:
    note_id: str
    path: str
    target_path: str | None
    confidence: str
    status: str
    delta_bytes: int
    detail: str


@dataclass(frozen=True)
class MigrationReport:
    rows: tuple[MigrationRow, ...]
    projected_l2_lines: int
    projected_l2_bytes: int
    ready: bool


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "note"


def _confidence(text: str) -> str:
    for m in mnemos.FIELD_RE.finditer(text):
        if m.group(1).strip() == "Confidence":
            return m.group(2).strip()
    return ""


def _archive_target(kind: str, note_id: str, path: str, title: str) -> str:
    if kind == "L2":
        return f"{ARCHIVE_ROOT}/l2/{note_id}-{_slug(title)}.md"
    rest = path.split("/Memory/", 1)[1] if "/Memory/" in path else path.lstrip("/")
    return f"{ARCHIVE_ROOT}/{rest}"


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _collect_links(memory_text: str, l3_docs: dict[str, str]) -> dict[str, set[str]]:
    """id -> set of active sources ([[id]]) that point at it, self excluded."""
    sources: dict[str, set[str]] = {}
    for n in mnemos.parse_notes(memory_text):
        for tgt in set(mnemos.LINK_RE.findall(n["body"])):
            if tgt != n["id"]:
                sources.setdefault(tgt, set()).add(n["id"])
    for path, text in l3_docs.items():
        self_id = graphcheck.l3_full_note_id(path, text)
        for tgt in set(mnemos.LINK_RE.findall(text)):
            if tgt != self_id:
                sources.setdefault(tgt, set()).add(path)
    return sources


def _collect_stub_targets(memory_text: str) -> dict[str, str]:
    """Active-L3 store path -> the L2 stub id that still points at it."""
    targets: dict[str, str] = {}
    for n in mnemos.parse_notes(memory_text):
        if not n["stub"]:
            continue
        for rel in graphcheck.L3PATH_RE.findall(n["body"]):
            targets["/" + rel.lstrip("/")] = n["id"]
    return targets


def plan(memory_text: str, l3_docs: dict[str, str],
         decisions: dict[str, dict], today: date) -> MigrationReport:
    """Project the evidence migration without touching any corpus file."""
    entities: list[dict] = []
    id_counts: dict[str, int] = {}

    for n in mnemos.parse_notes(memory_text):
        if n["stub"]:
            continue
        entities.append({
            "id": n["id"], "kind": "L2", "path": L2_PATH, "title": n["title"],
            "body": n["body"], "confidence": n["fields"].get("Confidence", ""),
            "body_start": n["body_start"], "scan_start": n["scan_start"],
            "scan_end": n["scan_end"],
        })
        id_counts[n["id"]] = id_counts.get(n["id"], 0) + 1

    for path in sorted(l3_docs):
        text = l3_docs[path]
        fid = graphcheck.l3_full_note_id(path, text)
        if fid is None:
            continue
        entities.append({
            "id": fid, "kind": "L3", "path": path, "title": "",
            "body": text, "confidence": _confidence(text),
        })
        id_counts[fid] = id_counts.get(fid, 0) + 1

    dup_ids = {i for i, c in id_counts.items() if c > 1}
    link_sources = _collect_links(memory_text, l3_docs)
    stub_targets = _collect_stub_targets(memory_text)

    rows: list[MigrationRow] = []
    ready = True
    used_targets: dict[str, str] = {}
    decisions_seen: set[str] = set()
    l2_edits: list[tuple[int, int, str]] = []

    def block(ent, detail, target=None):
        nonlocal ready
        ready = False
        rows.append(MigrationRow(ent["id"], ent["path"], target,
                                 ent["confidence"], "BLOCKED", 0, detail))

    for ent in entities:
        eid, body = ent["id"], ent["body"]
        d = decisions.get(eid)
        if d is not None:
            decisions_seen.add(eid)

        if eid in dup_ids:
            block(ent, "duplicate active self-ID")
            continue

        has_labeled = bool(evidence.EVIDENCE_RE.search(body))
        report = evidence.inspect(body, today)
        valid = not any(f.level == "FAIL" for f in report.findings)

        if valid:
            if d is None:
                rows.append(MigrationRow(eid, ent["path"], None,
                                         ent["confidence"], "READY", 0,
                                         "evidence present"))
            else:
                block(ent, f"stale decision '{d.get('action')}' for valid note")
            continue

        if d is None:
            block(ent, "invalid ledger and no decision")
            continue

        action = d.get("action")
        if action == "ARCHIVE":
            target = _archive_target(ent["kind"], eid, ent["path"], ent["title"])
            if target in used_targets:
                block(ent, f"duplicate archive target {target}", target)
                continue
            used_targets[target] = eid
            targeted = bool(link_sources.get(eid))
            if ent["kind"] == "L3" and stub_targets.get(ent["path"]):
                targeted = True
            if targeted:
                ready = False
                rows.append(MigrationRow(eid, ent["path"], target,
                                         ent["confidence"], "ARCHIVE", 0,
                                         "archive blocked by active incoming link/stub"))
                continue
            delta = 0
            if ent["kind"] == "L2":
                span = memory_text[ent["scan_start"]:ent["scan_end"]]
                delta = -len(span.encode("utf-8"))
                l2_edits.append((ent["scan_start"], ent["scan_end"], ""))
            rows.append(MigrationRow(eid, ent["path"], target,
                                     ent["confidence"], "ARCHIVE", delta, "archive"))
            continue

        if action == "ADD":
            if has_labeled:
                block(ent, "existing evidence malformed; repair or archive")
                continue
            ev = d.get("evidence")
            try:
                projected = evidence.append(body, ev, today)
            except (ValueError, TypeError) as exc:
                block(ent, f"invalid proposed evidence: {exc}")
                continue
            delta = len(projected.encode("utf-8")) - len(body.encode("utf-8"))
            rows.append(MigrationRow(eid, ent["path"], None,
                                     ent["confidence"], "READY", delta,
                                     "evidence added"))
            if ent["kind"] == "L2":
                l2_edits.append((ent["body_start"], ent["scan_end"], projected))
            continue

        block(ent, f"unsupported action '{action}'")

    for did in sorted(decisions):
        if did not in decisions_seen:
            ready = False
            rows.append(MigrationRow(did, "", None, "", "BLOCKED", 0,
                                     "decision for unknown or inactive note"))

    projected_text = _apply_edits(memory_text, l2_edits)
    projected_l2_bytes = len(projected_text.encode("utf-8"))
    projected_l2_lines = len(projected_text.splitlines())
    if projected_l2_lines > MAX_L2_LINES or projected_l2_bytes > MAX_L2_BYTES:
        ready = False

    rows.sort(key=lambda r: (r.note_id, r.path))
    return MigrationReport(tuple(rows), projected_l2_lines,
                           projected_l2_bytes, ready)


def report_to_json(report: MigrationReport) -> str:
    payload = {
        "ready": report.ready,
        "projected_l2_lines": report.projected_l2_lines,
        "projected_l2_bytes": report.projected_l2_bytes,
        "rows": [
            {
                "note_id": r.note_id,
                "path": r.path,
                "target_path": r.target_path,
                "confidence": r.confidence,
                "status": r.status,
                "delta_bytes": r.delta_bytes,
                "detail": r.detail,
            }
            for r in report.rows
        ],
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _load_l3_docs(manifest: dict[str, str], stage_dir: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for staged_name, real_path in manifest.items():
        if not (real_path.startswith("/Memory/") and real_path.endswith(".md")):
            continue
        if "/_archive/" in real_path:
            continue
        if real_path.rsplit("/", 1)[-1] in EXCLUDED_L3_NAMES:
            continue
        staged = stage_dir / staged_name
        if staged.exists():
            docs[real_path] = staged.read_text(encoding="utf-8")
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)

    try:
        memory_text = Path(args.memory).read_text(encoding="utf-8")
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
        decisions_raw = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        if not isinstance(decisions_raw, dict):
            raise ValueError("decision file must be a JSON object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    decisions = decisions_raw.get("notes", {})
    if not isinstance(decisions, dict):
        print("FATAL: decision file 'notes' must be an object", file=sys.stderr)
        return 2

    today = date.fromisoformat(args.today) if args.today else graphcheck._today()
    l3_docs = _load_l3_docs(manifest, manifest_path.parent)
    report = plan(memory_text, l3_docs, decisions, today)
    Path(args.output).write_text(report_to_json(report), encoding="utf-8")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
