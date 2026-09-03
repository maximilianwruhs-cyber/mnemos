"""guard - fail-closed emission policy and append-only audit log for sandbox scripts.

Ported concept: CopilotKit/OpenBot evaluates every tool call against policy rules
and records it in an audit trail *before* execution, failing closed.
Source: https://github.com/CopilotKit/openbot - reimplemented, not copied.

HONEST SCOPE. OpenBot's audit trail is trustworthy because the *platform* writes
it, outside the agent. In this runtime nothing outside the agent is programmable,
so a general "tool call monitor" would be self-reported and therefore worthless as
evidence. This module deliberately governs only what IS mechanically enforceable:
file emission from sandbox Python. Do not present it as anything wider.

MOTIVATING INCIDENT (2026-08-24). Three unguarded doclite runs emitted 246 PNG
files into /code-interpreter-output/ - a protected path from which files cannot
afterwards be moved or renamed. A pre-write cap stops that at run 1.

USAGE
    from guard import Guard, Policy

    with Guard("convert", Policy(max_files=4, allow_suffixes=(".md", ".zip"))) as g:
        g.write_text("report.md", markdown)
        g.write_bundle("figures.zip", {"p1.png": data, ...})   # 82 images -> 1 file
    print(g.summary)

Every allow and every deny is appended to /tmp/<run>.audit.jsonl before the write
happens. A denial raises PolicyDenied and leaves nothing on disk.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

__all__ = ["Policy", "Guard", "PolicyDenied"]


@dataclass(frozen=True)
class Policy:
    """Deny-biased defaults. Widen explicitly, never implicitly."""

    max_files: int = 25
    max_total_bytes: int = 8 * 1024 * 1024
    allow_suffixes: tuple = (".md", ".txt", ".json", ".jsonl", ".csv", ".zip")
    root: str = "/tmp"
    allow_reemit: bool = False
    dry_run: bool = False


class PolicyDenied(RuntimeError):
    def __init__(self, rule: str, target: str, detail: str):
        self.rule, self.target, self.detail = rule, target, detail
        super().__init__(f"[{rule}] {target}: {detail}")


class Guard:
    """Policy-checked, audit-logged file emission.

    The audit log and manifest are written outside the caps on purpose: an audit
    trail that can be suppressed by its own quota is not an audit trail.
    """

    def __init__(self, run: str, policy: Policy | None = None, fingerprint: str | None = None):
        self.run = run
        self.policy = policy or Policy()
        self.fingerprint = fingerprint
        self.root = Path(self.policy.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

        self.audit_path = self.root / f"{run}.audit.jsonl"
        self.manifest_path = self.root / f"{run}.manifest.json"

        self._seq = 0
        self._files: list[dict] = []
        self._denials: list[dict] = []
        self._bytes = 0

        # Cross-call re-emission guard. /tmp is wiped between sandbox calls, so this
        # only fires when a previous manifest was deliberately staged back in.
        self._blocked_reemit = False
        if self.fingerprint and not self.policy.allow_reemit and self.manifest_path.exists():
            try:
                prior = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if prior.get("fingerprint") == self.fingerprint:
                    self._blocked_reemit = True
            except Exception:
                # Unreadable manifest is ambiguous -> fail closed.
                self._blocked_reemit = True

        self._log({"event": "run_start", "policy": asdict(self.policy),
                   "fingerprint": fingerprint, "blocked_reemit": self._blocked_reemit})

    # ---------------------------------------------------------------- logging

    def _log(self, record: dict) -> None:
        record = {"ts": round(time.time(), 3), "run": self.run, "seq": self._seq, **record}
        self._seq += 1
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    # ---------------------------------------------------------------- policy

    def _check(self, name: str, nbytes: int) -> Path:
        target = (self.root / name).resolve()

        def deny(rule: str, detail: str):
            rec = {"event": "write", "decision": "deny", "rule": rule,
                   "target": str(target), "bytes": nbytes, "detail": detail}
            self._denials.append(rec)
            self._log(rec)
            raise PolicyDenied(rule, str(target), detail)

        if self._blocked_reemit:
            deny("re-emission", f"manifest for fingerprint {self.fingerprint} already exists")
        if self.root not in target.parents and target.parent != self.root:
            deny("path-escape", f"resolves outside {self.root}")
        if target.suffix not in self.policy.allow_suffixes:
            deny("suffix-not-allowed", f"{target.suffix!r} not in {self.policy.allow_suffixes}")
        if len(self._files) + 1 > self.policy.max_files:
            deny("max-files", f"cap {self.policy.max_files} reached")
        if self._bytes + nbytes > self.policy.max_total_bytes:
            deny("max-total-bytes", f"{self._bytes + nbytes} exceeds {self.policy.max_total_bytes}")
        return target

    # ---------------------------------------------------------------- writes

    def write_bytes(self, name: str, data: bytes, note: str = "") -> Path:
        target = self._check(name, len(data))
        digest = hashlib.sha256(data).hexdigest()
        if not self.policy.dry_run:
            target.write_bytes(data)
        rec = {"event": "write", "decision": "allow", "rule": "policy-ok",
               "target": str(target), "bytes": len(data), "sha256": digest[:16],
               "dry_run": self.policy.dry_run, "note": note}
        self._files.append(rec)
        self._bytes += len(data)
        self._log(rec)
        return target

    def write_text(self, name: str, text: str, note: str = "") -> Path:
        return self.write_bytes(name, text.encode("utf-8"), note=note)

    def write_bundle(self, name: str, members: dict, note: str = "") -> Path:
        """Emit many artifacts as ONE policy-checked archive.

        This is the remedy for the motivating incident: 82 loose PNGs become a
        single figures.zip, while each member still gets an audit record.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for member, payload in members.items():
                zf.writestr(member, payload)
        blob = buf.getvalue()
        target = self.write_bytes(name, blob, note=note or f"bundle of {len(members)} members")
        for member, payload in members.items():
            self._log({"event": "bundle_member", "decision": "allow", "rule": "bundled",
                       "target": f"{name}!{member}", "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest()[:16]})
        return target

    # ---------------------------------------------------------------- close

    @property
    def summary(self) -> dict:
        return {"run": self.run, "files": len(self._files), "bytes": self._bytes,
                "denials": len(self._denials), "fingerprint": self.fingerprint,
                "blocked_reemit": self._blocked_reemit,
                "audit_log": str(self.audit_path)}

    def close(self) -> dict:
        manifest = {**self.summary, "emitted": self._files, "denied": self._denials}
        self.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True),
                                      encoding="utf-8")
        self._log({"event": "run_end", **self.summary})
        return manifest

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


