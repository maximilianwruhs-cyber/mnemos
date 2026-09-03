#!/usr/bin/env python3
"""Atomically create graph-safe notes and append Evidence records."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
import mnemos  # noqa: E402
import snapshot  # noqa: E402

CATEGORIES = frozenset({"context", "lessons", "decisions", "preferences"})
L3_HEADING_RE = re.compile(r"#[ \t]+(MEM-\d{4}-\d{4})\b")
MARKER = "\n## 3. Ephemeral Scratchpad"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _validate_id(nid: str) -> None:
    if not re.fullmatch(r"MEM-\d{4}-\d{4}", nid):
        raise ValueError("invalid MEM ID")


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ValueError("invalid category")


def _check_memory_caps(text: str) -> None:
    limits = mnemos.CAPS["MEMORY.md"]
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    if lines > limits["lines"]:
        raise ValueError(
            f"MEMORY.md exceeds line cap {limits['lines']} ({lines} lines)")
    if size > limits["bytes"]:
        raise ValueError(
            f"MEMORY.md exceeds byte cap {limits['bytes']} ({size} bytes)")


def _atomic_replace(path: Path, text: str) -> None:
    """Write UTF-8 text via a same-directory temp file, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-memory-note-", suffix=".md")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _prepare_temp(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=".tmp-memory-note-", suffix=".md")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return tmp


def _pair_replace(
    primary: Path,
    primary_text: str,
    secondary: Path,
    secondary_text: str,
    secondary_restore: bytes,
) -> None:
    """Replace primary then secondary; roll back primary on secondary failure."""
    primary = Path(primary)
    secondary = Path(secondary)
    primary_tmp = _prepare_temp(primary.parent, primary_text)
    try:
        secondary_tmp = _prepare_temp(secondary.parent, secondary_text)
    except Exception:
        if primary_tmp.exists():
            try:
                primary_tmp.unlink()
            except OSError:
                pass
        raise
    primary_committed = False
    try:
        os.replace(primary_tmp, primary)
        primary_committed = True
        primary_tmp = None  # consumed
        os.replace(secondary_tmp, secondary)
        secondary_tmp = None  # consumed
    except Exception:
        # os.replace is atomic: secondary is only ever touched after primary
        # committed, so restore it only then, and only if it actually differs.
        if primary_committed:
            if primary.exists():
                try:
                    primary.unlink()
                except OSError:
                    pass
            if secondary.read_bytes() != secondary_restore:
                secondary.write_bytes(secondary_restore)
        raise
    finally:
        for tmp in (primary_tmp, secondary_tmp):
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def _l3_rel(nid: str, title: str, category: str) -> str:
    slug = _slug(title) or "note"
    return f"Memory/{category}/{nid}-{slug}.md"


def _stub_block(nid: str, title: str, confidence: str, directive: str,
                rel: str) -> str:
    return (
        f"\n### [{nid}] {title} — demoted to L3\n\n"
        f"- **Stub.** {confidence}. {directive} Full note: `{rel}`\n"
    )


def _refuse_archive(root: Path, target: Path) -> None:
    root_r = root.resolve()
    try:
        parts = target.resolve().relative_to(root_r).parts
    except ValueError as exc:
        raise ValueError(f"path escapes base: {target}") from exc
    if "_archive" in parts:
        raise ValueError("archive paths are not mutable")


