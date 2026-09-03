#!/usr/bin/env python3
"""Regression tests for the MNEMOS secret write gate using the public audit API."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
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
VALID_NOTE = """# MEMORY.md — Core Memory (L2)

### [MEM-2026-0001] Verified local behavior

- **Type:** Gotcha · **Confidence:** VERIFIED · **Salience:** 0.80
- **Created:** 2026-08-24 · **Last-Access:** 2026-08-24 · **Freq:** 1
- **Tags:** #test #local
- **Links:**
- **Provenance:** Executed locally.
- **Observation:** The behavior was observed.
- **Directive:** Use the verified path.
"""
CLEAN_AGENTS = "# AGENTS.md\n\n- Never store API keys or private credentials.\n"


class SecretGateTests(unittest.TestCase):
    def run_case(self, memory_text: str = VALID_NOTE, agents_text: str = CLEAN_AGENTS):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            agents = root / "AGENTS.md"
            output = root / "INDEX.generated.md"
            memory.write_text(memory_text, encoding="utf-8")
            agents.write_text(agents_text, encoding="utf-8")
            findings, _, _ = mnemos.audit(memory, agents, output, None, TODAY)
            return findings

    @staticmethod
    def secret_failures(findings):
        return [finding for finding in findings
                if finding.level == "FAIL" and finding.detail.startswith("possible ")]

    def test_runpy_resolves_scanner_staged_next_to_mnemos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            caller = root / "caller"
            stage.mkdir()
            caller.mkdir()
            source = Path(__file__).resolve().parent
            shutil.copy2(source / "mnemos.py", stage / "mnemos.py")
            shutil.copy2(source / "secretscan.py", stage / "secretscan.py")
            code = (
                "import runpy; "
                f"runpy.run_path({str(stage / 'mnemos.py')!r}, run_name='mnemos_probe')"
            )

            run = subprocess.run(
                [sys.executable, "-I", "-c", code], cwd=caller,
                capture_output=True, text=True,
            )

            self.assertEqual(run.returncode, 0, run.stderr)

    def test_github_token_in_note_is_rejected_without_leaking_value(self):
        token = "ghp_" + "a" * 36
        memory = VALID_NOTE.replace("The behavior was observed.", f"Token: {token}")

        failures = self.secret_failures(self.run_case(memory))

        self.assertEqual(len(failures), 1, failures)
        self.assertEqual(failures[0].subject, "MEM-2026-0001")
        self.assertIn("GitHub token", failures[0].detail)
        self.assertNotIn(token, failures[0].detail)

    def test_secret_in_note_title_is_rejected_with_note_subject(self):
        token = "ghp_" + "t" * 36
        memory = VALID_NOTE.replace(
            "Verified local behavior", f"Verified {token}"
        )

        failures = self.secret_failures(self.run_case(memory))

        self.assertEqual(len(failures), 1, failures)
        self.assertEqual(failures[0].subject, "MEM-2026-0001")
        self.assertNotIn(token, failures[0].detail)

    def test_secrets_outside_notes_are_rejected_as_memory_findings(self):
        token = "ghp_" + "s" * 36
        cases = (
            ("preamble", VALID_NOTE.replace(
                "# MEMORY.md — Core Memory (L2)",
                f"# MEMORY.md — Core Memory (L2)\n\nCredential: {token}",
            )),
            ("scratchpad", VALID_NOTE +
             f"\n## 3. Ephemeral Scratchpad\n\nCredential: {token}\n"),
        )
        for location, memory in cases:
            with self.subTest(location=location):
                failures = self.secret_failures(self.run_case(memory))

                self.assertEqual(len(failures), 1, failures)
                self.assertEqual(failures[0].subject, "MEMORY.md")
                self.assertNotIn(token, failures[0].detail)


    def test_distinct_tokens_with_same_preview_remain_distinct(self):
        first = "ghp_" + "a" * 36
        second = "ghp_" + "a" * 35 + "b"
        memory = VALID_NOTE.replace(
            "The behavior was observed.", f"Tokens: {first} {first} {second}"
        )

        failures = self.secret_failures(self.run_case(memory))

        self.assertEqual(len(failures), 2, failures)

    def test_private_key_headers_in_agents_are_rejected(self):
        headers = (
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "-----BEGIN DSA PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "-----BEGIN ENCRYPTED PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        )
        for header in headers:
            with self.subTest(header=header):
                failures = self.secret_failures(
                    self.run_case(agents_text=CLEAN_AGENTS + header + "\n")
                )

                self.assertEqual(len(failures), 1, failures)
                self.assertEqual(failures[0].subject, "AGENTS.md")
                self.assertIn("private key", failures[0].detail)

    def test_jwt_in_note_is_rejected(self):
        token = "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + "." + "c" * 10
        memory = VALID_NOTE.replace("The behavior was observed.", f"Bearer {token}")

        failures = self.secret_failures(self.run_case(memory))

        self.assertEqual(len(failures), 1, failures)
        self.assertIn("JWT", failures[0].detail)
        self.assertNotIn(token, failures[0].detail)

    def test_supported_provider_credentials_are_rejected_and_masked(self):
        cases = (
            ("AWS access key", "AKIA" + "A" * 16),
            ("GitHub PAT", "github_pat_" + "A" * 22),
            ("GitLab token", "glpat-" + "A" * 20),
            ("Slack token", "xoxb-" + "1" * 12 + "-" + "A" * 24),
            ("Google API key", "AIza" + "A" * 35),
            ("Stripe secret key", "sk_live_" + "A" * 24),
            ("Stripe webhook secret", "whsec_" + "A" * 32),
            ("Anthropic key", "sk-ant-api03-" + "A" * 24),
            ("OpenAI key", "sk-proj-" + "A" * 24),
            ("OpenAI key", "sk-" + "A" * 32),
            ("HuggingFace token", "hf_" + "A" * 34),
            ("npm token", "npm_" + "A" * 36),
            ("PyPI token", "pypi-AgEIcHlwaS5vcmcBA" + "A" * 50),
            ("SendGrid key", "SG." + "A" * 22 + "." + "B" * 43),
            ("Twilio API key", "SK" + "a" * 32),
            ("Square token", "sq0atp-" + "A" * 22),
            ("Shopify token", "shpat_" + "a" * 32),
            ("Telegram bot token", "123456789:" + "A" * 35),
            ("DigitalOcean token", "dop_v1_" + "a" * 64),
            ("Databricks token", "dapi" + "a" * 32),
            ("New Relic key", "NRAK-" + "A" * 27),
            ("Pulumi token", "pul-" + "A" * 40),
            ("Sentry token", "sntrys_" + "A" * 32),
            ("Linear key", "lin_api_" + "A" * 40),
            ("Postman key", "PMAK-" + "a" * 24 + "-" + "b" * 34),
            ("Vault token", "hvs." + "A" * 24),
            ("age secret key", "AGE-SECRET-KEY-1" + "A" * 20),
            ("credentials in URL", "https://alice:secret@example.test/path"),
        )
        for kind, token in cases:
            with self.subTest(kind=kind):
                memory = VALID_NOTE.replace(
                    "The behavior was observed.", f"Credential: {token}"
                )

                failures = self.secret_failures(self.run_case(memory))

                self.assertEqual(len(failures), 1, failures)
                self.assertIn(kind, failures[0].detail)
                self.assertNotIn(token, failures[0].detail)

    def test_long_hyphenated_slug_is_not_an_openai_key(self):
        memory = VALID_NOTE.replace(
            "The behavior was observed.", "Host: sk-prod-eu-central-1-database"
        )

        failures = self.secret_failures(self.run_case(memory))

        self.assertEqual(failures, [])

    def test_clean_memory_and_agents_have_no_secret_findings(self):
        failures = self.secret_failures(self.run_case())

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
