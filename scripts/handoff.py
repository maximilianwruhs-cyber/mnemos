#!/usr/bin/env python3
"""handoff.py - validate, list and expire session handoff snapshots.

WHY THIS EXISTS. A handoff written by a model is a claim about state, and claims
rot two ways: they invent evidence, and they go stale. A prose template cannot
stop either. This can. It refuses a snapshot whose "Verified" section asserts
something without naming what proved it, it refuses one whose next action is
empty, and it refuses one carrying a credential - a handoff is the easiest place
in the whole substrate to leak a secret by accident.

It never mutates the store. It reads text, returns findings, exits non-zero when
any finding is FAIL. Warnings are informative and do not block.

USAGE
    handoff.py --selftest             # needs no staging
    handoff.py validate /tmp/h.md     # exit 0 = accept, 1 = reject
    handoff.py list /tmp/handoffs     # inventory with age and expiry
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

SCHEMA = "handoff/1"
SIZE_CAP = 32 * 1024
DEFAULT_TTL_DAYS = 7

REQUIRED_META = ("schema", "created", "expires", "status", "source", "sensitive_reviewed")
VALID_STATUS = ("DRAFT", "REVIEWED")

SECTIONS = ("Objective", "Verified", "Unverified", "Decisions",
            "Failures", "Artifacts", "Next Action", "Resume Guard")

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
H2_RE = re.compile(r"^## (.+?)\s*$", re.M)
BULLET_RE = re.compile(r"^\s*-\s+(.*\S)\s*$", re.M)

# Each pattern demands a delimiter plus real length, so prose that merely uses
# the word "token" or "password" does not trip the gate.
SECRETS = [
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("credential assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token|client[_-]?secret)\b\s*[:=]\s*[^\s;]{6,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("azure sas token", re.compile(r"(?i)(?:\?|&)sv=[^\s&]+(?:&[^\s]*)?&sig=[A-Za-z0-9%+/=_-]{16,}")),
    ("session cookie", re.compile(r"(?i)\bcookie\b\s*[:=]\s*\S{16,}")),
    ("long opaque blob", re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")),
]

EVIDENCE_RE = re.compile(r"\*\*Evidence:\*\*\s*(\S.*)$")
WHY_RE = re.compile(r"\*\*Why:\*\*\s*(\S.*)$")
LABELED_BULLET_RE = re.compile(r"^\s*-\s+\*\*([^*]+):\*\*\s*(\S.*)\s*$")
RESUME_REQUIREMENTS = {
    "paths": ("path", "exist"),
    "time": ("time", "revalid"),
    "authority": ("current", "instruction"),
    "evidence": ("proven", "re-check"),
}


def _as_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _clip(text, width=60):
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "\u2026"


def parse(text):
    """Split a snapshot into (meta, sections, heading_order, has_frontmatter)."""
    meta = {}
    body = text
    match = FM_RE.match(text)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        body = text[match.end():]
    hits = list(H2_RE.finditer(body))
    sections = {}
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        sections[hit.group(1).strip()] = body[hit.end():end].strip()
    return meta, sections, [h.group(1).strip() for h in hits], bool(match)


def scan_secrets(text):
    return [label for label, rx in SECRETS if rx.search(text)]


def validate(text, today=None):
    """Return a list of (level, code, message). FAIL blocks persistence."""
    today = today or date.today()
    findings = []
    fail = lambda code, msg: findings.append(("FAIL", code, msg))
    warn = lambda code, msg: findings.append(("WARN", code, msg))

    size = len(text.encode("utf-8"))
    if size > SIZE_CAP:
        fail("F009", f"oversize: {size} bytes, cap {SIZE_CAP}")

    meta, sections, order, has_fm = parse(text)
    if not has_fm:
        fail("F001", "no YAML frontmatter block")
    else:
        fm = FM_RE.match(text).group(1)
        keys = [line.split(":", 1)[0].strip() for line in fm.splitlines() if ":" in line]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            fail("F016", f"duplicate frontmatter key(s): {', '.join(duplicates)}")
    for key in REQUIRED_META:
        if not meta.get(key):
            fail("F002", f"frontmatter missing '{key}'")
    if meta.get("schema") and meta["schema"] != SCHEMA:
        fail("F003", f"unsupported schema '{meta['schema']}', expected '{SCHEMA}'")
    if meta.get("status") and meta["status"] not in VALID_STATUS:
        fail("F013", f"invalid status '{meta['status']}'")

    unknown = sorted(set(order) - set(SECTIONS))
    if unknown:
        fail("F017", f"unknown section(s): {', '.join(unknown)}")
    for name in SECTIONS:
        if name not in sections:
            fail("F004", f"missing section '{name}'")
    seen = [h for h in order if h in SECTIONS]
    canonical = [s for s in SECTIONS if s in sections]
    if seen != canonical:
        fail("F005", "sections duplicated or out of canonical order")

    created, expires = _as_date(meta.get("created")), _as_date(meta.get("expires"))
    if meta.get("created") and created is None:
        fail("F014", "created is not an ISO date")
    if meta.get("expires") and expires is None:
        fail("F014", "expires is not an ISO date")
    if created and expires:
        if expires < created:
            fail("F010", "expires precedes created")
        elif expires < today:
            warn("W002", f"snapshot expired on {expires.isoformat()}")

    # Closed line grammar: claims and decisions are one bullet each. This prevents
    # prose and continuation lines from escaping marker enforcement.
    for section_name, code, marker_re, label in (
            ("Verified", "F011", EVIDENCE_RE, "evidence"),
            ("Decisions", "F012", WHY_RE, "rationale")):
        body = sections.get(section_name, "")
        if body and body.lower() != "(none)":
            lines = [line for line in body.splitlines() if line.strip()]
            malformed = [line for line in lines if not re.fullmatch(r"\s*-\s+.*\S\s*", line)]
            if malformed:
                fail("F015", f"{section_name} contains non-bullet or continuation content: {_clip(malformed[0])}")
            for line in lines:
                bullet = re.sub(r"^\s*-\s+", "", line).strip()
                if not marker_re.search(bullet):
                    fail(code, f"{section_name.lower()} claim without non-empty {label}: {_clip(bullet)}")

    action_lines = [line for line in sections.get("Next Action", "").splitlines() if line.strip()]
    action_fields = {}
    malformed_action = False
    for line in action_lines:
        match = LABELED_BULLET_RE.fullmatch(line)
        if not match or match.group(1).strip() in action_fields:
            malformed_action = True
            continue
        action_fields[match.group(1).strip()] = match.group(2).strip()
    if malformed_action or set(action_fields) != {"Do", "Needs", "Expect"}:
        fail("F006", "Next Action requires exactly one non-empty Do, Needs and Expect bullet")

    guard_lines = [line for line in sections.get("Resume Guard", "").splitlines() if line.strip()]
    if not guard_lines or any(not re.fullmatch(r"\s*-\s+.*\S\s*", line) for line in guard_lines):
        fail("F007", "Resume Guard must contain bullet-only rules")
    else:
        guard_bullets = [re.sub(r"^\s*-\s+", "", line).lower() for line in guard_lines]
        missing = [name for name, terms in RESUME_REQUIREMENTS.items()
                   if not any(all(term in bullet for term in terms) for bullet in guard_bullets)]
        if missing:
            fail("F007", f"Resume Guard missing required rule(s): {', '.join(missing)}")

    for label in scan_secrets(text):
        fail("F008", f"possible secret in snapshot: {label}")

    if meta.get("status") == "DRAFT":
        warn("W001", "status is DRAFT - not yet reviewed by a human")
    if meta.get("sensitive_reviewed", "").lower() not in ("yes", "true"):
        warn("W003", "sensitive content not confirmed reviewed")
    return findings


def is_expired(meta, today=None):
    expires = _as_date(meta.get("expires"))
    return bool(expires and expires < (today or date.today()))


def render_list(directory, today=None):
    today = today or date.today()
    rows = ["  AGE  EXPIRES     STATUS    FILE"]
    files = sorted(Path(directory).glob("*.md"))
    for path in files:
        meta, _, _, _ = parse(path.read_text(encoding="utf-8"))
        created = _as_date(meta.get("created"))
        age = f"{(today - created).days}d" if created else "?"
        flag = "EXPIRED" if is_expired(meta, today) else (meta.get("status") or "?")
        rows.append(f"  {age:>4}  {meta.get('expires', '?'):<10}  {flag:<8}  {path.name}")
    rows.append(f"  {len(files)} snapshot(s)")
    return "\n".join(rows)


GOOD = """---
schema: handoff/1
created: 2026-01-01
expires: 2026-01-08
status: REVIEWED
source: probe session
sensitive_reviewed: yes
---

