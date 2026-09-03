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


if __name__ == "__main__":
    unittest.main()
