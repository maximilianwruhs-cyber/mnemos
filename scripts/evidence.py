#!/usr/bin/env python3
"""Pure Evidence ledger: parse, validate, canonicalize, and append records."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secretscan  # noqa: E402

EVIDENCE_RE = re.compile(r"^[ \t]*-[ \t]*\*\*Evidence:\*\*[ \t]*(.*)$", re.M)
KEYS = ("date", "stance", "source", "quote")
STANCES = {"SUPPORT", "CHALLENGE"}
MAX_ITEMS = 16
MAX_SOURCE = 240
MAX_QUOTE = 280


@dataclass(frozen=True)
class EvidenceItem:
    """One validated Evidence record with canonical identity."""

    date: date
    stance: str
    source: str
    quote: str
    canonical: str
    fingerprint: bytes


@dataclass(frozen=True)
class EvidenceFinding:
    """Structural FAIL or contested WARN from Evidence inspection."""

    level: str
    detail: str


@dataclass(frozen=True)
class EvidenceReport:
    """Derived Evidence view for a single note body."""

    items: tuple[EvidenceItem, ...]
    support: int
    challenge: int
    contested: bool
    findings: tuple[EvidenceFinding, ...]


def _canonical(values: dict[str, str]) -> str:
    ordered = {key: values[key] for key in KEYS}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def _secret_detail(hit: secretscan.SecretMatch) -> str:
    return f"possible {hit.kind} ({hit.preview}) - remove before commit"


def _validate_values(values: dict[str, str], today: date) -> list[str]:
    """Return FAIL detail strings for already-trimmed string field values."""
    errors: list[str] = []
    raw_date = values["date"]
    try:
        observed = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"Evidence date is invalid: {raw_date!r}")
        observed = None
    if observed is not None and observed > today:
        errors.append(f"Evidence date is in the future: {raw_date}")

    stance = values["stance"]
    if stance not in STANCES:
        errors.append(f"Evidence stance is invalid: {stance!r}")

    source = values["source"]
    quote = values["quote"]
    if not source:
        errors.append("Evidence source is empty")
    elif len(source) > MAX_SOURCE:
        errors.append(f"Evidence source exceeds {MAX_SOURCE} characters")
    if not quote:
        errors.append("Evidence quote is empty")
    elif len(quote) > MAX_QUOTE:
        errors.append(f"Evidence quote exceeds {MAX_QUOTE} characters")

    for label, value in (("source", source), ("quote", quote)):
        if "\n" in value or "\r" in value:
            errors.append(f"Evidence {label} contains a physical newline")

    for label, value in (("source", source), ("quote", quote)):
        for hit in secretscan.scan(value):
            errors.append(_secret_detail(hit))

    return errors


def _parse_payload(payload: str, today: date) -> tuple[EvidenceItem | None, list[str]]:
    """Parse one Evidence payload into an item or FAIL details."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None, ["Evidence JSON is malformed"]

    if not isinstance(data, dict):
        return None, ["Evidence payload must be a JSON object"]

    if set(data.keys()) != set(KEYS):
        return None, ["Evidence object keys must be exactly date/stance/source/quote"]

    if any(not isinstance(data[key], str) for key in KEYS):
        return None, ["Evidence field values must be strings"]

    values = {key: data[key].strip() for key in KEYS}
    errors = _validate_values(values, today)
    if errors:
        return None, errors

    canonical = _canonical(values)
    item = EvidenceItem(
        date=datetime.strptime(values["date"], "%Y-%m-%d").date(),
        stance=values["stance"],
        source=values["source"],
        quote=values["quote"],
        canonical=canonical,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).digest(),
    )
    return item, []


def inspect(text: str, today: date) -> EvidenceReport:
    """Parse and validate every labeled Evidence line in ``text``."""
    findings: list[EvidenceFinding] = []
    items: list[EvidenceItem] = []
    seen: set[bytes] = set()
    labeled = 0

    for match in EVIDENCE_RE.finditer(text):
        labeled += 1
        payload = match.group(1)
        full_line = match.group(0)
        item, errors = _parse_payload(payload, today)

        if item is None:
            for detail in errors:
                findings.append(EvidenceFinding("FAIL", detail))
            continue

        expected_line = f"- **Evidence:** {item.canonical}"
        if full_line != expected_line:
            findings.append(EvidenceFinding(
                "FAIL", "Evidence line is not canonical"))
            continue

        if item.fingerprint in seen:
            findings.append(EvidenceFinding(
                "FAIL", "Evidence record is an exact duplicate"))
            continue

        seen.add(item.fingerprint)
        items.append(item)

    if labeled == 0:
        findings.append(EvidenceFinding(
            "FAIL", "Evidence records are required"))
    elif labeled > MAX_ITEMS:
        findings.append(EvidenceFinding(
            "FAIL", f"Evidence exceeds {MAX_ITEMS} records"))

    support = sum(1 for item in items if item.stance == "SUPPORT")
    challenge = sum(1 for item in items if item.stance == "CHALLENGE")

    if labeled > 0 and support == 0:
        findings.append(EvidenceFinding(
            "FAIL", "Evidence requires at least one SUPPORT record"))

    contested = challenge > 0
    if contested and support > 0 and not any(f.level == "FAIL" for f in findings):
        findings.append(EvidenceFinding("WARN", "Evidence is contested"))

    return EvidenceReport(
        items=tuple(items),
        support=support,
        challenge=challenge,
        contested=contested,
        findings=tuple(findings),
    )


def _candidate_newlines(item: dict[str, str]) -> None:
    for key in KEYS:
        value = item.get(key, "")
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            raise ValueError(f"Evidence {key} contains a physical newline")


def append(text: str, item: dict[str, str], today: date) -> str:
    """Return ``text`` with one canonical Evidence line appended."""
    if not isinstance(item, dict):
        raise ValueError("Evidence candidate must be a mapping")
    _candidate_newlines(item)

    # Build a trimmed candidate for rendering when keys/types allow it.
    if set(item.keys()) == set(KEYS) and all(isinstance(item[k], str) for k in KEYS):
        candidate = {key: item[key].strip() for key in KEYS}
    else:
        # Fall through to inspect via best-effort rendering when possible.
        candidate = {key: str(item.get(key, "")) for key in KEYS}

    separator = "" if text.endswith("\n") else "\n"
    updated = text + separator + f"- **Evidence:** {_canonical(candidate)}\n"
    report = inspect(updated, today)
    if any(f.level == "FAIL" for f in report.findings):
        raise ValueError("; ".join(
            f.detail for f in report.findings if f.level == "FAIL"))
    return updated
