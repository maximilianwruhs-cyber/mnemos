#!/usr/bin/env python3
"""MNEMOS v1.1: validate and index the local markdown memory substrate.

Stdlib-only, offline, and deterministic. Validation failures return a non-zero
exit status. A regenerated INDEX.md is written even on failure for diagnosis.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

CAPS = {
    "AGENTS.md": {"lines": 120, "bytes": 32768},
    "MEMORY.md": {"lines": 200, "bytes": 12288},
}
MAX_L2_NOTES = 12
W_R, W_I, W_V = 0.25, 0.35, 0.40
DELTA = 0.995
GAMMA = 0.6
LAMBDA = 0.01
TAU = 0.35
THETA_LOW = 0.30
VALID_CONF = {"VERIFIED", "HIGH", "MEDIUM", "LOW"}
VALID_TYPE = {
    "Failure-Mode", "Gotcha", "Decision", "Preference",
    "Environment-Invariant", "Domain-Fact",
}
REQUIRED_FIELDS = {
    "Type", "Confidence", "Salience", "Created", "Last-Access", "Freq",
    "Tags", "Links", "Provenance", "Observation", "Directive",
}
NOTE_RE = re.compile(r"^### \[(MEM-\d{4}-\d{4})\]\s+(.+?)\s*$", re.M)
LINK_RE = re.compile(r"\[\[(MEM-\d{4}-\d{4})\]\]")
FIELD_RE = re.compile(r"\*\*([A-Za-z-]+)(?:\s*\([^)]*\))?:\*\*[ \t]*([^\n·]*)")
STUB_RE = re.compile(r"^\s*-\s+\*\*Stub\.\*\*", re.M)
TAG_RE = re.compile(r"#[\w-]+")


@dataclass(frozen=True)
class Finding:
    level: str
    subject: str
    detail: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return None


def parse_notes(text: str) -> list[dict]:
    notes: list[dict] = []
    matches = list(NOTE_RE.finditer(text))
    for pos, match in enumerate(matches):
        end = matches[pos + 1].start() if pos + 1 < len(matches) else len(text)
        body = text[match.end():end]
        section_end = body.find("\n## ")
        if section_end >= 0:
            body = body[:section_end]
        fields = {m.group(1).strip(): m.group(2).strip() for m in FIELD_RE.finditer(body)}
        notes.append({
            "id": match.group(1),
            "title": match.group(2).strip(),
            "body": body,
            "fields": fields,
            "stub": bool(STUB_RE.search(body)),
            "links": LINK_RE.findall(fields.get("Links", "")),
        })
    return notes


def check_file(path: Path, name: str) -> list[Finding]:
    limits = CAPS[name]
    text = read_text(path)
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    return [
        Finding("FAIL" if lines > limits["lines"] else "PASS", name,
                f"{lines} lines (cap {limits['lines']})"),
        Finding("FAIL" if size > limits["bytes"] else "PASS", name,
                f"{size} bytes (cap {limits['bytes']})"),
    ]


def check_notes(notes: list[dict], today: date) -> list[Finding]:
    findings: list[Finding] = []
    ids = [note["id"] for note in notes]
    id_set = set(ids)
    if not notes:
        findings.append(Finding("FAIL", "MEMORY.md", "no atomic notes parsed"))
        return findings
    if len(notes) > MAX_L2_NOTES:
        findings.append(Finding("FAIL", "MEMORY.md",
                                f"L2 occupancy {len(notes)} exceeds cap {MAX_L2_NOTES}"))
    for duplicate in sorted({nid for nid in ids if ids.count(nid) > 1}):
        findings.append(Finding("FAIL", duplicate, "duplicate note ID"))

    for note in notes:
        nid, fields = note["id"], note["fields"]
        if note.get("stub"):
            # L3 demotion stub: AGENTS.md permits a one-line pointer in place of a
            # full note. Validate the stub contract, not the full note schema.
            body = note["body"]
            if not re.search(r"\b(VERIFIED|HIGH|MEDIUM|LOW)\b", body):
                findings.append(Finding("FAIL", nid, "stub missing confidence tier"))
            elif not re.search(r"Memory/[\w./-]+\.md", body):
                findings.append(Finding("FAIL", nid, "stub missing L3 full-note path"))
            else:
                findings.append(Finding("PASS", nid, "stub contract complete"))
            continue
        missing = sorted(REQUIRED_FIELDS - fields.keys())
        if missing:
            findings.append(Finding("FAIL", nid, f"missing fields: {', '.join(missing)}"))
        else:
            findings.append(Finding("PASS", nid, "schema complete"))

        conf = fields.get("Confidence", "")
        if conf not in VALID_CONF:
            findings.append(Finding("FAIL", nid, f"invalid confidence '{conf}'"))
        note_type = fields.get("Type", "")
        if note_type not in VALID_TYPE:
            findings.append(Finding("FAIL", nid, f"invalid type '{note_type}'"))

        salience = parse_float(fields.get("Salience", ""))
        if salience is None or not 0.0 <= salience <= 1.0:
            findings.append(Finding("FAIL", nid, f"invalid salience '{fields.get('Salience', '')}'"))
        freq = parse_int(fields.get("Freq", ""))
        if freq is None or freq < 0:
            findings.append(Finding("FAIL", nid, f"invalid frequency '{fields.get('Freq', '')}'"))

        created = parse_date(fields.get("Created", ""))
        accessed = parse_date(fields.get("Last-Access", ""))
        for label, parsed in (("Created", created), ("Last-Access", accessed)):
            if parsed is None:
                findings.append(Finding("FAIL", nid, f"invalid {label} date"))
            elif parsed > today:
                findings.append(Finding("FAIL", nid, f"{label} date is in the future"))
        if created and accessed and accessed < created:
            findings.append(Finding("FAIL", nid, "Last-Access precedes Created"))

        provenance = fields.get("Provenance", "").strip()
        if not provenance:
            findings.append(Finding("FAIL", nid, "empty provenance"))
        if conf in {"VERIFIED", "HIGH"} and not provenance:
            findings.append(Finding("FAIL", nid, f"{conf} requires provenance"))
        if not fields.get("Observation", "").strip():
            findings.append(Finding("FAIL", nid, "empty observation"))
        if not fields.get("Directive", "").strip():
            findings.append(Finding("FAIL", nid, "empty directive"))
        if not TAG_RE.search(fields.get("Tags", "")):
            findings.append(Finding("FAIL", nid, "no valid tags"))
        for target in note["links"]:
            if target not in id_set:
                findings.append(Finding("FAIL", nid, f"unresolved link {target}"))
    return findings


def recency(last_access: date | None, today: date) -> float:
    if last_access is None:
        return 0.0
    hours = max((today - last_access).days, 0) * 24
    return DELTA ** hours


def score_notes(notes: list[dict], today: date) -> list[dict]:
    rows: list[dict] = []
    for note in notes:
        fields = note["fields"]
        salience = parse_float(fields.get("Salience", "")) or 0.0
        freq = parse_int(fields.get("Freq", "")) or 0
        created = parse_date(fields.get("Created", ""))
        accessed = parse_date(fields.get("Last-Access", ""))
        age = max((today - created).days, 0) if created else 0
        r_score = recency(accessed, today)
        utility = GAMMA * min(freq / 10.0, 1.0) + (1 - GAMMA) * salience - LAMBDA * age
        composite = W_R * r_score + W_I * salience + W_V * 0.5
        if note.get("stub"):
            action = "STUB"   # pointer to L3; never decays, never auto-deleted
        else:
            action = "KEEP" if utility >= TAU else ("DELETE" if salience < THETA_LOW else "DISTIL")
        rows.append({"id": note["id"], "I": salience, "freq": freq, "age": age,
                     "R": r_score, "U": utility, "S": composite, "action": action})
    return sorted(rows, key=lambda row: row["S"], reverse=True)


def escape_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_index(notes: list[dict], rows: list[dict], today: date) -> str:
    by_id = {row["id"]: row for row in rows}
    tags: dict[str, set[str]] = {}
    lines = [
        "# Memory Graph Index", "",
        "> Regenerated by `scripts/mnemos.py`. Do not hand-edit the table.",
        f"> Last audit: {today.isoformat()}", "", "## Note registry", "",
        "| ID | Title | Type | Conf. | I(m) | S | U | Action | Links |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for note in notes:
        row, fields = by_id[note["id"]], note["fields"]
        for tag in TAG_RE.findall(fields.get("Tags", "")):
            tags.setdefault(tag, set()).add(note["id"][-4:])
        links = ", ".join(target[-4:] for target in note["links"]) or "-"
        lines.append(
            f"| {note['id']} | {escape_cell(note['title'])} | {escape_cell(fields.get('Type', '?'))} | "
            f"{escape_cell(fields.get('Confidence', '?'))} | {row['I']:.2f} | {row['S']:.3f} | "
            f"{row['U']:.3f} | {row['action']} | {links} |"
        )
    lines.extend(["", "## Tag index", ""])
    for tag in sorted(tags):
        lines.append(f"- `{tag}` → {', '.join(sorted(tags[tag]))}")
    numbers = [int(note["id"][-4:]) for note in notes]
    lines.extend(["", "## Counters", "",
                  f"- Next ID: `MEM-{today.year}-{max(numbers, default=0) + 1:04d}`",
                  f"- L2 occupancy: {len(notes)} / {MAX_L2_NOTES}",
                  f"- Prune candidates: {sum(row['action'] not in ('KEEP', 'STUB') for row in rows)}"])
    return "\n".join(lines) + "\n"


def audit(memory: Path, agents: Path, output: Path, existing_index: Path | None,
          today: date) -> tuple[list[Finding], list[dict], str]:
    findings = check_file(agents, "AGENTS.md") + check_file(memory, "MEMORY.md")
    notes = parse_notes(read_text(memory))
    findings.extend(check_notes(notes, today))
    rows = score_notes(notes, today)
    generated = build_index(notes, rows, today)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    if existing_index is not None:
        if not existing_index.exists():
            findings.append(Finding("FAIL", "INDEX.md", "persisted index missing"))
        elif read_text(existing_index) != generated:
            findings.append(Finding("FAIL", "INDEX.md", "persisted index differs from generated index"))
        else:
            findings.append(Finding("PASS", "INDEX.md", "persisted index synchronized"))
    return findings, rows, generated


def print_report(findings: Iterable[Finding], rows: list[dict], output: Path, today: date) -> int:
    findings = list(findings)
    print("=" * 78)
    print(f"MNEMOS v1.1 AUDIT - {today.isoformat()}")
    print("=" * 78)
    print("\n--- INTEGRITY ---")
    for finding in findings:
        print(f"  [{finding.level:4}] {finding.subject:24} {finding.detail}")
    print("\n--- DECAY & UTILITY ---")
    print(f"  {'ID':16} {'I(m)':>5} {'Freq':>5} {'Age':>4} {'R(t)':>6} {'U(m)':>7} {'S':>6}  Action")
    for row in rows:
        print(f"  {row['id']:16} {row['I']:5.2f} {row['freq']:5d} {row['age']:4d} "
              f"{row['R']:6.3f} {row['U']:7.3f} {row['S']:6.3f}  {row['action']}")
    failures = sum(f.level == "FAIL" for f in findings)
    warnings = sum(f.level == "WARN" for f in findings)
    print("\n--- SUMMARY ---")
    print(f"  integrity failures: {failures}")
    print(f"  warnings          : {warnings}")
    print(f"  regenerated index : {output}")
    print(f"\n  VERDICT: {'PASS' if failures == 0 and warnings == 0 else 'FAIL'}")
    return 0 if failures == 0 and warnings == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=Path, default=Path("/tmp/MEMORY.md"))
    parser.add_argument("--agents", type=Path, default=Path("/tmp/AGENTS.md"))
    parser.add_argument("--index", type=Path, default=None,
                        help="persisted INDEX.md to compare with generated output")
    parser.add_argument("--output", type=Path, default=Path("/tmp/INDEX.generated.md"))
    parser.add_argument("--today", type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
                        default=date.today())
    args = parser.parse_args(argv)
    try:
        findings, rows, _ = audit(args.memory, args.agents, args.output, args.index, args.today)
        return print_report(findings, rows, args.output, args.today)
    except (OSError, UnicodeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
