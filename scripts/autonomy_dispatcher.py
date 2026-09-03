#!/usr/bin/env python3
"""Deterministic, file-based autonomy dispatcher.

Each invocation performs at most one durable state transition. The dispatcher
only executes action types and approval scopes explicitly listed in policy.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

QUEUE_STATES = {
    "reports": ("pending", "processing", "archive", "rejected"),
    "tasks": ("pending", "processing", "archive", "failed"),
    "handoffs": ("pending", "processing", "accepted", "rejected"),
}
PRIORITY_ORDER = ("handoffs", "tasks", "reports")


def now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or now()).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "internal_language": "en",
        "one_transition_per_tick": True,
        "lease_ttl_seconds": 900,
        "max_retries": 3,
        "approved_scopes": ["workspace_artifacts"],
        "allowed_actions": ["noop", "write_text"],
        "allowed_write_prefixes": ["artifacts/"],
        "protected_paths": ["SOUL.md", "IDENTITY.md", "AGENTS.md", "USER.md", "MEMORY.md"],
        "require_explicit_schedule_approval": True,
    }


def initialize(root: Path) -> None:
    for queue, states in QUEUE_STATES.items():
        for state in states:
            (root / queue / state).mkdir(parents=True, exist_ok=True)
    for directory in ("config", "state", "audit", "artifacts"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    policy_path = root / "config/policy.json"
    if not policy_path.exists():
        atomic_write_json(policy_path, default_policy())
    circuit_path = root / "state/circuit-breaker.json"
    if not circuit_path.exists():
        atomic_write_json(circuit_path, {"open": False, "opened_at": None, "reason": None})


def append_event(root: Path, event_type: str, **details: Any) -> None:
    event = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "occurred_at": timestamp(),
        "language": "en",
        "event_type": event_type,
        **details,
    }
    path = root / "audit/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def validate_envelope(payload: dict[str, Any], expected_kind: str) -> None:
    required = {"schema_version", "id", "kind"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if payload["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    if payload["kind"] != expected_kind:
        raise ValueError(f"Expected kind {expected_kind}")
    identifier = payload["id"]
    if not isinstance(identifier, str) or not identifier or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in identifier):
        raise ValueError("Invalid identifier")


def enqueue(root: Path, queue: str, payload: dict[str, Any]) -> Path:
    if queue not in QUEUE_STATES:
        raise ValueError("Unknown queue")
    expected = {"reports": "report", "tasks": "task", "handoffs": "handoff"}[queue]
    validate_envelope(payload, expected)
    destination = root / queue / "pending" / f"{payload['id']}.json"
    if destination.exists():
        raise FileExistsError(f"Queue item already exists: {payload['id']}")
    atomic_write_json(destination, payload)
    return destination


def acquire_lease(root: Path, owner: str, ttl_seconds: int) -> bool:
    lease_path = root / "state/lease.json"
    current = now()
    if lease_path.exists():
        try:
            lease = read_json(lease_path)
            expires = datetime.fromisoformat(lease["expires_at"].replace("Z", "+00:00"))
            if expires > current and lease.get("owner") != owner:
                return False
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    atomic_write_json(lease_path, {
        "owner": owner,
        "acquired_at": timestamp(current),
        "expires_at": timestamp(current + timedelta(seconds=ttl_seconds)),
    })
    return True


def release_lease(root: Path, owner: str) -> None:
    lease_path = root / "state/lease.json"
    if lease_path.exists():
        try:
            if read_json(lease_path).get("owner") == owner:
                lease_path.unlink()
        except (ValueError, json.JSONDecodeError):
            lease_path.unlink()


def resolve_write_path(root: Path, relative: str, policy: dict[str, Any]) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Action path must be a non-empty string")
    normalized = Path(relative).as_posix().lstrip("/")
    if any(part in ("", ".", "..") for part in Path(normalized).parts):
        raise ValueError("Action path escapes the workspace")
    if normalized in policy["protected_paths"]:
        raise ValueError("Action targets a protected path")
    if not any(normalized.startswith(prefix) for prefix in policy["allowed_write_prefixes"]):
        raise ValueError("Action path is outside allowed prefixes")
    destination = (root / normalized).resolve()
    if root.resolve() not in destination.parents:
        raise ValueError("Action path escapes the workspace")
    return destination


def claim_next(root: Path, queue: str) -> tuple[Path, dict[str, Any]] | None:
    candidates = []
    for path in (root / queue / "pending").glob("*.json"):
        try:
            payload = read_json(path)
            priority = int(payload.get("priority", 100))
            candidates.append((priority, path.name, path, payload))
        except (ValueError, json.JSONDecodeError):
            candidates.append((10**9, path.name, path, {}))
    if not candidates:
        return None
    _, _, source, payload = min(candidates)
    target = root / queue / "processing" / source.name
    os.replace(source, target)
    return target, payload


def move(path: Path, target_directory: Path) -> Path:
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / path.name
    os.replace(path, target)
    return target


def process_report(root: Path, path: Path, report: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_envelope(report, "report")
        action = report["proposed_action"]
        if report.get("approval_scope") not in policy["approved_scopes"]:
            raise ValueError("Approval scope is not authorized")
        task = {
            "schema_version": 1,
            "id": report["id"].replace("report-", "task-", 1),
            "kind": "task",
            "priority": report.get("priority", 100),
            "retry_count": 0,
            "approval_scope": report["approval_scope"],
            "source_report_id": report["id"],
            "action": action,
        }
        enqueue(root, "tasks", task)
        move(path, root / "reports/archive")
        append_event(root, "report_planned", source_id=report["id"], output_id=task["id"])
        return {"transition": "report_to_task", "item_id": report["id"]}
    except Exception as exc:
        move(path, root / "reports/rejected")
        append_event(root, "report_rejected", source_id=report.get("id", path.stem), reason=str(exc))
        return {"transition": "report_rejected", "item_id": report.get("id", path.stem), "reason": str(exc)}


def process_task(root: Path, path: Path, task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_envelope(task, "task")
        action = task["action"]
        action_type = action.get("type")
        if task.get("approval_scope") not in policy["approved_scopes"]:
            raise ValueError("Approval scope is not authorized")
        if action_type not in policy["allowed_actions"]:
            raise ValueError("Action type is not authorized")
        artifact_path = None
        digest = None
        if action_type == "write_text":
            destination = resolve_write_path(root, action.get("path"), policy)
            content = action.get("content")
            if not isinstance(content, str):
                raise ValueError("write_text content must be a string")
            atomic_write_text(destination, content)
            artifact_path = destination.relative_to(root).as_posix()
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        handoff = {
            "schema_version": 1,
            "id": task["id"].replace("task-", "handoff-", 1),
            "kind": "handoff",
            "task_id": task["id"],
            "retry_count": int(task.get("retry_count", 0)),
            "action_type": action_type,
            "artifact_path": artifact_path,
            "expected_sha256": digest,
        }
        enqueue(root, "handoffs", handoff)
        move(path, root / "tasks/archive")
        append_event(root, "task_executed", source_id=task["id"], output_id=handoff["id"], action_type=action_type)
        return {"transition": "task_to_handoff", "item_id": task["id"]}
    except Exception as exc:
        move(path, root / "tasks/failed")
        append_event(root, "task_rejected", source_id=task.get("id", path.stem), reason=str(exc))
        return {"transition": "task_rejected", "item_id": task.get("id", path.stem), "reason": str(exc)}


def requeue_archived_task(root: Path, task_id: str, retry_count: int) -> None:
    archived = root / "tasks/archive" / f"{task_id}.json"
    if not archived.is_file():
        raise FileNotFoundError(f"Archived task not found: {task_id}")
    task = read_json(archived)
    validate_envelope(task, "task")
    task["retry_count"] = retry_count
    atomic_write_json(archived, task)
    os.replace(archived, root / "tasks/pending" / archived.name)


def process_handoff(root: Path, path: Path, handoff: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_envelope(handoff, "handoff")
        artifact = handoff.get("artifact_path")
        expected = handoff.get("expected_sha256")
        if artifact is None:
            valid = handoff.get("action_type") == "noop" and expected is None
        else:
            destination = resolve_write_path(root, artifact, policy)
            valid = destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected
        if valid:
            move(path, root / "handoffs/accepted")
            append_event(root, "handoff_accepted", source_id=handoff["id"], task_id=handoff["task_id"])
            return {"transition": "handoff_accepted", "item_id": handoff["id"]}
        retries = int(handoff.get("retry_count", 0)) + 1
        move(path, root / "handoffs/rejected")
        if retries > int(policy["max_retries"]):
            atomic_write_json(root / "state/circuit-breaker.json", {
                "open": True,
                "opened_at": timestamp(),
                "reason": f"Verification failed for {handoff['task_id']} after retry limit",
            })
            append_event(root, "circuit_opened", source_id=handoff["id"], task_id=handoff["task_id"])
            return {"transition": "circuit_opened", "item_id": handoff["id"]}
        requeue_archived_task(root, handoff["task_id"], retries)
        append_event(
            root,
            "handoff_rejected",
            source_id=handoff["id"],
            task_id=handoff["task_id"],
            retry_count=retries,
            outcome="task_requeued",
        )
        return {"transition": "handoff_rejected", "item_id": handoff["id"], "task_requeued": True}
    except Exception as exc:
        move(path, root / "handoffs/rejected")
        append_event(root, "handoff_rejected", source_id=handoff.get("id", path.stem), reason=str(exc))
        return {"transition": "handoff_rejected", "item_id": handoff.get("id", path.stem), "reason": str(exc)}


def tick(root: Path, owner: str | None = None) -> dict[str, Any]:
    initialize(root)
    policy = read_json(root / "config/policy.json")
    if policy.get("internal_language") != "en":
        raise ValueError("Internal language policy must be English")
    circuit = read_json(root / "state/circuit-breaker.json")
    if circuit.get("open"):
        return {"transition": "circuit_open", "reason": circuit.get("reason")}
    owner = owner or f"run-{uuid.uuid4()}"
    if not acquire_lease(root, owner, int(policy["lease_ttl_seconds"])):
        return {"transition": "lease_busy"}
    try:
        for queue in PRIORITY_ORDER:
            claimed = claim_next(root, queue)
            if claimed is None:
                continue
            path, payload = claimed
            if queue == "reports":
                return process_report(root, path, payload, policy)
            if queue == "tasks":
                return process_task(root, path, payload, policy)
            return process_handoff(root, path, payload, policy)
        append_event(root, "idle")
        return {"transition": "idle"}
    finally:
        release_lease(root, owner)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one deterministic autonomy state transition.")
    parser.add_argument("--root", type=Path, required=True, help="Autonomy workspace root")
    parser.add_argument("--initialize", action="store_true", help="Create the workspace and exit")
    parser.add_argument("--owner", help="Stable lease owner identifier")
    args = parser.parse_args()
    if args.initialize:
        initialize(args.root)
        print(json.dumps({"status": "initialized", "root": str(args.root)}))
        return 0
    print(json.dumps(tick(args.root, args.owner), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
