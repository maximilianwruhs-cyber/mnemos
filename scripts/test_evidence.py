#!/usr/bin/env python3
"""Schema and append regression tests for the pure evidence ledger."""
from __future__ import annotations

import importlib.util
import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


def load_module(filename):
    path = Path("/tmp") / filename
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evidence = load_module("evidence.py")
TODAY = date(2026, 9, 3)
NOTE_WITH_SUPPORT = ('- **Evidence:** {"date":"2026-09-02","stance":"SUPPORT",'
                     '"source":"first probe","quote":"PASS"}\n')
CHALLENGE_LINE = ('- **Evidence:** {"date":"2026-09-03","stance":"CHALLENGE",'
                  '"source":"counter-probe","quote":"FAIL"}\n')
SECOND_SUPPORT = {"date": "2026-09-03", "stance": "SUPPORT",
                  "source": "second probe", "quote": "PASS"}
SUPPORT_TEMPLATE = (
    '- **Evidence:** {{"date":"2026-09-02","stance":"SUPPORT",'
    '"source":"probe {n}","quote":"PASS"}}\n'
)


class EvidenceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evidence, "evidence.py is not implemented")

    def test_one_support_record_is_valid_and_counted(self):
        report = evidence.inspect(NOTE_WITH_SUPPORT, TODAY)
        self.assertEqual(report.findings, ())
        self.assertEqual((report.support, report.challenge), (1, 0))
        self.assertFalse(report.contested)

    def test_missing_support_is_a_structural_failure(self):
        report = evidence.inspect("- **Directive:** Act.\n", TODAY)
        self.assertTrue(any(f.level == "FAIL" and "Evidence" in f.detail
                            for f in report.findings))

    def test_challenge_is_valid_but_contested(self):
        report = evidence.inspect(NOTE_WITH_SUPPORT + CHALLENGE_LINE, TODAY)
        self.assertEqual([f.level for f in report.findings], ["WARN"])
        self.assertEqual((report.support, report.challenge), (1, 1))
        self.assertTrue(report.contested)

    def test_append_preserves_existing_bytes_and_uses_canonical_json(self):
        updated = evidence.append(NOTE_WITH_SUPPORT, SECOND_SUPPORT, TODAY)
        self.assertTrue(updated.startswith(NOTE_WITH_SUPPORT))
        self.assertIn(
            '- **Evidence:** {"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"second probe","quote":"PASS"}\n', updated)

    def test_invalid_records_fail(self):
        cases = {
            "malformed": "{bad json}",
            "unknown key": '{"date":"2026-09-03","stance":"SUPPORT","source":"x","quote":"y","extra":"z"}',
            "future": '{"date":"2026-09-04","stance":"SUPPORT","source":"x","quote":"y"}',
            "bad stance": '{"date":"2026-09-03","stance":"MAYBE","source":"x","quote":"y"}',
            "empty source": '{"date":"2026-09-03","stance":"SUPPORT","source":"","quote":"y"}',
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
                self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_source_length_241_fails(self):
        long_source = "x" * 241
        payload = (
            '{"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"' + long_source + '","quote":"y"}'
        )
        report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
        self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_quote_length_281_fails(self):
        long_quote = "y" * 281
        payload = (
            '{"date":"2026-09-03","stance":"SUPPORT","source":"x",'
            '"quote":"' + long_quote + '"}'
        )
        report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
        self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_append_rejects_physical_newlines(self):
        bad = dict(SECOND_SUPPORT)
        bad["quote"] = "line1\nline2"
        with self.assertRaises(ValueError):
            evidence.append(NOTE_WITH_SUPPORT, bad, TODAY)

    def test_exact_duplicate_is_rejected(self):
        dup = {
            "date": "2026-09-02",
            "stance": "SUPPORT",
            "source": "first probe",
            "quote": "PASS",
        }
        with self.assertRaises(ValueError):
            evidence.append(NOTE_WITH_SUPPORT, dup, TODAY)
        report = evidence.inspect(NOTE_WITH_SUPPORT + NOTE_WITH_SUPPORT, TODAY)
        self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_seventeenth_record_is_rejected(self):
        body = "".join(SUPPORT_TEMPLATE.format(n=i) for i in range(16))
        report = evidence.inspect(body, TODAY)
        self.assertEqual(report.findings, ())
        self.assertEqual(report.support, 16)
        seventeenth = {
            "date": "2026-09-03",
            "stance": "SUPPORT",
            "source": "overflow",
            "quote": "too many",
        }
        with self.assertRaises(ValueError):
            evidence.append(body, seventeenth, TODAY)
        overflow = body + (
            '- **Evidence:** {"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"overflow","quote":"too many"}\n'
        )
        report = evidence.inspect(overflow, TODAY)
        self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_noncanonical_key_order_and_spacing_fail(self):
        cases = {
            "key order": (
                '- **Evidence:** {"stance":"SUPPORT","date":"2026-09-02",'
                '"source":"first probe","quote":"PASS"}\n'
            ),
            "spacing": (
                '- **Evidence:** {"date": "2026-09-02", "stance": "SUPPORT", '
                '"source": "first probe", "quote": "PASS"}\n'
            ),
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                report = evidence.inspect(text, TODAY)
                self.assertTrue(any(f.level == "FAIL" for f in report.findings))

    def test_indented_or_nonjson_evidence_beside_valid_support_fails(self):
        indented = (
            '  - **Evidence:** {"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"indented","quote":"PASS"}\n'
        )
        non_json = "- **Evidence:** not-json-at-all\n"
        for label, extra in (("indented", indented), ("non-json", non_json)):
            with self.subTest(label=label):
                report = evidence.inspect(NOTE_WITH_SUPPORT + extra, TODAY)
                self.assertTrue(any(f.level == "FAIL" for f in report.findings))
                self.assertEqual(report.support, 1)

    def test_secret_token_is_masked_without_exposure(self):
        token = "ghp_" + ("a" * 36)
        payload = (
            '{"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"probe","quote":"saw ' + token + '"}'
        )
        report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
        failures = [f for f in report.findings if f.level == "FAIL"]
        self.assertTrue(failures)
        joined = " | ".join(f.detail for f in report.findings)
        self.assertNotIn(token, joined)
        self.assertTrue(any("possible" in f.detail and "remove before commit" in f.detail
                            for f in failures))
        with self.assertRaises(ValueError) as ctx:
            evidence.append(
                NOTE_WITH_SUPPORT,
                {
                    "date": "2026-09-03",
                    "stance": "SUPPORT",
                    "source": "probe",
                    "quote": f"saw {token}",
                },
                TODAY,
            )
        self.assertNotIn(token, str(ctx.exception))

    def test_runpy_resolves_secretscan_staged_next_to_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            caller = root / "caller"
            stage.mkdir()
            caller.mkdir()
            source = Path(__file__).resolve().parent
            shutil.copy2(source / "evidence.py", stage / "evidence.py")
            shutil.copy2(source / "secretscan.py", stage / "secretscan.py")
            code = (
                "import runpy; "
                f"runpy.run_path({str(stage / 'evidence.py')!r}, run_name='evidence_probe')"
            )
            run = subprocess.run(
                [sys.executable, "-I", "-c", code], cwd=str(caller),
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