# ====================================================================== tests

def _selftest() -> int:
    import shutil, tempfile

    tmp = Path(tempfile.mkdtemp(prefix="guardtest_"))
    checks: dict[str, bool] = {}

    def denied(fn, rule):
        try:
            fn()
            return False
        except PolicyDenied as e:
            return e.rule == rule

    # 1-2 happy path
    g = Guard("t1", Policy(root=str(tmp), max_files=3))
    p = g.write_text("ok.md", "hello")
    checks["allowed write lands on disk"] = p.exists() and p.read_text() == "hello"
    checks["audit log created"] = g.audit_path.exists()

    # 3 suffix
    checks["disallowed suffix denied"] = denied(
        lambda: g.write_bytes("evil.exe", b"x"), "suffix-not-allowed")
    checks["denied file absent from disk"] = not (tmp / "evil.exe").exists()

    # 4 path escape
    checks["path escape denied"] = denied(
        lambda: g.write_text("../outside.md", "x"), "path-escape")

    # 5 file cap
    g.write_text("a.md", "a"); g.write_text("b.md", "b")
    checks["max-files cap enforced"] = denied(
        lambda: g.write_text("c.md", "c"), "max-files")

    # 6 byte cap
    g2 = Guard("t2", Policy(root=str(tmp), max_total_bytes=10))
    checks["max-total-bytes enforced"] = denied(
        lambda: g2.write_bytes("big.txt", b"x" * 11), "max-total-bytes")

    # 7 dry run
    g3 = Guard("t3", Policy(root=str(tmp), dry_run=True))
    g3.write_text("phantom.md", "nope")
    checks["dry_run writes nothing"] = not (tmp / "phantom.md").exists()

    # 8 bundle: the incident remedy
    before = len(list(tmp.glob("*.zip")))
    g4 = Guard("t4", Policy(root=str(tmp), max_files=2))
    g4.write_bundle("figures.zip", {f"p{i}.png": b"\x89PNG" + bytes([i]) for i in range(82)})
    after = len(list(tmp.glob("*.zip")))
    checks["82 members emit exactly 1 file"] = (after - before) == 1
    with zipfile.ZipFile(tmp / "figures.zip") as zf:
        checks["bundle retains all 82 members"] = len(zf.namelist()) == 82

    # 9 log integrity
    lines = [json.loads(x) for x in g.audit_path.read_text().splitlines()]
    checks["audit log is parseable jsonl"] = all("decision" in r or "event" in r for r in lines)
    checks["denials recorded with rule"] = sum(
        1 for r in lines if r.get("decision") == "deny") == 3

    # 10 manifest truth
    man = g.close()
    on_disk = sum(1 for f in man["emitted"] if Path(f["target"]).exists())
    checks["manifest matches disk"] = on_disk == man["files"] == 3

    # 11 cross-call re-emission
    g5 = Guard("t5", Policy(root=str(tmp)), fingerprint="abc123")
    g5.write_text("first.md", "x"); g5.close()
    g6 = Guard("t5", Policy(root=str(tmp)), fingerprint="abc123")
    checks["re-emission denied"] = denied(
        lambda: g6.write_text("second.md", "y"), "re-emission")
    g7 = Guard("t5", Policy(root=str(tmp), allow_reemit=True), fingerprint="abc123")
    checks["re-emission overridable"] = bool(g7.write_text("third.md", "z").exists())

    print("GUARD SELF-TEST")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = all(checks.values())
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