# HANDOFF - probe

## Objective
- **Goal:** prove the validator accepts a well formed snapshot.

## Verified
- Parser handles frontmatter. **Evidence:** selftest assertion in handoff.py.

## Unverified
- Nothing outstanding.

## Decisions
- Markdown over JSON. **Why:** greppable and diffable in the store.

## Failures
- (none)

## Artifacts
- CREATED `/scripts/handoff.py` - the validator.

## Next Action
- **Do:** Run the unit suite.
- **Needs:** The persisted validator and test file.
- **Expect:** Every registered test exits successfully.

## Resume Guard
- Verify every referenced path still exists before acting.
- Revalidate anything time-sensitive before reuse.
- Current user instructions outrank this snapshot.
- Do not restate a claim as proven without re-checking it.
"""

TODAY = date(2026, 1, 2)


def selftest():
    checks = []

    def expect(name, condition):
        checks.append((name, bool(condition)))

    def codes(text):
        return {c for lvl, c, _ in validate(text, TODAY) if lvl == "FAIL"}

    expect("clean snapshot accepted", not codes(GOOD))
    expect("frontmatter required", "F001" in codes(GOOD.split("---\n", 2)[2]))
    expect("missing field caught",
           "F002" in codes(GOOD.replace("source: probe session\n", "")))
    expect("schema pinned", "F003" in codes(GOOD.replace("handoff/1", "handoff/9")))
    expect("missing section caught",
           "F004" in codes(GOOD.replace("## Unverified", "## Notes")))
    expect("order enforced", "F005" in codes(
        GOOD.replace("## Verified", "## TMP").replace("## Unverified", "## Verified")
            .replace("## TMP", "## Unverified")))
    expect("empty next action caught",
           "F006" in codes(GOOD.replace("- **Do:** Run the unit suite.", "")))
    expect("empty resume guard caught", "F007" in codes(
        GOOD.replace("- Verify every referenced path still exists before acting.", "")))
    expect("secret blocked",
           "F008" in codes(GOOD.replace("probe session", "x") + "\napi_key: abcdefgh12345678\n"))
    expect("oversize blocked", "F009" in codes(GOOD + "x" * (SIZE_CAP + 1)))
    expect("date inversion caught",
           "F010" in codes(GOOD.replace("expires: 2026-01-08", "expires: 2025-12-01")))
    expect("evidence gate fires", "F011" in codes(
        GOOD.replace("**Evidence:** selftest assertion in handoff.py.", "it works.")))
    expect("rationale gate fires", "F012" in codes(
        GOOD.replace("**Why:** greppable and diffable in the store.", "obviously.")))
    expect("bad status caught", "F013" in codes(GOOD.replace("status: REVIEWED", "status: FINE")))
    expect("expiry warns not blocks", any(
        c == "W002" for _, c, _ in validate(GOOD, date(2026, 2, 1))))
    expect("prose about tokens is not a secret",
           not scan_secrets("We decided against token rotation for now."))

    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"RESULT: {'PASS' if passed == len(checks) else 'FAIL'} - "
          f"{passed}/{len(checks)} handoff assertions")
    return 0 if passed == len(checks) else 1


def main(argv):
    if not argv or argv[0] in ("--selftest", "selftest"):
        return selftest()
    if argv[0] == "validate" and len(argv) > 1:
        findings = validate(Path(argv[1]).read_text(encoding="utf-8"))
        for level, code, message in findings:
            print(f"  [{level}] {code} {message}")
        fails = [f for f in findings if f[0] == "FAIL"]
        print(f"  VERDICT: {'REJECT' if fails else 'ACCEPT'} - "
              f"{len(fails)} fail, {len(findings) - len(fails)} warn")
        return 1 if fails else 0
    if argv[0] == "list" and len(argv) > 1:
        print(render_list(argv[1]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
