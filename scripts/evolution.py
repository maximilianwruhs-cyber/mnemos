#!/usr/bin/env python3
"""Bounded proposal applier for autonomous evolution.

A proposal is data, not code. The engine validates one pending proposal, snapshots
all affected paths through probation.py, applies it atomically, and archives the
proposal. The next tick evaluates the mandatory regression suite and either
promotes the change or restores the snapshot byte-exact.
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import probation
import snapshot

SCHEMA_VERSION = 1
CONFIG = "autonomy/config/evolution.json"
PENDING = "autonomy/evolution/pending"
ARCHIVE = "autonomy/evolution/archive"
REJECTED = "autonomy/evolution/rejected"
AUDIT = "autonomy/audit/evolution.jsonl"
HARD_IMMUTABLE = (
    "SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md",
    "scripts/snapshot.py", "scripts/probation.py", "scripts/evolution.py",
    "scripts/test_*.py", "autonomy/config/invariants.json",
    "autonomy/config/regression.json", "autonomy/config/evolution.json",
    "autonomy/state/probation.json", "autonomy/state/circuit-breaker.json",
    "autonomy/snapshots/*", "autonomy/snapshots/**", "Memory/_archive/*", "Memory/_archive/**",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".evolution-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_audit(base: Path, event: dict) -> None:
    path = base / AUDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def patterns(config: dict, key: str) -> tuple[str, ...]:
    values = config.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(key + " must be a list of strings")
    return tuple(values)


def matches(path: str, pats) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in pats)


def decode_content(op: dict) -> bytes:
    if "content_b64" in op:
        return base64.b64decode(op["content_b64"], validate=True)
    if "content" in op and isinstance(op["content"], str):
        return op["content"].encode("utf-8")
    raise ValueError("write operation requires content or content_b64")


def validate(base: Path, proposal: dict, config: dict) -> list[dict]:
    if proposal.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported proposal schema_version")
    if not isinstance(proposal.get("id"), str) or not proposal["id"].strip():
        raise ValueError("proposal id is required")
    ops = proposal.get("operations")
    maximum = int(config.get("max_operations", 4))
    if not isinstance(ops, list) or not ops or len(ops) > maximum:
        raise ValueError(f"operations must contain 1..{maximum} entries")
    allowed = patterns(config, "allowed")
    immutable = tuple(sorted(set(HARD_IMMUTABLE) | set(patterns(config, "immutable"))))
    max_bytes = int(config.get("max_total_bytes", 65536))
    total, seen, normalized = 0, set(), []
    for raw in ops:
        if not isinstance(raw, dict) or raw.get("op") not in {"write", "delete"}:
            raise ValueError("operation must be write or delete")
        rel = raw.get("path")
        if not isinstance(rel, str):
            raise ValueError("operation path is required")
        target = snapshot.resolve_inside(base, rel)
        rel = target.relative_to(base.resolve()).as_posix()
        if rel in seen:
            raise ValueError("duplicate operation path: " + rel)
        seen.add(rel)
        if not matches(rel, allowed) or matches(rel, immutable):
            raise PermissionError("path is outside the mutable envelope: " + rel)
        exists = target.is_file()
        expected = raw.get("expected_sha256")
        if exists:
            if not isinstance(expected, str) or expected != digest(target.read_bytes()):
                raise ValueError("stale or missing expected_sha256: " + rel)
        elif expected not in (None, "absent"):
            raise ValueError("new path must declare expected_sha256 absent: " + rel)
        item = {"op": raw["op"], "path": rel, "target": target}
        if raw["op"] == "write":
            data = decode_content(raw)
            total += len(data)
            item["data"] = data
        elif not exists:
            raise ValueError("delete target does not exist: " + rel)
        normalized.append(item)
    if total > max_bytes:
        raise ValueError("proposal exceeds max_total_bytes")
    return normalized


def circuit_open(base: Path) -> bool:
    path = base / probation.CIRCUIT
    return path.is_file() and bool(load_json(path).get("open"))


def reject(base: Path, proposal_path: Path, reason: str) -> dict:
    dest = base / REJECTED / proposal_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(proposal_path), str(dest))
    event = {"at": utcnow(), "event": "proposal_rejected", "proposal": proposal_path.name, "reason": reason}
    append_audit(base, event)
    return {"status": "rejected", "proposal": proposal_path.name, "reason": reason}


def apply_one(base: Path, config_path: Path | None = None) -> dict:
    if circuit_open(base):
        return {"status": "blocked", "reason": "circuit_open"}
    if probation.load_state(base).get("active"):
        return {"status": "blocked", "reason": "active_probation"}
    pending = base / PENDING
    candidates = sorted(pending.glob("*.json")) if pending.is_dir() else []
    if not candidates:
        return {"status": "no_proposal"}
    proposal_path = candidates[0]
    try:
        proposal = load_json(proposal_path)
        config = load_json(config_path or (base / CONFIG))
        if config.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported evolution config schema_version")
        ops = validate(base, proposal, config)
    except Exception as exc:
        return reject(base, proposal_path, f"{type(exc).__name__}: {exc}")
    paths = [item["path"] for item in ops]
    started = probation.begin(base, base / "autonomy/snapshots", paths, proposal.get("reason", ""))
    try:
        for item in ops:
            if item["op"] == "write":
                atomic_write(item["target"], item["data"])
            else:
                item["target"].unlink()
        dest = base / ARCHIVE / proposal_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(proposal_path), str(dest))
        event = {"at": utcnow(), "event": "proposal_applied_probationary", "proposal": proposal_path.name,
                 "change_id": started["change_id"], "paths": paths}
        append_audit(base, event)
        return {"status": "probationary_change_applied", "proposal": proposal_path.name,
                "change_id": started["change_id"], "snapshot_id": started["snapshot_id"], "paths": paths}
    except Exception as exc:
        snapshot.restore(base / "autonomy/snapshots", base, started["snapshot_id"], apply=True, only=paths)
        state = probation.load_state(base)
        state["history"].append({"change_id": started["change_id"], "at": utcnow(), "outcome": "apply_failed"})
        state["active"] = None
        probation.save_state(base, state)
        probation.open_circuit(base, "evolution_apply_failed:" + started["change_id"])
        append_audit(base, {"at": utcnow(), "event": "proposal_apply_failed", "proposal": proposal_path.name,
                            "reason": f"{type(exc).__name__}: {exc}"})
        return {"status": "apply_failed_reverted", "proposal": proposal_path.name, "reason": str(exc)}


def _selftest() -> int:
    import tempfile as tf
    checks = []
    def check(name, ok):
        checks.append((name, bool(ok)))
        print(("[PASS] " if ok else "[FAIL] ") + name)
    with tf.TemporaryDirectory() as td:
        base = Path(td)
        (base / "autonomy/config").mkdir(parents=True)
        (base / "autonomy/state").mkdir(parents=True)
        (base / "autonomy/evolution/pending").mkdir(parents=True)
        (base / "scripts").mkdir()
        (base / probation.CIRCUIT).write_text('{"open": false}')
        (base / probation.STATE).write_text('{"schema_version": 1, "active": null, "history": []}')
        config = {"schema_version": 1, "allowed": ["scripts/*.py", "Memory/**/*.md"],
                  "immutable": [], "max_operations": 2, "max_total_bytes": 1000}
        (base / CONFIG).write_text(json.dumps(config))
        victim = base / "scripts/demo.py"
        victim.write_text("old\n")
        proposal = {"schema_version": 1, "id": "p1", "reason": "test", "operations": [
            {"op": "write", "path": "scripts/demo.py", "expected_sha256": digest(b"old\n"), "content": "new\n"}]}
        (base / PENDING / "001.json").write_text(json.dumps(proposal))
        result = apply_one(base)
        check("valid proposal applied", result["status"] == "probationary_change_applied")
        check("content changed", victim.read_text() == "new\n")
        check("probation active", probation.load_state(base).get("active") is not None)
        check("proposal archived", (base / ARCHIVE / "001.json").is_file())
        check("audit appended", (base / AUDIT).read_text().count("proposal_applied_probationary") == 1)
        (base / PENDING / "002.json").write_text(json.dumps(proposal))
        check("second change blocked", apply_one(base)["reason"] == "active_probation")
        (base / PENDING / "002.json").unlink()
        state = probation.load_state(base); state["active"] = None; probation.save_state(base, state)
        bad = {"schema_version": 1, "id": "bad", "operations": [
            {"op": "write", "path": "AGENTS.md", "expected_sha256": "absent", "content": "x"}]}
        (base / PENDING / "003.json").write_text(json.dumps(bad))
        rejected = apply_one(base)
        check("immutable proposal rejected", rejected["status"] == "rejected")
        check("rejected proposal retained", (base / REJECTED / "003.json").is_file())
        stale = {"schema_version": 1, "id": "stale", "operations": [
            {"op": "write", "path": "scripts/demo.py", "expected_sha256": "0" * 64, "content": "x"}]}
        (base / PENDING / "004.json").write_text(json.dumps(stale))
        check("stale proposal rejected", apply_one(base)["status"] == "rejected")
        (base / probation.CIRCUIT).write_text('{"open": true}')
        check("open circuit blocks evolution", apply_one(base)["reason"] == "circuit_open")
        check("path traversal refused", _raises(lambda: validate(base, {"schema_version":1,"id":"x","operations":[{"op":"write","path":"../x","expected_sha256":"absent","content":"x"}]}, config)))
        check("duplicate path refused", _raises(lambda: validate(base, {"schema_version":1,"id":"x","operations":[{"op":"write","path":"scripts/x.py","expected_sha256":"absent","content":"x"},{"op":"delete","path":"scripts/x.py"}]}, config)))
    passed = sum(ok for _, ok in checks)
    print(f"RESULT: {passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


def _raises(callable_) -> bool:
    try:
        callable_()
    except Exception:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply one bounded evolution proposal.")
    parser.add_argument("--base")
    parser.add_argument("--config")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    if not args.base:
        parser.error("--base is required")
    result = apply_one(Path(args.base).resolve(), Path(args.config) if args.config else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] in {"rejected", "apply_failed_reverted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
