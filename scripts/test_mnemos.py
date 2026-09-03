#!/usr/bin/env python3
"""Regression tests for MNEMOS using real temporary files and the public API."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

MODULE_PATH = Path("/tmp/mnemos.py")
spec = importlib.util.spec_from_file_location("mnemos", MODULE_PATH)
mnemos = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mnemos
spec.loader.exec_module(mnemos)

TODAY = date(2026, 8, 24)
EVIDENCE_LINE = (
    '- **Evidence:** {"date":"2026-08-24","stance":"SUPPORT",'
    '"source":"local regression","quote":"The behavior was observed."}\n'
)
CHALLENGE_LINE = (
    '- **Evidence:** {"date":"2026-08-24","stance":"CHALLENGE",'
    '"source":"counter-probe","quote":"FAIL"}\n'
)
VALID_NOTE = """# MEMORY.md — Core Memory (L2)

### [MEM-2026-0001] Verified local behavior

- **Type:** Gotcha · **Confidence:** VERIFIED · **Salience:** 0.80
- **Created:** 2026-08-24 · **Last-Access:** 2026-08-24 · **Freq:** 1
- **Tags:** #test #local
- **Links:**
- **Provenance:** Executed locally.
- **Observation:** The behavior was observed.
- **Directive:** Use the verified path.
""" + EVIDENCE_LINE
AGENTS = "# AGENTS.md\n\n- Deterministic test fixture.\n"


class MnemosTests(unittest.TestCase):
    def run_case(self, memory_text: str, index_text: str | None = None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            agents = root / "AGENTS.md"
            output = root / "generated.md"
            existing = root / "INDEX.md"
            memory.write_text(memory_text, encoding="utf-8")
            agents.write_text(AGENTS, encoding="utf-8")
            if index_text is not None:
                existing.write_text(index_text, encoding="utf-8")
            findings, rows, generated = mnemos.audit(
                memory, agents, output, existing if index_text is not None else None, TODAY
            )
            return findings, rows, generated, output.read_text(encoding="utf-8")

    def assert_failure(self, text: str, detail: str):
        findings, _, _, _ = self.run_case(text)
        self.assertTrue(any(f.level == "FAIL" and detail in f.detail for f in findings), findings)

    def test_valid_substrate_passes_and_index_is_deterministic(self):
        findings, rows, generated, written = self.run_case(VALID_NOTE)
        self.assertFalse(any(f.level != "PASS" for f in findings), findings)
        self.assertEqual(generated, written)
        self.assertEqual(rows[0]["action"], "KEEP")

    def test_duplicate_id_fails(self):
        self.assert_failure(VALID_NOTE + VALID_NOTE.split("### ", 1)[1].join(["### ", ""]), "duplicate note ID")

    def test_invalid_confidence_fails(self):
        self.assert_failure(VALID_NOTE.replace("VERIFIED", "CERTAIN"), "invalid confidence")

    def test_invalid_type_fails(self):
        self.assert_failure(VALID_NOTE.replace("Gotcha", "Unknown-Type"), "invalid type")

    def test_out_of_range_salience_fails(self):
        self.assert_failure(VALID_NOTE.replace("0.80", "1.20"), "invalid salience")

    def test_unresolved_link_fails(self):
        self.assert_failure(VALID_NOTE.replace("**Links:**", "**Links:** [[MEM-2026-9999]]"), "unresolved link")

    def test_invalid_frequency_fails(self):
        self.assert_failure(VALID_NOTE.replace("**Freq:** 1", "**Freq:** -1"), "invalid frequency")

    def test_future_date_fails(self):
        self.assert_failure(VALID_NOTE.replace("2026-08-24", "2026-08-25", 1), "date is in the future")

    def test_stale_index_fails(self):
        findings, _, _, _ = self.run_case(VALID_NOTE, "stale\n")
        self.assertTrue(any(f.level == "FAIL" and f.subject == "INDEX.md" for f in findings))

    def test_synchronized_index_passes(self):
        _, _, generated, _ = self.run_case(VALID_NOTE)
        findings, _, _, _ = self.run_case(VALID_NOTE, generated)
        self.assertTrue(any(f.level == "PASS" and f.subject == "INDEX.md" for f in findings))
        self.assertFalse(any(f.level == "FAIL" for f in findings))

    def test_missing_evidence_fails(self):
        self.assert_failure(VALID_NOTE.replace(EVIDENCE_LINE, ""),
                            "Evidence records are required")

    def test_challenge_warns_without_changing_action(self):
        text = VALID_NOTE + CHALLENGE_LINE
        findings, rows, generated, _ = self.run_case(text)
        self.assertTrue(any(f.level == "WARN" and "contested" in f.detail
                            for f in findings))
        self.assertEqual(rows[0]["action"], "KEEP")
        self.assertIn("| 1/1 |", generated)

    def test_evidence_counts_are_derived_in_index(self):
        _, _, generated, _ = self.run_case(VALID_NOTE)
        self.assertIn("| S/C |", generated)
        self.assertIn("| 1/0 |", generated)
        self.assertNotIn("Proofs", generated)

    def test_secret_in_evidence_is_reported_once_and_masked(self):
        token = "ghp_" + "x" * 36
        text = VALID_NOTE.replace("local regression", token)
        findings, _, _, _ = self.run_case(text)
        secret_findings = [f for f in findings if f.detail.startswith("possible ")]
        self.assertEqual(len(secret_findings), 1, secret_findings)
        self.assertNotIn(token, secret_findings[0].detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
