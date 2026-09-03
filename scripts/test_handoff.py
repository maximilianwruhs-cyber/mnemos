#!/usr/bin/env python3
"""Regression tests for handoff.py.

The selftest inside handoff.py proves each rule fires once. These tests pin the
properties that would rot silently: that warnings never block, that the secret
scanner does not fire on ordinary prose, that listing survives a malformed file,
and that an accepted snapshot round-trips through parse without losing content.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path

MODULE_PATH = Path("/tmp/handoff.py")
_spec = importlib.util.spec_from_file_location("handoff", MODULE_PATH)
handoff = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(handoff)

TODAY = date(2026, 1, 2)


class HandoffTests(unittest.TestCase):
    def fails(self, text, today=TODAY):
        return {c for lvl, c, _ in handoff.validate(text, today) if lvl == "FAIL"}

    def warns(self, text, today=TODAY):
        return {c for lvl, c, _ in handoff.validate(text, today) if lvl == "WARN"}

    def test_selftest_passes(self):
        self.assertEqual(handoff.selftest(), 0)

    def test_reference_snapshot_is_clean(self):
        self.assertEqual(self.fails(handoff.GOOD), set())

    def test_warnings_do_not_block(self):
        draft = handoff.GOOD.replace("status: REVIEWED", "status: DRAFT")
        self.assertIn("W001", self.warns(draft))
        self.assertEqual(self.fails(draft), set())

    def test_expired_snapshot_warns_but_is_still_readable(self):
        findings = handoff.validate(handoff.GOOD, date(2027, 1, 1))
        self.assertTrue(any(c == "W002" for _, c, _ in findings))
        self.assertFalse(any(lvl == "FAIL" for lvl, _, _ in findings))

    def test_secret_scanner_ignores_ordinary_prose(self):
        prose = ("The password policy changed. We use a token bucket. "
                 "Secret Santa is unrelated. Cookie banners are annoying.")
        self.assertEqual(handoff.scan_secrets(prose), [])

    def test_secret_scanner_catches_real_shapes(self):
        self.assertIn("private key block", handoff.scan_secrets(
            "-----BEGIN RSA PRIVATE KEY-----"))
        self.assertIn("aws access key id", handoff.scan_secrets("AKIAIOSFODNN7EXAMPLE"))
        self.assertIn("bearer token", handoff.scan_secrets(
            "Authorization: Bearer abcdefghijklmnop.qrstuvwx"))

    def test_none_placeholder_satisfies_evidence_gate(self):
        text = handoff.GOOD.replace(
            "- Parser handles frontmatter. **Evidence:** selftest assertion in handoff.py.",
            "(none)")
        self.assertNotIn("F011", self.fails(text))

    def test_multiple_verified_bullets_each_need_evidence(self):
        text = handoff.GOOD.replace(
            "- Parser handles frontmatter. **Evidence:** selftest assertion in handoff.py.",
            "- A. **Evidence:** proof.\n- B is asserted with nothing behind it.")
        self.assertIn("F011", self.fails(text))

    def test_parse_preserves_section_bodies(self):
        meta, sections, order, has_fm = handoff.parse(handoff.GOOD)
        self.assertTrue(has_fm)
        self.assertEqual(meta["schema"], "handoff/1")
        self.assertEqual(order[:2], ["Objective", "Verified"])
        self.assertIn("greppable", sections["Decisions"])

    def test_is_expired_boundary_is_inclusive_of_expiry_day(self):
        meta = {"expires": "2026-01-08"}
        self.assertFalse(handoff.is_expired(meta, date(2026, 1, 8)))
        self.assertTrue(handoff.is_expired(meta, date(2026, 1, 9)))

    def test_list_survives_a_malformed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.md").write_text(handoff.GOOD, encoding="utf-8")
            (root / "junk.md").write_text("not a handoff at all", encoding="utf-8")
            out = handoff.render_list(root, TODAY)
            self.assertIn("good.md", out)
            self.assertIn("junk.md", out)
            self.assertIn("2 snapshot(s)", out)

    def test_cli_validate_returns_nonzero_on_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.md"
            path.write_text(handoff.GOOD.replace("## Resume Guard", "## Notes"),
                            encoding="utf-8")
            self.assertEqual(handoff.main(["validate", str(path)]), 1)

    def test_cli_validate_returns_zero_on_accept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ok.md"
            path.write_text(handoff.GOOD, encoding="utf-8")
            self.assertEqual(handoff.main(["validate", str(path)]), 0)

    def test_empty_evidence_is_rejected(self):
        text = handoff.GOOD.replace(
            "**Evidence:** selftest assertion in handoff.py.", "**Evidence:**")
        self.assertIn("F011", self.fails(text))

    def test_non_bullet_verified_prose_is_rejected(self):
        text = handoff.GOOD.replace(
            "- Parser handles frontmatter. **Evidence:** selftest assertion in handoff.py.",
            "Parser handles frontmatter and is verified.")
        self.assertIn("F015", self.fails(text))

    def test_verified_continuation_line_is_rejected(self):
        text = handoff.GOOD.replace(
            "- Parser handles frontmatter. **Evidence:** selftest assertion in handoff.py.",
            "- Parser handles frontmatter. **Evidence:** selftest assertion.\n  Additional unsupported claim.")
        self.assertIn("F015", self.fails(text))

    def test_empty_rationale_is_rejected(self):
        text = handoff.GOOD.replace("**Why:** greppable and diffable in the store.", "**Why:**")
        self.assertIn("F012", self.fails(text))

    def test_next_action_requires_do_needs_expect(self):
        text = handoff.GOOD.replace("- **Do:** Run the unit suite.", "- TBD")
        self.assertIn("F006", self.fails(text))

    def test_resume_guard_requires_canonical_rules(self):
        text = handoff.GOOD.replace("- Verify every referenced path still exists before acting.", "- TBD")
        self.assertIn("F007", self.fails(text))

    def test_duplicate_frontmatter_key_is_rejected(self):
        text = handoff.GOOD.replace("status: REVIEWED", "status: DRAFT\nstatus: REVIEWED")
        self.assertIn("F016", self.fails(text))

    def test_unknown_section_is_rejected(self):
        text = handoff.GOOD.replace("## Unverified", "## Surprise\n- hidden\n\n## Unverified")
        self.assertIn("F017", self.fails(text))

    def test_additional_secret_shapes_are_rejected(self):
        secrets = [
            "github_pat_" + "A" * 30,
            "?sv=2024-01-01&sig=" + "A" * 44 + "&se=2099-01-01",
            "password: abc123",
        ]
        for secret in secrets:
            with self.subTest(secret=secret[:20]):
                self.assertTrue(handoff.scan_secrets(secret))


if __name__ == "__main__":
    unittest.main(verbosity=2)