def create(
    memory: Path,
    root: Path,
    nid: str,
    title: str,
    category: str,
    body: str,
    today: date,
) -> str:
    """Create an L3 full note and matching L2 stub under root."""
    memory = Path(memory)
    root = Path(root)
    _validate_id(nid)
    _validate_category(category)
    if memory.resolve() != (root / "MEMORY.md").resolve():
        raise ValueError("memory path must be root/MEMORY.md")

    text = memory.read_text(encoding="utf-8")
    notes = mnemos.parse_notes(text)
    if any(note["id"] == nid for note in notes):
        raise ValueError(f"ID already exists: {nid}")

    rel = _l3_rel(nid, title, category)
    target = root / rel
    if target.exists():
        raise ValueError(f"target exists: {rel}")

    candidate = f"### [{nid}] {title}\n\n{body.rstrip()}\n"
    probe = text.rstrip() + "\n\n" + candidate
    probe_notes = mnemos.parse_notes(probe)
    findings = mnemos.check_notes(probe_notes, today)
    candidate_fails = [
        finding for finding in findings
        if finding.level == "FAIL" and finding.subject == nid
    ]
    if candidate_fails:
        raise ValueError("; ".join(item.detail for item in candidate_fails))

    fields = next(note["fields"] for note in probe_notes if note["id"] == nid)
    confidence = fields.get("Confidence", "").strip()
    directive = fields.get("Directive", "").strip()
    if not confidence or not directive:
        raise ValueError("validated note missing Confidence or Directive")

    heading = f"# {nid} — {title}\n\n"
    note_text = heading + body.rstrip() + "\n"
    if MARKER not in text:
        raise ValueError("Atomic Notes insertion marker missing")
    stub = _stub_block(nid, title, confidence, directive, rel)
    updated_memory = text.replace(MARKER, stub + MARKER, 1)
    _check_memory_caps(updated_memory)

    original_memory = memory.read_bytes()
    _pair_replace(target, note_text, memory, updated_memory, original_memory)
    return rel


def append_evidence(
    root: Path,
    relative_path: str,
    nid: str,
    item: dict[str, str],
    today: date,
) -> None:
    """Append one Evidence record to an L2 note body or L3 full note."""
    root = Path(root)
    _validate_id(nid)
    target = snapshot.resolve_inside(root, relative_path)
    _refuse_archive(root, target)
    if not target.is_file():
        raise ValueError(f"missing note path: {relative_path}")

    rel_posix = target.resolve().relative_to(root.resolve()).as_posix()
    if rel_posix == "MEMORY.md" or target.resolve() == (root / "MEMORY.md").resolve():
        text = target.read_text(encoding="utf-8")
        notes = {note["id"]: note for note in mnemos.parse_notes(text)}
        note = notes.get(nid)
        if note is None:
            raise ValueError(f"note not found: {nid}")
        if note["stub"]:
            raise ValueError("cannot append evidence to a stub")
        updated_body = evidence.append(note["body"], item, today)
        updated = (
            text[: note["body_start"]]
            + updated_body
            + text[note["scan_end"] :]
        )
        _check_memory_caps(updated)
        _atomic_replace(target, updated)
        return

    text = target.read_text(encoding="utf-8")
    # The first level-1 heading in the file must declare exactly nid.
    first_heading = re.search(r"^#[ \t]+.+$", text, re.M)
    declared = L3_HEADING_RE.match(first_heading.group(0)) if first_heading else None
    if declared is None:
        raise ValueError("L3 note missing MEM heading")
    if declared.group(1) != nid:
        raise ValueError(f"L3 heading ID does not match {nid}")
    updated = evidence.append(text, item, today)
    _atomic_replace(target, updated)


def _parse_today(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid today date: {value}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create L3 notes or append Evidence records.")
    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", help="Create L3 note + L2 stub")
    create_p.add_argument("--memory", required=True, type=Path)
    create_p.add_argument("--root", required=True, type=Path)
    create_p.add_argument("--id", required=True)
    create_p.add_argument("--title", required=True)
    create_p.add_argument("--category", required=True)
    create_p.add_argument("--body-file", required=True, type=Path)
    create_p.add_argument("--today", required=True, type=_parse_today)

    append_p = sub.add_parser(
        "append-evidence", help="Append one Evidence record")
    append_p.add_argument("--root", required=True, type=Path)
    append_p.add_argument("--path", required=True)
    append_p.add_argument("--id", required=True)
    append_p.add_argument("--evidence-json", required=True)
    append_p.add_argument("--today", required=True, type=_parse_today)

    args = parser.parse_args(argv)
    if args.command == "create":
        body = args.body_file.read_text(encoding="utf-8")
        rel = create(
            args.memory, args.root, args.id, args.title, args.category,
            body, args.today)
        print(rel)
        return 0

    item = json.loads(args.evidence_json)
    if not isinstance(item, dict):
        raise ValueError("evidence-json must decode to an object")
    append_evidence(args.root, args.path, args.id, item, args.today)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
