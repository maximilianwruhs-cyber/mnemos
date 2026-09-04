#!/usr/bin/env python3
"""corpus_lint.py - validate and hash a MNEMOS certification corpus (corpus-v2).

Verification layer 1 of the semantic-recall release decision: schema, controlled
vocabulary, de-identification, referential integrity, scenario-group isolation,
duplicate and leakage checks, coverage floors, and canonical content hashes.

Pure standard library. The corpus is data; nothing in it is executed or
interpreted. Contract:
scripts/fixtures/vector-semantics/corpus-v2/CONTRACT.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

SPLITS = ("train", "dev", "certification")
LANGUAGES = ("en", "de")
CONTRAST_FAMILIES = (
    "ordinary-paraphrase", "success-vs-failure", "permit-vs-prohibit",
    "apply-vs-rollback", "online-vs-offline", "current-vs-superseded",
    "mutate-vs-inspect", "cause-vs-coincidence", "identifier-tokens",
)
PROVENANCE = (
    "derived-failure-pattern", "synthetic-contrast", "paraphrase-augmentation",
)

# Coverage floors (CONTRACT.md sec 6; design sec 6/sec 63).
CERT_QUERY_FLOOR = 200
CERT_NOTE_FLOOR = 200
DEV_QUERY_FLOOR = 150
FAMILY_CERT_FLOOR = 15
LANG_MIN, LANG_MAX = 0.40, 0.60
FAMILY_LANG_MIN = 0.30

LEAK_JACCARD = 0.60
DEID_DENYLIST = "de-id-denylist.txt"
MANIFEST = "manifest.json"

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CERT_REF_RE = re.compile(r"[\\/]certification([\\/]|$)|certification[\\/](notes|queries)")


# --- text helpers -----------------------------------------------------------

def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(unicodedata.normalize("NFC", text).casefold()))


def canonical_bytes(records) -> bytes:
    """Canonical JSONL: id-sorted, minified, one record per line, LF-terminated."""
    lines = [
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for r in sorted(records, key=lambda r: r.get("id", ""))
    ]
    return "".join(line + "\n" for line in lines).encode("utf-8")


# --- loading ----------------------------------------------------------------

def load_corpus(root, errors) -> dict:
    root = Path(root)
    data = {
        "notes": {s: [] for s in SPLITS},
        "queries": {s: [] for s in SPLITS},
        "raw_bytes": {},
    }
    for split in SPLITS:
        for kind in ("notes", "queries"):
            rel = f"{split}/{kind}.jsonl"
            path = root / split / f"{kind}.jsonl"
            if not path.exists():
                continue
            raw = path.read_bytes()
            data["raw_bytes"][rel] = raw
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(f"{rel}: not UTF-8: {exc}")
                continue
            parts = text.split("\n")
            if text != "" and parts[-1] != "":
                errors.append(f"{rel}: file must end with a single trailing newline")
            body = parts[:-1] if (parts and parts[-1] == "") else parts
            for lineno, line in enumerate(body, 1):
                if line.strip() == "":
                    errors.append(f"{rel}:{lineno}: blank line")
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{rel}:{lineno}: invalid JSON: {exc}")
                    continue
                if not isinstance(rec, dict):
                    errors.append(f"{rel}:{lineno}: record must be a JSON object")
                    continue
                data[kind][split].append(rec)
    return data


def load_split(root, split, certified_run=False) -> dict:
    """Guarded loader. The certification split is sealed for training/calibration."""
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    if split == "certification" and not certified_run:
        raise PermissionError(
            "certification split is sealed; certified_run=True is required and "
            "only for the one-shot certification pass"
        )
    base = Path(root) / split
    return {
        "notes": _read_jsonl(base / "notes.jsonl"),
        "queries": _read_jsonl(base / "queries.jsonl"),
    }


def _read_jsonl(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            out.append(json.loads(line))
    return out


# --- per-record schema ------------------------------------------------------

def _check_note(note, split, rel) -> list:
    errors = []
    nid = note.get("id", "<no-id>")
    for field in ("id", "text", "language", "scenario_group", "provenance"):
        if field not in note:
            errors.append(f"{rel}: note {nid} missing field {field}")
    if "id" in note and not str(note["id"]).startswith("n-"):
        errors.append(f"{rel}: note id must start with 'n-': {note['id']}")
    if "language" in note and note["language"] not in LANGUAGES:
        errors.append(f"{rel}: note {nid} bad language {note['language']!r}")
    if "provenance" in note and note["provenance"] not in PROVENANCE:
        errors.append(f"{rel}: note {nid} bad provenance {note['provenance']!r}")
    return errors


def _check_query(query, split, rel, note_ids_split) -> list:
    errors = []
    qid = query.get("id", "<no-id>")
    for field in ("id", "text", "language", "scenario_group", "provenance",
                  "contrast_families", "gold", "hard_negatives",
                  "easy_negatives", "rationale"):
        if field not in query:
            errors.append(f"{rel}: query {qid} missing field {field}")
    if "id" in query and not str(query["id"]).startswith("q-"):
        errors.append(f"{rel}: query id must start with 'q-': {query['id']}")
    if "language" in query and query["language"] not in LANGUAGES:
        errors.append(f"{rel}: query {qid} bad language {query['language']!r}")
    if "provenance" in query and query["provenance"] not in PROVENANCE:
        errors.append(f"{rel}: query {qid} bad provenance {query['provenance']!r}")

    fams = query.get("contrast_families")
    if not isinstance(fams, list) or not fams:
        errors.append(f"{rel}: query {qid} needs >=1 contrast_families")
    else:
        for fam in fams:
            if fam not in CONTRAST_FAMILIES:
                errors.append(f"{rel}: query {qid} bad contrast_family {fam!r}")

    gold = query.get("gold")
    if not isinstance(gold, list) or not gold:
        errors.append(f"{rel}: query {qid} needs >=1 gold")
    else:
        for gid in gold:
            if gid not in note_ids_split:
                errors.append(f"{rel}: query {qid} gold {gid} not a note in {split}")

    hard = query.get("hard_negatives")
    if not isinstance(hard, list) or not hard:
        errors.append(f"{rel}: query {qid} needs >=1 hard_negative")
    else:
        for hn in hard:
            if not isinstance(hn, dict) or "note_id" not in hn or "contrast_family" not in hn:
                errors.append(f"{rel}: query {qid} hard_negative must be {{note_id, contrast_family}}")
                continue
            if hn["note_id"] not in note_ids_split:
                errors.append(f"{rel}: query {qid} hard_negative {hn['note_id']} not a note in {split}")
            if hn["contrast_family"] not in CONTRAST_FAMILIES:
                errors.append(f"{rel}: query {qid} hard_negative bad contrast_family {hn['contrast_family']!r}")

    easy = query.get("easy_negatives")
    if not isinstance(easy, list) or not easy:
        errors.append(f"{rel}: query {qid} needs >=1 easy_negative")
    else:
        for eid in easy:
            if eid not in note_ids_split:
                errors.append(f"{rel}: query {qid} easy_negative {eid} not a note in {split}")
    return errors


def check_records(data) -> list:
    errors = []
    note_id_counts, query_id_counts = {}, {}
    for split in SPLITS:
        notes = data["notes"][split]
        queries = data["queries"][split]
        note_ids_split = {n["id"] for n in notes if "id" in n}
        notes_by_id = {n["id"]: n for n in notes if "id" in n and "text" in n}

        seen_note_text = {}
        for note in notes:
            errors += _check_note(note, split, f"{split}/notes.jsonl")
            nid = note.get("id")
            if nid is not None:
                note_id_counts[nid] = note_id_counts.get(nid, 0) + 1
            if "text" in note:
                key = _normalize(note["text"])
                if key in seen_note_text:
                    errors.append(f"{split}/notes.jsonl: duplicate note text {nid} == {seen_note_text[key]}")
                else:
                    seen_note_text[key] = nid

        seen_query_text = {}
        for query in queries:
            errors += _check_query(query, split, f"{split}/queries.jsonl", note_ids_split)
            qid = query.get("id")
            if qid is not None:
                query_id_counts[qid] = query_id_counts.get(qid, 0) + 1
            if "text" in query:
                key = _normalize(query["text"])
                if key in seen_query_text:
                    errors.append(f"{split}/queries.jsonl: duplicate query text {qid} == {seen_query_text[key]}")
                else:
                    seen_query_text[key] = qid
                qtok = _tokens(query["text"])
                for gid in (query.get("gold") or []):
                    gold_note = notes_by_id.get(gid)
                    if gold_note and qtok and qtok <= _tokens(gold_note["text"]):
                        errors.append(
                            f"{split}/queries.jsonl: gold {gid} repeats all query tokens of "
                            f"{qid} (positives must not repeat query vocabulary)"
                        )

    for nid, count in sorted(note_id_counts.items()):
        if count > 1:
            errors.append(f"note id not unique across corpus: {nid} ({count}x)")
    for qid, count in sorted(query_id_counts.items()):
        if count > 1:
            errors.append(f"query id not unique across corpus: {qid} ({count}x)")
    return errors


# --- cross-record invariants ------------------------------------------------

def check_canonical(data) -> list:
    errors = []
    for split in SPLITS:
        for kind in ("notes", "queries"):
            rel = f"{split}/{kind}.jsonl"
            raw = data["raw_bytes"].get(rel)
            if raw is None:
                continue
            recs = data[kind][split]
            if any("id" not in r for r in recs):
                continue  # schema errors already reported; canonical form undefined
            if raw != canonical_bytes(recs):
                errors.append(f"{rel}: not in canonical form (id-sorted, minified, LF-terminated; CONTRACT sec 8)")
    return errors


def check_scenario_isolation(data) -> list:
    errors = []
    groups = {}
    for split in SPLITS:
        for rec in data["notes"][split] + data["queries"][split]:
            sg = rec.get("scenario_group")
            if sg is not None:
                groups.setdefault(sg, set()).add(split)
    for sg, splits in sorted(groups.items()):
        if len(splits) > 1:
            errors.append(f"scenario_group {sg} spans splits {sorted(splits)} (must be exactly one)")
    return errors


def check_leakage(data) -> list:
    errors = []

    def entries(splits):
        out = []
        for split in splits:
            for kind in ("notes", "queries"):
                for rec in data[kind][split]:
                    if "text" in rec and "id" in rec:
                        out.append((rec["id"], rec.get("scenario_group"), _tokens(rec["text"])))
        return out

    cert = entries(["certification"])
    other = entries(["train", "dev"])
    for cid, csg, ctok in cert:
        if not ctok:
            continue
        for oid, osg, otok in other:
            if osg == csg or not otok:
                continue
            union = len(ctok | otok)
            if union and len(ctok & otok) / union >= LEAK_JACCARD:
                ratio = len(ctok & otok) / union
                errors.append(f"leakage: certification {cid} ~ {oid} (jaccard {ratio:.2f} >= {LEAK_JACCARD})")
    return errors


def check_deid(root, data) -> list:
    errors = []
    path = Path(root) / DEID_DENYLIST
    if not path.exists():
        return errors
    markers = [
        m.strip() for m in path.read_text(encoding="utf-8").splitlines()
        if m.strip() and not m.startswith("#")
    ]
    if not markers:
        return errors
    for split in SPLITS:
        for kind in ("notes", "queries"):
            for rec in data[kind][split]:
                low = rec.get("text", "").casefold()
                for marker in markers:
                    if marker.casefold() in low:
                        errors.append(f"{split}/{kind}.jsonl: {rec.get('id')} contains de-id marker {marker!r}")
    return errors


def check_floors(data) -> list:
    errors = []
    cert_q = data["queries"]["certification"]
    cert_n = data["notes"]["certification"]
    dev_q = data["queries"]["dev"]
    if len(cert_q) < CERT_QUERY_FLOOR:
        errors.append(f"certification queries {len(cert_q)} < {CERT_QUERY_FLOOR}")
    if len(cert_n) < CERT_NOTE_FLOOR:
        errors.append(f"certification notes {len(cert_n)} < {CERT_NOTE_FLOOR}")
    if len(dev_q) < DEV_QUERY_FLOOR:
        errors.append(f"dev queries {len(dev_q)} < {DEV_QUERY_FLOOR}")

    fam_counts = {f: 0 for f in CONTRAST_FAMILIES}
    fam_lang = {f: {"en": 0, "de": 0} for f in CONTRAST_FAMILIES}
    lang_counts = {"en": 0, "de": 0}
    for query in cert_q:
        lang = query.get("language")
        if lang in lang_counts:
            lang_counts[lang] += 1
        for fam in (query.get("contrast_families") or []):
            if fam in fam_counts:
                fam_counts[fam] += 1
                if lang in fam_lang[fam]:
                    fam_lang[fam][lang] += 1

    for fam in CONTRAST_FAMILIES:
        if fam_counts[fam] < FAMILY_CERT_FLOOR:
            errors.append(f"contrast family {fam}: {fam_counts[fam]} cert queries < {FAMILY_CERT_FLOOR}")

    total = len(cert_q)
    if total:
        for lang in ("en", "de"):
            frac = lang_counts[lang] / total
            if not (LANG_MIN <= frac <= LANG_MAX):
                errors.append(f"language {lang} is {frac:.2f} of cert queries, need {LANG_MIN:.2f}-{LANG_MAX:.2f}")
        for fam in CONTRAST_FAMILIES:
            if fam_counts[fam]:
                for lang in ("en", "de"):
                    frac = fam_lang[fam][lang] / fam_counts[fam]
                    if frac < FAMILY_LANG_MIN:
                        errors.append(f"family {fam} language {lang} {frac:.2f} < {FAMILY_LANG_MIN:.2f}")
    return errors


def check_no_cert_reference(config_path) -> list:
    path = Path(config_path)
    if not path.exists():
        return [f"config not found: {config_path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if _CERT_REF_RE.search(text):
        return [f"config {config_path} references the certification split (training/calibration must not)"]
    return []


# --- hashing / manifest -----------------------------------------------------

def compute_manifest(data, corpus_version) -> dict:
    file_hashes = {}
    counts = {}
    for split in SPLITS:
        counts[split] = {
            "notes": len(data["notes"][split]),
            "queries": len(data["queries"][split]),
        }
        for kind in ("notes", "queries"):
            rel = f"{split}/{kind}.jsonl"
            file_hashes[rel] = hashlib.sha256(canonical_bytes(data[kind][split])).hexdigest()
    rollup = "\n".join(f"{file_hashes[rel]}  {rel}" for rel in sorted(file_hashes))
    corpus_hash = hashlib.sha256(rollup.encode("utf-8")).hexdigest()
    return {
        "corpus_version": corpus_version,
        "counts": counts,
        "file_hashes": file_hashes,
        "corpus_hash": corpus_hash,
    }


def check_manifest(root, computed) -> list:
    path = Path(root) / MANIFEST
    if not path.exists():
        return [f"{MANIFEST} missing (required for --check)"]
    try:
        declared = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{MANIFEST} invalid: {exc}"]
    errors = []
    for key in ("corpus_version", "counts", "file_hashes", "corpus_hash"):
        if declared.get(key) != computed[key]:
            errors.append(f"{MANIFEST} {key} mismatch (declared != recomputed)")
    return errors


# --- driver -----------------------------------------------------------------

def lint(root, enforce_floors=True, configs=(), corpus_version="v2"):
    errors = []
    data = load_corpus(root, errors)
    errors += check_canonical(data)
    errors += check_records(data)
    errors += check_scenario_isolation(data)
    errors += check_leakage(data)
    errors += check_deid(root, data)
    if enforce_floors:
        errors += check_floors(data)
    for cfg in configs:
        errors += check_no_cert_reference(cfg)
    manifest = compute_manifest(data, corpus_version)
    return errors, manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate and hash a MNEMOS certification corpus.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--check", action="store_true", help="require manifest.json to match recomputed hashes/counts")
    parser.add_argument("--no-floors", action="store_true", help="skip coverage floors (structure only)")
    parser.add_argument("--config", action="append", default=[], help="config that must not reference certification")
    parser.add_argument("--corpus-version", default="v2")
    parser.add_argument("--emit-manifest", action="store_true", help="write manifest.json from the recomputed hashes")
    args = parser.parse_args(argv)

    errors, manifest = lint(
        args.root, enforce_floors=not args.no_floors,
        configs=args.config, corpus_version=args.corpus_version,
    )
    if args.check:
        errors += check_manifest(args.root, manifest)

    if errors:
        for err in errors:
            print(f"[FAIL] {err}", file=sys.stderr)
        print(f"corpus lint: FAILED ({len(errors)} problems)", file=sys.stderr)
        return 1

    if args.emit_manifest:
        (args.root / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"corpus lint: PASS corpus_hash={manifest['corpus_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
