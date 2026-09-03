#!/usr/bin/env python3
"""Schema and append regression tests for the pure evidence ledger."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
import json
from unittest.mock import patch



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
memory_note = load_module("memory_note.py")
mnemos = load_module("mnemos.py")
snapshot = load_module("snapshot.py")
graphcheck = load_module("graphcheck.py")
secretscan = load_module("secretscan.py")
evidence_migrate = load_module("evidence_migrate.py")

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
NID = "MEM-2026-0001"
OTHER_ID = "MEM-2026-0002"
EMPTY_MEMORY = (
    "# MEMORY.md\n\n"
    "## 2. Atomic Notes\n\n"
    "## 3. Ephemeral Scratchpad\n"
)
FIELD_BLOCK = (
    "- **Type:** Gotcha · **Confidence:** VERIFIED · **Salience:** 0.80\n"
    "- **Created:** 2026-09-03 · **Last-Access:** 2026-09-03 · **Freq:** 1\n"
    "- **Tags:** #test #local\n"
    "- **Links:**\n"
    "- **Provenance:** Executed locally.\n"
    "- **Observation:** The behavior was observed.\n"
    "- **Directive:** Use the verified path.\n"
)
VALID_BODY = FIELD_BLOCK + NOTE_WITH_SUPPORT
BODY_WITHOUT_EVIDENCE = FIELD_BLOCK
OTHER_BODY = (
    "- **Type:** Gotcha · **Confidence:** HIGH · **Salience:** 0.70\n"
    "- **Created:** 2026-09-03 · **Last-Access:** 2026-09-03 · **Freq:** 1\n"
    "- **Tags:** #other\n"
    "- **Links:**\n"
    "- **Provenance:** Other probe.\n"
    "- **Observation:** Other claim.\n"
    "- **Directive:** Keep other path.\n"
    '- **Evidence:** {"date":"2026-09-02","stance":"SUPPORT",'
    '"source":"other probe","quote":"PASS"}\n'
)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _full_l2_note(nid: str, title: str, body: str) -> str:
    return f"### [{nid}] {title}\n\n{body.rstrip()}\n\n"


def _two_note_memory() -> str:
    return (
        "# MEMORY.md\n\n"
        "## 2. Atomic Notes\n\n"
        + _full_l2_note(NID, "Title", VALID_BODY)
        + _full_l2_note(OTHER_ID, "Other", OTHER_BODY)
        + "## 3. Ephemeral Scratchpad\n"
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

    def test_date_and_stance_secrets_are_not_echoed_in_findings(self):
        token = "ghp_" + ("b" * 36)
        cases = {
            "date": (
                '{"date":"' + token + '","stance":"SUPPORT",'
                '"source":"probe","quote":"ok"}'
            ),
            "stance": (
                '{"date":"2026-09-03","stance":"' + token + '",'
                '"source":"probe","quote":"ok"}'
            ),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
                joined = " | ".join(f.detail for f in report.findings)
                self.assertTrue(any(f.level == "FAIL" for f in report.findings))
                self.assertNotIn(token, joined)
                bad = {
                    "date": "2026-09-03",
                    "stance": "SUPPORT",
                    "source": "probe",
                    "quote": "ok",
                }
                bad[label] = token
                with self.assertRaises(ValueError) as ctx:
                    evidence.append(NOTE_WITH_SUPPORT, bad, TODAY)
                self.assertNotIn(token, str(ctx.exception))

    def test_append_rejects_unknown_keys_and_nonstring_values(self):
        original = NOTE_WITH_SUPPORT
        unknown = dict(SECOND_SUPPORT)
        unknown["extra"] = "z"
        with self.assertRaises(ValueError):
            evidence.append(original, unknown, TODAY)
        self.assertEqual(original, NOTE_WITH_SUPPORT)

        nonstring = dict(SECOND_SUPPORT)
        nonstring["quote"] = ["not", "a", "string"]
        with self.assertRaises(ValueError):
            evidence.append(original, nonstring, TODAY)
        self.assertEqual(original, NOTE_WITH_SUPPORT)

    def test_valid_challenge_emits_warn_even_with_coexisting_fail(self):
        malformed = "- **Evidence:** {bad json}\n"
        report = evidence.inspect(
            NOTE_WITH_SUPPORT + CHALLENGE_LINE + malformed, TODAY)
        levels = [f.level for f in report.findings]
        self.assertIn("FAIL", levels)
        self.assertIn("WARN", levels)
        self.assertTrue(report.contested)
        self.assertEqual((report.support, report.challenge), (1, 1))

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

class NoteMutationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(memory_note, "memory_note.py is not staged")
        self.assertIsNotNone(mnemos, "mnemos.py is not staged")
        self.assertIsNotNone(evidence, "evidence.py is not staged")

    def test_create_rejects_missing_evidence_without_writes(self):
        self.assertIn("today", inspect.signature(memory_note.create).parameters)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            before = memory.read_bytes()
            with self.assertRaisesRegex(ValueError, "Evidence"):
                memory_note.create(memory, root, NID, "Title", "lessons",
                                   BODY_WITHOUT_EVIDENCE, TODAY)
            self.assertEqual(memory.read_bytes(), before)
            self.assertEqual(list((root / "Memory").rglob("*.md")) if
                             (root / "Memory").exists() else [], [])

    def test_create_writes_valid_full_note_and_complete_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            rel = memory_note.create(memory, root, NID, "Title", "lessons",
                                     VALID_BODY, TODAY)
            full = (root / rel).read_text(encoding="utf-8")
            stub = memory.read_text(encoding="utf-8")
            self.assertEqual(evidence.inspect(full, TODAY).support, 1)
            self.assertIn("VERIFIED", stub)
            self.assertIn("Use the verified path.", stub)
            self.assertIn(rel, stub)
            self.assertTrue(full.startswith(f"# {NID} — Title\n"))

    def test_append_evidence_to_l3_is_atomic_and_preserves_fields(self):
        self.assertTrue(hasattr(memory_note, "append_evidence"),
                        "append_evidence is not implemented")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            rel = memory_note.create(memory, root, NID, "Title", "lessons",
                                     VALID_BODY, TODAY)
            note_path = root / rel
            relative = note_path.relative_to(root).as_posix()
            before = note_path.read_bytes()
            memory_note.append_evidence(root, relative, NID, SECOND_SUPPORT, TODAY)
            after = note_path.read_bytes()
            self.assertTrue(after.startswith(before))
            before_fields = dict(mnemos.FIELD_RE.findall(before.decode("utf-8")))
            after_fields = dict(mnemos.FIELD_RE.findall(after.decode("utf-8")))
            for name in ("Freq", "Last-Access", "Confidence", "Salience",
                         "Observation", "Directive"):
                self.assertEqual(after_fields[name], before_fields[name])
            self.assertEqual(evidence.inspect(after.decode(), TODAY).support, 2)

    def test_append_evidence_splices_only_the_named_l2_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(_two_note_memory(), encoding="utf-8")
            before = memory.read_text(encoding="utf-8")
            memory_note.append_evidence(root, "MEMORY.md", NID,
                                        SECOND_SUPPORT, TODAY)
            after = memory.read_text(encoding="utf-8")
            notes = {note["id"]: note for note in mnemos.parse_notes(after)}
            old_notes = {note["id"]: note for note in mnemos.parse_notes(before)}
            self.assertEqual(evidence.inspect(notes[NID]["body"], TODAY).support, 2)
            self.assertEqual(notes[OTHER_ID]["body"], old_notes[OTHER_ID]["body"])

    def test_append_rejects_duplicate_and_secret_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            rel = memory_note.create(memory, root, NID, "Title", "lessons",
                                     VALID_BODY, TODAY)
            note_path = root / rel
            before_note = note_path.read_bytes()
            before_memory = memory.read_bytes()
            dup = {
                "date": "2026-09-02",
                "stance": "SUPPORT",
                "source": "first probe",
                "quote": "PASS",
            }
            with self.assertRaises(ValueError):
                memory_note.append_evidence(
                    root, rel, NID, dup, TODAY)
            token = "ghp_" + ("c" * 36)
            secret_item = {
                "date": "2026-09-03",
                "stance": "SUPPORT",
                "source": "probe",
                "quote": f"saw {token}",
            }
            with self.assertRaises(ValueError) as ctx:
                memory_note.append_evidence(
                    root, rel, NID, secret_item, TODAY)
            self.assertNotIn(token, str(ctx.exception))
            self.assertEqual(note_path.read_bytes(), before_note)
            self.assertEqual(memory.read_bytes(), before_memory)

    def test_append_refuses_escape_absolute_archive_stub_missing_and_id_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(_two_note_memory(), encoding="utf-8")
            rel = memory_note.create(
                memory, root, "MEM-2026-0003", "Fresh", "lessons", VALID_BODY, TODAY)
            note_path = root / rel
            archive = root / "Memory" / "_archive" / "old.md"
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text(
                f"# {NID} — Archived\n\n{VALID_BODY}", encoding="utf-8")
            before = _tree_bytes(root)

            cases = [
                ("escape", "../outside.md", NID),
                ("absolute", str(note_path.resolve()), NID),
                ("archive", "Memory/_archive/old.md", NID),
                ("missing", "MEMORY.md", "MEM-2026-9999"),
                ("stub", "MEMORY.md", "MEM-2026-0003"),
                ("l3-mismatch", rel, OTHER_ID),
            ]
            for label, relative, nid in cases:
                with self.subTest(label=label):
                    with self.assertRaises(ValueError):
                        memory_note.append_evidence(
                            root, relative, nid, SECOND_SUPPORT, TODAY)
                    self.assertEqual(_tree_bytes(root), before)

    def test_create_and_l2_append_refuse_memory_cap_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            # Just over the line cap so a stub insertion pushes past 200 lines.
            padding = "\n".join(f"- pad {i}" for i in range(191))
            near_cap = (
                "# MEMORY.md\n\n"
                "## 2. Atomic Notes\n\n"
                f"{padding}\n\n"
                "## 3. Ephemeral Scratchpad\n"
            )
            memory.write_text(near_cap, encoding="utf-8")
            before = memory.read_bytes()
            with self.assertRaisesRegex(ValueError, "cap"):
                memory_note.create(memory, root, NID, "Title", "lessons",
                                   VALID_BODY, TODAY)
            self.assertEqual(memory.read_bytes(), before)
            self.assertEqual(list((root / "Memory").rglob("*.md")) if
                             (root / "Memory").exists() else [], [])

            # Byte cap: oversized valid L2 corpus that already fits, append pushes over.
            body = VALID_BODY
            note = _full_l2_note(NID, "Title", body)
            filler = "x" * 11700
            mem_text = (
                "# MEMORY.md\n\n"
                "## 2. Atomic Notes\n\n"
                f"{note}"
                f"<!-- {filler} -->\n\n"
                "## 3. Ephemeral Scratchpad\n"
            )
            # Ensure baseline is under byte cap.
            self.assertLessEqual(len(mem_text.encode("utf-8")), 12288)
            memory.write_text(mem_text, encoding="utf-8")
            before = memory.read_bytes()
            # Large evidence quote still valid alone but overflows MEMORY bytes.
            big_item = {
                "date": "2026-09-03",
                "stance": "SUPPORT",
                "source": "bulk",
                "quote": "Q" * 280,
            }
            projected = evidence.append(
                mnemos.parse_notes(mem_text)[0]["body"], big_item, TODAY)
            projected_memory = (
                mem_text[:mnemos.parse_notes(mem_text)[0]["body_start"]]
                + projected
                + mem_text[mnemos.parse_notes(mem_text)[0]["scan_end"]:]
            )
            self.assertGreater(len(projected_memory.encode("utf-8")), 12288)
            with self.assertRaisesRegex(ValueError, "cap"):
                memory_note.append_evidence(
                    root, "MEMORY.md", NID, big_item, TODAY)
            self.assertEqual(memory.read_bytes(), before)

    def test_append_replace_failure_leaves_bytes_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            rel = memory_note.create(memory, root, NID, "Title", "lessons",
                                     VALID_BODY, TODAY)
            before = _tree_bytes(root)

            def boom(source, target):
                raise OSError("forced replace failure")

            with patch.object(memory_note.os, "replace", side_effect=boom):
                with self.assertRaises(OSError):
                    memory_note.append_evidence(
                        root, rel, NID, SECOND_SUPPORT, TODAY)
            self.assertEqual(_tree_bytes(root), before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(_two_note_memory(), encoding="utf-8")
            before = _tree_bytes(root)

            def boom(source, target):
                raise OSError("forced replace failure")

            with patch.object(memory_note.os, "replace", side_effect=boom):
                with self.assertRaises(OSError):
                    memory_note.append_evidence(
                        root, "MEMORY.md", NID, SECOND_SUPPORT, TODAY)
            self.assertEqual(_tree_bytes(root), before)


    def test_create_second_replace_failure_rolls_back_l3_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            original_memory = memory.read_bytes()
            expected_rel = "Memory/lessons/MEM-2026-0001-title.md"
            expected_l3 = root / expected_rel

            real_replace = memory_note.os.replace
            calls = 0

            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("forced second replace failure")
                return real_replace(source, target)

            with patch.object(memory_note.os, "replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    memory_note.create(memory, root, NID, "Title", "lessons",
                                       VALID_BODY, TODAY)
            self.assertEqual(memory.read_bytes(), original_memory)
            self.assertFalse(expected_l3.exists())
            # No leftover temps with note content committed.
            self.assertEqual(
                [p for p in root.rglob("*.md") if p.name != "MEMORY.md"], [])

    def test_create_second_replace_failure_never_rewrites_untouched_memory(self):
        # os.replace is atomic, so a failed MEMORY replace never touched MEMORY;
        # the rollback must not rewrite it (a truncating write that then fails
        # would corrupt the one file this module exists to protect).
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            original_memory = memory.read_bytes()

            real_replace = memory_note.os.replace
            calls = 0

            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("forced second replace failure")
                return real_replace(source, target)

            def poisoned_write_bytes(self, data):
                raise AssertionError("untouched MEMORY must not be rewritten")

            with patch.object(memory_note.os, "replace", side_effect=fail_second):
                with patch.object(memory_note.Path, "write_bytes",
                                  poisoned_write_bytes):
                    with self.assertRaises(OSError) as ctx:
                        memory_note.create(memory, root, NID, "Title",
                                           "lessons", VALID_BODY, TODAY)
            self.assertIn("forced second replace failure", str(ctx.exception))
            self.assertEqual(memory.read_bytes(), original_memory)

    def test_runpy_resolves_memory_note_siblings_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            caller = root / "caller"
            stage.mkdir()
            caller.mkdir()
            source = Path(__file__).resolve().parent
            for name in ("memory_note.py", "mnemos.py", "evidence.py",
                         "secretscan.py", "snapshot.py"):
                shutil.copy2(source / name, stage / name)
            code = (
                "import runpy; "
                f"runpy.run_path({str(stage / 'memory_note.py')!r}, "
                "run_name='memory_note_probe')"
            )
            run = subprocess.run(
                [sys.executable, "-I", "-c", code], cwd=str(caller),
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_cli_create_and_append_evidence_smoke(self):
        script = Path(__file__).resolve().parent / "memory_note.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "MEMORY.md"
            memory.write_text(EMPTY_MEMORY, encoding="utf-8")
            body_file = root / "body.md"
            body_file.write_text(VALID_BODY, encoding="utf-8")
            create_run = subprocess.run(
                [
                    sys.executable, str(script), "create",
                    "--memory", str(memory),
                    "--root", str(root),
                    "--id", NID,
                    "--title", "Title",
                    "--category", "lessons",
                    "--body-file", str(body_file),
                    "--today", TODAY.isoformat(),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(create_run.returncode, 0, create_run.stderr)
            rel = create_run.stdout.strip()
            self.assertTrue((root / rel).is_file())

            append_run = subprocess.run(
                [
                    sys.executable, str(script), "append-evidence",
                    "--root", str(root),
                    "--path", rel,
                    "--id", NID,
                    "--evidence-json", json.dumps(SECOND_SUPPORT),
                    "--today", TODAY.isoformat(),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(append_run.returncode, 0, append_run.stderr)
            self.assertEqual(
                evidence.inspect((root / rel).read_text(encoding="utf-8"), TODAY).support,
                2,
            )

            bad_body = root / "bad.md"
            bad_body.write_text(BODY_WITHOUT_EVIDENCE, encoding="utf-8")
            before = memory.read_bytes()
            bad_create = subprocess.run(
                [
                    sys.executable, str(script), "create",
                    "--memory", str(memory),
                    "--root", str(root),
                    "--id", "MEM-2026-0099",
                    "--title", "Bad",
                    "--category", "lessons",
                    "--body-file", str(bad_body),
                    "--today", TODAY.isoformat(),
                ],
                capture_output=True, text=True,
            )
            self.assertNotEqual(bad_create.returncode, 0)
            self.assertEqual(memory.read_bytes(), before)


DISTIL_FIELD_BLOCK = (
    "- **Type:** Gotcha · **Confidence:** VERIFIED · **Salience:** 0.30\n"
    "- **Created:** 2026-01-01 · **Last-Access:** 2026-09-03 · **Freq:** 0\n"
    "- **Tags:** #test #local\n"
    "- **Links:** [[MEM-2026-0002]]\n"
    "- **Provenance:** Executed locally.\n"
    "- **Observation:** The behavior was observed.\n"
    "- **Directive:** Use the verified path.\n"
)
DISTIL_EVIDENCE = (
    NOTE_WITH_SUPPORT
    + '- **Evidence:** {"date":"2026-09-01","stance":"SUPPORT",'
    '"source":"second probe","quote":"PASS2"}\n'
)
DISTIL_BODY = DISTIL_FIELD_BLOCK + DISTIL_EVIDENCE


class DistillTests(unittest.TestCase):
    def setUp(self):
        if None in (memory_note, mnemos, snapshot, evidence):
            self.skipTest("modules not staged")
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.memory = self.root / "MEMORY.md"
        text = (
            "# MEMORY.md\n\n"
            "## 2. Atomic Notes\n\n"
            + _full_l2_note(NID, "Title", DISTIL_BODY)
            + _full_l2_note(OTHER_ID, "Other", OTHER_BODY)
            + "## 3. Ephemeral Scratchpad\n"
        )
        self.memory.write_text(text, encoding="utf-8")
        self.store = self.root / "autonomy" / "snapshots"
        self.rel = memory_note._l3_rel(NID, "Title", "lessons")
        self.manifest = snapshot.create(
            self.store, self.root, ["MEMORY.md", self.rel], "before-distill")
        # Confirm the fixture genuinely scores DISTIL.
        rows = {r["id"]: r for r in mnemos.score_notes(
            mnemos.parse_notes(text), TODAY)}
        self.assertEqual(rows[NID]["action"], "DISTIL")

    def tearDown(self):
        self.dir.cleanup()

    def _distill(self, **over):
        args = dict(
            memory=self.memory, root=self.root, snapshot_store=self.store,
            snapshot_id=self.manifest["id"], nid=NID, category="lessons",
            observation="Compressed semantic claim.", today=TODAY)
        args.update(over)
        return memory_note.distill(
            args["memory"], args["root"], args["snapshot_store"],
            args["snapshot_id"], args["nid"], args["category"],
            args["observation"], args["today"])

    def test_distill_preserves_identity_directive_links_and_evidence(self):
        rel = self._distill()
        self.assertEqual(rel, self.rel)
        l3 = (self.root / rel).read_text(encoding="utf-8")
        self.assertIn(f"# {NID} —", l3)
        self.assertIn("Compressed semantic claim.", l3)
        self.assertNotIn("The behavior was observed.", l3)
        self.assertIn("Use the verified path.", l3)
        self.assertIn(f"[[{OTHER_ID}]]", l3)
        self.assertEqual(evidence.inspect(l3, TODAY).support, 2)
        l2 = self.memory.read_text(encoding="utf-8")
        notes = {n["id"]: n for n in mnemos.parse_notes(l2)}
        self.assertTrue(notes[NID]["stub"])
        self.assertIn("Use the verified path.", l2)
        self.assertIn(rel, l2)
        # Untouched sibling note is byte-for-byte identical.
        self.assertIn(_full_l2_note(OTHER_ID, "Other", OTHER_BODY), l2)

    def test_distill_refuses_non_distil_action(self):
        text = (
            "# MEMORY.md\n\n## 2. Atomic Notes\n\n"
            + _full_l2_note(NID, "Title", VALID_BODY)
            + _full_l2_note(OTHER_ID, "Other", OTHER_BODY)
            + "## 3. Ephemeral Scratchpad\n"
        )
        self.memory.write_text(text, encoding="utf-8")
        store = self.root / "keep-store"
        manifest = snapshot.create(store, self.root, ["MEMORY.md", self.rel], "keep")
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill(snapshot_store=store, snapshot_id=manifest["id"])
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_missing_note(self):
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill(nid="MEM-2026-0404")
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_existing_target(self):
        target = self.root / self.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# occupied\n", encoding="utf-8")
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill()
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_rejects_multiline_observation(self):
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill(observation="line one\nline two")
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_rejects_empty_observation(self):
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill(observation="   ")
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_absent_snapshot(self):
        before = _tree_bytes(self.root)
        with self.assertRaises(FileNotFoundError):
            self._distill(snapshot_id="snap-does-not-exist")
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_stale_snapshot(self):
        # Mutate MEMORY after snapshotting so its digest no longer matches.
        self.memory.write_text(
            self.memory.read_text(encoding="utf-8") + "\n<!-- drift -->\n",
            encoding="utf-8")
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill()
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_unreserved_target_path(self):
        # Snapshot that captured MEMORY but never reserved the L3 path.
        store = self.root / "partial-store"
        manifest = snapshot.create(store, self.root, ["MEMORY.md"], "partial")
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill(snapshot_store=store, snapshot_id=manifest["id"])
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_refuses_corrupt_snapshot(self):
        digest = self.manifest["files"]["MEMORY.md"]
        obj = self.store / "objects" / digest[:2] / digest
        obj.write_bytes(b"corrupted")
        before = _tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self._distill()
        self.assertEqual(_tree_bytes(self.root), before)

    def test_distill_cli_smoke(self):
        script = Path(__file__).resolve().parent / "memory_note.py"
        run = subprocess.run(
            [
                sys.executable, str(script), "distill",
                "--memory", str(self.memory),
                "--root", str(self.root),
                "--snapshot-store", str(self.store),
                "--snapshot-id", self.manifest["id"],
                "--id", NID,
                "--category", "lessons",
                "--observation", "Compressed semantic claim.",
                "--today", TODAY.isoformat(),
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), self.rel)
        self.assertEqual(
            evidence.inspect((self.root / self.rel).read_text(encoding="utf-8"),
                             TODAY).support, 2)

GC_REL = "/Memory/lessons/MEM-2026-0001-title.md"
GC_MANIFEST = {"MEMORY.md": "/MEMORY.md", "l3.md": GC_REL}
GC_SUPPORT = ('- **Evidence:** {"date":"2026-09-02","stance":"SUPPORT",'
              '"source":"probe","quote":"PASS"}\n')
GC_CHALLENGE = ('- **Evidence:** {"date":"2026-09-03","stance":"CHALLENGE",'
                '"source":"counter","quote":"FAIL"}\n')


def _gc_stub_memory(rel="Memory/lessons/MEM-2026-0001-title.md"):
    return (
        "# MEMORY.md\n\n## 2. Atomic Notes\n\n"
        f"### [{NID}] Title\n\n"
        f"- **Stub.** VERIFIED. Use the verified path. Full note: `{rel}`\n\n"
        "## 3. Ephemeral Scratchpad\n"
    )


def _gc_full_note(evidence_lines, nid=NID):
    return (
        f"# {nid} — Title\n\n"
        "- **Directive:** Use the verified path.\n"
        f"{evidence_lines}"
    )


class GraphCheckTests(unittest.TestCase):
    def setUp(self):
        if graphcheck is None or evidence is None:
            self.skipTest("modules not staged")
        self.dir = tempfile.TemporaryDirectory()
        self.stage = Path(self.dir.name)
        self._saved = (graphcheck.STAGE, graphcheck.MANIFEST)
        graphcheck.STAGE = self.stage
        graphcheck.MANIFEST = self.stage / "_manifest.json"
        os.environ["MNEMOS_TODAY"] = TODAY.isoformat()

    def tearDown(self):
        graphcheck.STAGE, graphcheck.MANIFEST = self._saved
        os.environ.pop("MNEMOS_TODAY", None)
        self.dir.cleanup()

    def _run(self, files, manifest):
        for name, content in files.items():
            (self.stage / name).write_text(content, encoding="utf-8")
        (self.stage / "_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = graphcheck.main()
        index = self.stage / "INDEX-L3.out.md"
        text = index.read_text(encoding="utf-8") if index.exists() else ""
        return code, buf.getvalue(), text

    def test_active_full_note_without_evidence_fails(self):
        code, out, _ = self._run(
            {"MEMORY.md": _gc_stub_memory(), "l3.md": _gc_full_note("")},
            GC_MANIFEST)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", out)
        self.assertIn(GC_REL, out)
        self.assertIn("Evidence records are required", out)

    def test_valid_support_note_is_supported(self):
        code, out, index = self._run(
            {"MEMORY.md": _gc_stub_memory(),
             "l3.md": _gc_full_note(GC_SUPPORT)},
            GC_MANIFEST)
        self.assertEqual(code, 0)
        self.assertNotIn("[FAIL]", out)
        self.assertIn("| 1/0 | supported |", index)

    def test_contested_note_warns(self):
        code, out, index = self._run(
            {"MEMORY.md": _gc_stub_memory(),
             "l3.md": _gc_full_note(GC_SUPPORT + GC_CHALLENGE)},
            GC_MANIFEST)
        self.assertEqual(code, 1)
        self.assertIn("[WARN]", out)
        self.assertIn("contested", out)
        self.assertIn("| 1/1 | contested |", index)

    def test_daily_document_with_stub_is_exempt(self):
        daily = ("# 2026-09-03 Daily Digest\n\n"
                 "- **Stub.** VERIFIED. Pointer. Full note: `Memory/x.md`\n")
        manifest = dict(GC_MANIFEST)
        manifest["daily.md"] = "/Memory/daily/2026-09-03.md"
        code, out, index = self._run(
            {"MEMORY.md": _gc_stub_memory(),
             "l3.md": _gc_full_note(GC_SUPPORT),
             "daily.md": daily},
            manifest)
        self.assertEqual(code, 0)
        self.assertNotIn("/Memory/daily/2026-09-03.md   ", out.replace("[OK  ]", ""))
        self.assertIn("| `/Memory/daily/2026-09-03.md` | daily |", index)
        self.assertIn("| - | - |", index)

    def test_l3_full_note_id_oracle(self):
        self.assertEqual(
            graphcheck.l3_full_note_id("/Memory/lessons/x.md",
                                       _gc_full_note(GC_SUPPORT)), NID)
        self.assertIsNone(graphcheck.l3_full_note_id(
            "/Memory/_archive/x.md", _gc_full_note(GC_SUPPORT)))
        self.assertIsNone(graphcheck.l3_full_note_id(
            "/Memory/lessons/x.md",
            "- **Stub.** VERIFIED. Full note: `Memory/y.md`\n"))
        self.assertIsNone(graphcheck.l3_full_note_id(
            "/Memory/daily/2026-09-03.md", "# 2026-09-03 Daily\n\ntext\n"))
        self.assertIsNone(graphcheck.l3_full_note_id(
            "/Memory/lessons/MEM-2026-0001-x.md", "# Some Title\n\nbody\n"))

    def test_dangling_stub_target_remains_fail(self):
        code, out, _ = self._run(
            {"MEMORY.md": _gc_stub_memory("Memory/lessons/MEM-2026-0001-gone.md")},
            {"MEMORY.md": "/MEMORY.md"})
        self.assertEqual(code, 1)
        self.assertIn("DANGLING stub target", out)

    def test_unresolved_l3_link_remains_fail(self):
        code, out, _ = self._run(
            {"MEMORY.md": _gc_stub_memory(),
             "l3.md": _gc_full_note(GC_SUPPORT) + "See [[MEM-2026-0404]]\n"},
            GC_MANIFEST)
        self.assertEqual(code, 1)
        self.assertIn("unresolved link MEM-2026-0404", out)

    def test_unreferenced_active_l3_is_orphan(self):
        orphan = _gc_full_note(GC_SUPPORT, nid="MEM-2026-0002")
        manifest = {"MEMORY.md": "/MEMORY.md",
                    "orphan.md": "/Memory/lessons/MEM-2026-0002-loose.md"}
        empty = "# MEMORY.md\n\n## 2. Atomic Notes\n\n## 3. Ephemeral Scratchpad\n"
        code, out, _ = self._run(
            {"MEMORY.md": empty, "orphan.md": orphan}, manifest)
        self.assertEqual(code, 1)
        self.assertIn("/Memory/lessons/MEM-2026-0002-loose.md", out)
        self.assertIn("ORPHAN L3 FILES", out)

    def test_valid_bidirectional_pair_is_clean(self):
        code, out, index = self._run(
            {"MEMORY.md": _gc_stub_memory(),
             "l3.md": _gc_full_note(GC_SUPPORT)},
            GC_MANIFEST)
        self.assertEqual(code, 0)
        self.assertIn("VERDICT: PASS", out)
        self.assertIn("supported", index)

    def test_isolated_import_from_unrelated_cwd(self):
        iso = Path(self.dir.name) / "iso"
        iso.mkdir()
        for name in ("graphcheck.py", "evidence.py", "secretscan.py"):
            (iso / name).write_bytes((Path("/tmp") / name).read_bytes())
        (iso / "MEMORY.md").write_text(_gc_stub_memory(), encoding="utf-8")
        (iso / "l3.md").write_text(_gc_full_note(GC_SUPPORT), encoding="utf-8")
        (iso / "_manifest.json").write_text(
            json.dumps(GC_MANIFEST), encoding="utf-8")
        elsewhere = Path(self.dir.name) / "elsewhere"
        elsewhere.mkdir()
        code = (
            "import importlib.util, io, contextlib\n"
            "from pathlib import Path\n"
            f"iso = Path(r'{iso}')\n"
            "spec = importlib.util.spec_from_file_location('graphcheck', iso / 'graphcheck.py')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "m.STAGE = iso; m.MANIFEST = iso / '_manifest.json'\n"
            "buf = io.StringIO()\n"
            "with contextlib.redirect_stdout(buf): rc = m.main()\n"
            "print('RC', rc)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = ""
        env["MNEMOS_TODAY"] = TODAY.isoformat()
        run = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(elsewhere), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("RC 0", run.stdout)
        self.assertNotIn("ModuleNotFoundError", run.stderr)

MIG_FIELDS = ("- **Type:** Gotcha \u00b7 **Confidence:** HIGH\n"
              "- **Links:**{links}\n- **Provenance:** local\n"
              "- **Observation:** obs\n- **Directive:** dir\n")
MIG_EV = {"date": "2026-09-02", "stance": "SUPPORT",
          "source": "reopened source", "quote": "PASS"}


def _mig_note(nid, title="Note", body="", links=""):
    head = f"### [{nid}] {title}\n"
    return head + MIG_FIELDS.format(links=links) + body


def _mig_memory(*notes):
    return ("# MEMORY.md\n\n## 2. Atomic Notes\n\n" +
            "".join(notes) + "\n## 3. Ephemeral Scratchpad\n")


class MigrationPlannerTests(unittest.TestCase):
    def _row(self, report, nid):
        return next(r for r in report.rows if r.note_id == nid)

    def test_valid_note_without_decision_is_ready_zero_delta(self):
        mem = _mig_memory(_mig_note("MEM-2026-0001", body=NOTE_WITH_SUPPORT))
        report = evidence_migrate.plan(mem, {}, {}, TODAY)
        self.assertTrue(report.ready)
        row = self._row(report, "MEM-2026-0001")
        self.assertEqual((row.status, row.delta_bytes), ("READY", 0))

    def test_add_projects_positive_delta_and_stays_ready(self):
        mem = _mig_memory(_mig_note("MEM-2026-0002"))
        decisions = {"MEM-2026-0002": {"action": "ADD", "evidence": MIG_EV}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertTrue(report.ready)
        row = self._row(report, "MEM-2026-0002")
        self.assertEqual(row.status, "READY")
        self.assertGreater(row.delta_bytes, 0)

    def test_missing_evidence_without_decision_blocks_with_identity(self):
        mem = _mig_memory(_mig_note("MEM-2026-0003"))
        report = evidence_migrate.plan(mem, {}, {}, TODAY)
        self.assertFalse(report.ready)
        row = self._row(report, "MEM-2026-0003")
        self.assertEqual(row.status, "BLOCKED")
        self.assertEqual(row.path, "/MEMORY.md")
        self.assertEqual(row.confidence, "HIGH")

    def test_archive_l3_projects_deterministic_target_and_excludes_span(self):
        note = _mig_note("MEM-2026-0004", body="- **Observation:** o\n")
        mem = _mig_memory(note)
        l3 = {"/Memory/lessons/MEM-2026-0005-x.md":
              "# [MEM-2026-0005] X\n- **Observation:** o\n"}
        decisions = {
            "MEM-2026-0004": {"action": "ARCHIVE"},
            "MEM-2026-0005": {"action": "ARCHIVE"},
        }
        report = evidence_migrate.plan(mem, l3, decisions, TODAY)
        self.assertTrue(report.ready)
        l2row = self._row(report, "MEM-2026-0004")
        self.assertEqual(
            l2row.target_path,
            "Memory/_archive/evidence-migration/l2/MEM-2026-0004-note.md")
        self.assertLess(l2row.delta_bytes, 0)
        l3row = self._row(report, "MEM-2026-0005")
        self.assertEqual(
            l3row.target_path,
            "Memory/_archive/evidence-migration/lessons/MEM-2026-0005-x.md")
        # projected L2 no longer contains the archived note heading.
        self.assertNotIn(str(report.projected_l2_bytes), ("",))
        self.assertLess(report.projected_l2_bytes, len(mem.encode("utf-8")))

    def test_archive_blocked_by_active_incoming_link(self):
        linker = _mig_note("MEM-2026-0006", body=NOTE_WITH_SUPPORT,
                           links=" [[MEM-2026-0007]]")
        target = _mig_note("MEM-2026-0007", body="- **Observation:** o\n")
        mem = _mig_memory(linker, target)
        decisions = {"MEM-2026-0007": {"action": "ARCHIVE"}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        row = self._row(report, "MEM-2026-0007")
        self.assertEqual(row.status, "ARCHIVE")
        self.assertIn("incoming link", row.detail)

    def test_archive_blocked_by_l2_stub(self):
        stub = ("### [MEM-2026-0008] Stubbed\n"
                "- **Stub.** Full note: `Memory/lessons/MEM-2026-0009-x.md`\n\n")
        mem = _mig_memory(stub)
        l3 = {"/Memory/lessons/MEM-2026-0009-x.md":
              "# [MEM-2026-0009] X\n- **Observation:** o\n"}
        decisions = {"MEM-2026-0009": {"action": "ARCHIVE"}}
        report = evidence_migrate.plan(mem, l3, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertEqual(self._row(report, "MEM-2026-0009").status, "ARCHIVE")

    def test_malformed_existing_ledger_is_never_appended(self):
        note = _mig_note("MEM-2026-0010", body="- **Evidence:** not json\n")
        mem = _mig_memory(note)
        decisions = {"MEM-2026-0010": {"action": "ADD", "evidence": MIG_EV}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertIn("malformed", self._row(report, "MEM-2026-0010").detail)

    def test_invalid_proposed_evidence_blocks(self):
        mem = _mig_memory(_mig_note("MEM-2026-0011"))
        bad = dict(MIG_EV, stance="MAYBE")
        decisions = {"MEM-2026-0011": {"action": "ADD", "evidence": bad}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertEqual(self._row(report, "MEM-2026-0011").status, "BLOCKED")

    def test_stale_decision_on_valid_note_blocks(self):
        mem = _mig_memory(_mig_note("MEM-2026-0012", body=NOTE_WITH_SUPPORT))
        decisions = {"MEM-2026-0012": {"action": "ADD", "evidence": MIG_EV}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertIn("stale", self._row(report, "MEM-2026-0012").detail)

    def test_unknown_decision_id_blocks(self):
        mem = _mig_memory(_mig_note("MEM-2026-0013", body=NOTE_WITH_SUPPORT))
        decisions = {"MEM-2026-9999": {"action": "ADD", "evidence": MIG_EV}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertEqual(self._row(report, "MEM-2026-9999").status, "BLOCKED")

    def test_unsupported_action_blocks(self):
        mem = _mig_memory(_mig_note("MEM-2026-0014"))
        decisions = {"MEM-2026-0014": {"action": "DELETE"}}
        report = evidence_migrate.plan(mem, {}, decisions, TODAY)
        self.assertFalse(report.ready)
        self.assertIn("unsupported", self._row(report, "MEM-2026-0014").detail)

    def test_cap_overflow_reports_exact_values_and_blocks(self):
        big = "- **Observation:** " + ("x" * 13000) + "\n"
        mem = _mig_memory(_mig_note("MEM-2026-0015", body=NOTE_WITH_SUPPORT + big))
        report = evidence_migrate.plan(mem, {}, {}, TODAY)
        self.assertFalse(report.ready)
        self.assertGreater(report.projected_l2_bytes, evidence_migrate.MAX_L2_BYTES)

    def test_duplicate_active_self_id_blocks(self):
        note = _mig_note("MEM-2026-0016", body=NOTE_WITH_SUPPORT)
        mem = _mig_memory(note, note)
        report = evidence_migrate.plan(mem, {}, {}, TODAY)
        self.assertFalse(report.ready)
        self.assertIn("duplicate", self._row(report, "MEM-2026-0016").detail)

    def test_cli_is_immutable_and_deterministic(self):
        work = Path(tempfile.mkdtemp())
        mem = _mig_memory(_mig_note("MEM-2026-0017"))
        (work / "MEMORY.md").write_text(mem, encoding="utf-8")
        l3_name, l3_real = "l3a.md", "/Memory/lessons/MEM-2026-0018-x.md"
        (work / l3_name).write_text(
            "# [MEM-2026-0018] X\n- **Observation:** o\n", encoding="utf-8")
        (work / "_manifest.json").write_text(
            json.dumps({"MEMORY.md": "/MEMORY.md", l3_name: l3_real}),
            encoding="utf-8")
        (work / "dec.json").write_text(json.dumps({
            "schema_version": 1,
            "notes": {
                "MEM-2026-0017": {"action": "ADD", "evidence": MIG_EV},
                "MEM-2026-0018": {"action": "ADD", "evidence": MIG_EV},
            }}), encoding="utf-8")

        def digest():
            h = hashlib.sha256()
            for p in sorted(work.glob("*")):
                if p.name.startswith("report"):
                    continue
                h.update(p.read_bytes())
            return h.hexdigest()

        script = Path(__file__).resolve().parent / "evidence_migrate.py"
        args = ["--memory", str(work / "MEMORY.md"),
                "--manifest", str(work / "_manifest.json"),
                "--decisions", str(work / "dec.json"), "--today", "2026-09-03"]
        before = digest()
        r1 = subprocess.run([sys.executable, str(script), *args,
                             "--output", str(work / "report.json")],
                            capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script), *args,
                             "--output", str(work / "report2.json")],
                            capture_output=True, text=True)
        self.assertEqual(before, digest())
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual((work / "report.json").read_bytes(),
                         (work / "report2.json").read_bytes())
        payload = json.loads((work / "report.json").read_text())
        self.assertTrue(payload["ready"])



if __name__ == "__main__":
    unittest.main()
