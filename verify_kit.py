#!/usr/bin/env python3
"""Verify package integrity and run the bundled MNEMOS regression gate."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = [
    "README.md", "INSTALL.md", "PORTABILITY-NOTES.md", "EXCLUSIONS.md",
    "install.py", "verify_kit.py",
    "docs/MNEMOS-CURRENT-STATE.md", "docs/MNEMOS-IMPLEMENTATION-GUIDE.md",
    "docs/MEMORY-PROTOCOL.md", "templates/root/SOUL.md",
    "templates/root/IDENTITY.md", "templates/root/AGENTS.md",
    "templates/root/USER.md", "templates/root/MEMORY.md",
    "templates/root/HEARTBEAT.md", "templates/memory/INDEX.md",
    "templates/memory/INDEX-L3.md", "templates/autonomy-state/circuit-breaker.json",
    "templates/autonomy-state/probation.json", "autonomy/config/regression.json",
    "autonomy/config/policy.json", "autonomy/config/evolution.json",
    "autonomy/config/invariants.json", "autonomy/config/workspace.manifest.json",
    "autonomy/config/health-scope.json", "autonomy/config/envelope.schema.json",
]
FORBIDDEN = [
    "autonomy/state/lease.json", "autonomy/state/health-attestation.json",
    "autonomy/state/health-fingerprint.json", "autonomy/audit/events.jsonl",
    "autonomy/audit/evolution.jsonl",
]
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)
    print("[FAIL]", message)


def ok(message: str) -> None:
    print("[PASS]", message)


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            fail(f"missing required file: {rel}", errors)
    if not errors:
        ok("required package files present")

    for rel in FORBIDDEN:
        if (ROOT / rel).exists():
            fail(f"live state must not be packaged: {rel}", errors)
    if not any((ROOT / rel).exists() for rel in FORBIDDEN):
        ok("live autonomy state and audit history excluded")

    manifest_path = ROOT / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest.get("files", [])
        actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and p.name not in {"MANIFEST.json", "SHA256SUMS.txt"}}
        declared = {x["path"] for x in entries}
        if actual != declared:
            fail(f"manifest path mismatch: missing={sorted(actual-declared)}, extra={sorted(declared-actual)}", errors)
        else:
            ok(f"manifest covers {len(actual)} files")
        for item in entries:
            path = ROOT / item["path"]
            if path.is_file() and (path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]):
                fail(f"checksum mismatch: {item['path']}", errors)
        if not any("checksum mismatch" in e for e in errors):
            ok("manifest sizes and SHA-256 digests match")

    py_files = sorted(ROOT.rglob("*.py"))
    for path in py_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            fail(f"Python parse failure {path.relative_to(ROOT)}: {exc}", errors)
    if not any("Python parse failure" in e for e in errors):
        ok(f"{len(py_files)} Python files parse")

    for path in sorted(ROOT.rglob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"JSON parse failure {path.relative_to(ROOT)}: {exc}", errors)
    if not any("JSON parse failure" in e for e in errors):
        ok("all JSON files parse")

    # Credential scanners and their tests intentionally contain detection patterns
    # and synthetic fixtures. Their dedicated mandatory suites cover that behavior.
    scanner_sources = {
        "scripts/handoff.py", "scripts/test_handoff.py",
        "scripts/secretscan.py", "scripts/test_secretscan.py",
        "scripts/test_evidence.py",
    }
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file()):
        rel = path.relative_to(ROOT).as_posix()
        if rel in scanner_sources:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(f"credential-like content: {rel}", errors)
    if not any("credential-like" in e for e in errors):
        ok("no credential-like content detected outside tested scanner fixtures")

    suite_path = ROOT / "autonomy/config/regression.json"
    if suite_path.exists():
        suite = json.loads(suite_path.read_text(encoding="utf-8")).get("suite", [])
        if len(suite) != 12:
            fail(f"expected 12 regression suites, found {len(suite)}", errors)
        results = []
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for item in suite:
            script = ROOT / item["script"]
            run = subprocess.run([sys.executable, "-B", str(script), *item.get("args", [])], cwd=ROOT, env=env, capture_output=True, text=True)
            results.append((item["id"], run.returncode))
            if run.returncode != 0:
                fail(f"regression {item['id']} exit {run.returncode}: {(run.stdout+run.stderr)[-500:]}", errors)
        if results and all(code == 0 for _, code in results):
            ok("12/12 bundled regression suites pass")

    status = "PASS" if not errors else "FAIL"
    print(f"RESULT: {status} - {len(errors)} error(s)")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
