#!/usr/bin/env python3
"""Regression tests for the deterministic autonomy dispatcher."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("autonomy_dispatcher.py")
spec = importlib.util.spec_from_file_location("autonomy_dispatcher", MODULE_PATH)
ad = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ad)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ad.initialize(self.root)
        policy = ad.default_policy()
        ad.atomic_write_json(self.root / "config/policy.json", policy)

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self):
        return ad.tick(self.root, owner="test-run")

    def test_full_approved_write_text_cycle(self):
        ad.enqueue(self.root, "reports", {
            "schema_version": 1,
            "id": "report-001",
            "kind": "report",
            "priority": 50,
            "approval_scope": "workspace_artifacts",
            "proposed_action": {
                "type": "write_text",
                "path": "artifacts/result.txt",
                "content": "verified output\n"
            }
        })
        self.assertEqual(self.tick()["transition"], "report_to_task")
        self.assertEqual(self.tick()["transition"], "task_to_handoff")
        self.assertEqual((self.root / "artifacts/result.txt").read_text(), "verified output\n")
        self.assertEqual(self.tick()["transition"], "handoff_accepted")
        accepted = list((self.root / "handoffs/accepted").glob("*.json"))
        self.assertEqual(len(accepted), 1)

    def test_unapproved_action_is_rejected_without_execution(self):
        ad.enqueue(self.root, "tasks", {
            "schema_version": 1,
            "id": "task-002",
            "kind": "task",
            "priority": 10,
            "retry_count": 0,
            "approval_scope": "workspace_artifacts",
            "action": {"type": "shell", "command": "echo unsafe"}
        })
        result = self.tick()
        self.assertEqual(result["transition"], "task_rejected")
        self.assertFalse((self.root / "artifacts/result.txt").exists())

    def test_path_escape_is_rejected(self):
        ad.enqueue(self.root, "tasks", {
            "schema_version": 1,
            "id": "task-003",
            "kind": "task",
            "priority": 10,
            "retry_count": 0,
            "approval_scope": "workspace_artifacts",
            "action": {"type": "write_text", "path": "../escape.txt", "content": "no"}
        })
        self.assertEqual(self.tick()["transition"], "task_rejected")
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_active_lease_blocks_second_owner(self):
        ad.acquire_lease(self.root, "first", ttl_seconds=600)
        result = ad.tick(self.root, owner="second")
        self.assertEqual(result["transition"], "lease_busy")

    def test_verification_failure_requeues_archived_task(self):
        ad.enqueue(self.root, "tasks", {
            "schema_version": 1,
            "id": "task-retry",
            "kind": "task",
            "priority": 10,
            "retry_count": 0,
            "approval_scope": "workspace_artifacts",
            "action": {"type": "write_text", "path": "artifacts/retry.txt", "content": "original\n"}
        })
        self.assertEqual(self.tick()["transition"], "task_to_handoff")
        (self.root / "artifacts/retry.txt").write_text("tampered\n", encoding="utf-8")
        result = self.tick()
        self.assertEqual(result["transition"], "handoff_rejected")
        requeued = json.loads((self.root / "tasks/pending/task-retry.json").read_text())
        self.assertEqual(requeued["retry_count"], 1)

    def test_verification_failure_requeues_until_circuit_breaker(self):
        policy = ad.default_policy()
        policy["max_retries"] = 0
        ad.atomic_write_json(self.root / "config/policy.json", policy)
        handoff = {
            "schema_version": 1,
            "id": "handoff-004",
            "kind": "handoff",
            "task_id": "task-004",
            "retry_count": 0,
            "artifact_path": "artifacts/missing.txt",
            "expected_sha256": "0" * 64
        }
        ad.enqueue(self.root, "handoffs", handoff)
        result = self.tick()
        self.assertEqual(result["transition"], "circuit_opened")
        state = json.loads((self.root / "state/circuit-breaker.json").read_text())
        self.assertTrue(state["open"])

    def test_one_transition_per_tick_and_audit_is_jsonl(self):
        for number in (1, 2):
            ad.enqueue(self.root, "reports", {
                "schema_version": 1,
                "id": f"report-{number}",
                "kind": "report",
                "priority": number,
                "approval_scope": "workspace_artifacts",
                "proposed_action": {"type": "noop"}
            })
        self.tick()
        self.assertEqual(len(list((self.root / "reports/pending").glob("*.json"))), 1)
        events = [json.loads(line) for line in (self.root / "audit/events.jsonl").read_text().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(event["language"] == "en" for event in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
