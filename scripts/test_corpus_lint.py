#!/usr/bin/env python3
"""Tests for corpus_lint.py against synthetic corpus-v2 fixtures.

A controlled fixture is enough here: these assertions are about the contract
(schema, isolation, hashing), not about model semantics. Real coverage floors
(>=200) are exercised only by asserting they FIRE on a small corpus.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("corpus_lint", HERE / "corpus_lint.py")
corpus_lint = importlib.util.module_from_spec(spec)
sys.modules["corpus_lint"] = corpus_lint
spec.loader.exec_module(corpus_lint)

SPLITS = corpus_lint.SPLITS


def base_data():
    return {
        "train": {
            "notes": [
                {"id": "n-net-pos", "text": "outbound sockets are refused so packages must be supplied offline", "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast"},
                {"id": "n-net-hard", "text": "the office internet was upgraded last week", "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast"},
                {"id": "n-net-easy", "text": "the coffee machine on the third floor is broken", "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast"},
            ],
            "queries": [
                {"id": "q-net", "text": "the sandbox cannot reach the internet", "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast", "contrast_families": ["online-vs-offline"], "gold": ["n-net-pos"], "hard_negatives": [{"note_id": "n-net-hard", "contrast_family": "online-vs-offline"}], "easy_negatives": ["n-net-easy"], "rationale": "egress denial vs an unrelated internet mention"},
            ],
        },
        "dev": {
            "notes": [
                {"id": "n-sec-pos", "text": "refuse any write whose payload carries an api token or password", "language": "en", "scenario_group": "sg-secret", "provenance": "synthetic-contrast"},
                {"id": "n-sec-hard", "text": "document how operators request new credentials from the identity team", "language": "en", "scenario_group": "sg-secret", "provenance": "synthetic-contrast"},
                {"id": "n-sec-easy", "text": "list the opening hours of the cafeteria", "language": "en", "scenario_group": "sg-secret", "provenance": "synthetic-contrast"},
            ],
            "queries": [
                {"id": "q-sec", "text": "block credentials from being saved into notes", "language": "en", "scenario_group": "sg-secret", "provenance": "synthetic-contrast", "contrast_families": ["permit-vs-prohibit"], "gold": ["n-sec-pos"], "hard_negatives": [{"note_id": "n-sec-hard", "contrast_family": "permit-vs-prohibit"}], "easy_negatives": ["n-sec-easy"], "rationale": "enforcement behaviour vs a process description"},
            ],
        },
        "certification": {
            "notes": [
                {"id": "n-roll-pos", "text": "roll back to the previous snapshot when a mutation did not succeed", "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast"},
                {"id": "n-roll-hard", "text": "archive the report of a change that completed successfully", "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast"},
                {"id": "n-roll-easy", "text": "book the meeting room for the finance review", "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast"},
            ],
            "queries": [
                {"id": "q-roll", "text": "restore the workspace after a repair failed", "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast", "contrast_families": ["apply-vs-rollback"], "gold": ["n-roll-pos"], "hard_negatives": [{"note_id": "n-roll-hard", "contrast_family": "apply-vs-rollback"}], "easy_negatives": ["n-roll-easy"], "rationale": "recovery from an unsuccessful change vs a successful archive"},
            ],
        },
    }


class CorpusLintTests(unittest.TestCase):
    def build(self, data, denylist=None):
        root = Path(tempfile.mkdtemp(prefix="corpus-v2-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for split in SPLITS:
            (root / split).mkdir(parents=True, exist_ok=True)
            for kind in ("notes", "queries"):
                recs = data.get(split, {}).get(kind, [])
                (root / split / f"{kind}.jsonl").write_bytes(corpus_lint.canonical_bytes(recs))
        if denylist is not None:
            (root / corpus_lint.DEID_DENYLIST).write_text("\n".join(denylist) + "\n", encoding="utf-8")
        return root

    def errors(self, data, enforce_floors=False, **kw):
        root = self.build(data, **kw)
        errs, _ = corpus_lint.lint(root, enforce_floors=enforce_floors)
        return errs

    # --- happy path ---------------------------------------------------------

    def test_clean_corpus_passes_structure(self):
        errs = self.errors(base_data())
        self.assertEqual(errs, [], f"clean corpus should lint clean: {errs}")

    def test_manifest_hash_is_hex64(self):
        root = self.build(base_data())
        _, manifest = corpus_lint.lint(root, enforce_floors=False)
        self.assertEqual(len(manifest["corpus_hash"]), 64)
        int(manifest["corpus_hash"], 16)

    # --- schema / vocab -----------------------------------------------------

    def test_missing_field_caught(self):
        data = deepcopy(base_data())
        del data["certification"]["notes"][0]["provenance"]
        self.assertTrue(any("missing field provenance" in e for e in self.errors(data)))

    def test_bad_contrast_family_caught(self):
        data = deepcopy(base_data())
        data["train"]["queries"][0]["contrast_families"] = ["not-a-family"]
        self.assertTrue(any("bad contrast_family" in e for e in self.errors(data)))

    def test_raw_provenance_rejected(self):
        data = deepcopy(base_data())
        data["train"]["notes"][0]["provenance"] = "raw"
        self.assertTrue(any("bad provenance" in e for e in self.errors(data)))

    def test_id_prefix_enforced(self):
        data = deepcopy(base_data())
        data["dev"]["notes"].append({"id": "sec-extra", "text": "some unrelated distractor line", "language": "en", "scenario_group": "sg-secret", "provenance": "synthetic-contrast"})
        self.assertTrue(any("must start with 'n-'" in e for e in self.errors(data)))

    # --- referential integrity ----------------------------------------------

    def test_gold_must_reference_note_in_split(self):
        data = deepcopy(base_data())
        data["certification"]["queries"][0]["gold"] = ["n-does-not-exist"]
        self.assertTrue(any("gold n-does-not-exist not a note" in e for e in self.errors(data)))

    def test_hard_negative_requires_contrast_tag(self):
        data = deepcopy(base_data())
        data["train"]["queries"][0]["hard_negatives"] = ["n-net-hard"]
        self.assertTrue(any("hard_negative must be" in e for e in self.errors(data)))

    def test_positive_may_not_repeat_all_query_tokens(self):
        data = deepcopy(base_data())
        data["train"]["queries"][0]["text"] = "sockets refused offline"
        data["train"]["notes"][0]["text"] = "outbound sockets refused offline for supplied packages"
        self.assertTrue(any("must not repeat query vocabulary" in e for e in self.errors(data)))

    # --- duplicate / isolation / leakage ------------------------------------

    def test_duplicate_note_text_within_split(self):
        data = deepcopy(base_data())
        data["certification"]["notes"].append({"id": "n-roll-dup", "text": data["certification"]["notes"][0]["text"], "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast"})
        self.assertTrue(any("duplicate note text" in e for e in self.errors(data)))

    def test_scenario_group_isolation(self):
        data = deepcopy(base_data())
        data["train"]["notes"].append({"id": "n-roll-leak", "text": "a distinct line about rolling changes back again", "language": "en", "scenario_group": "sg-roll", "provenance": "synthetic-contrast"})
        self.assertTrue(any("spans splits" in e for e in self.errors(data)))

    def test_cross_split_near_duplicate_leakage(self):
        data = deepcopy(base_data())
        # a train note (different scenario group) copying a certification note's text
        data["train"]["notes"].append({"id": "n-net-leak", "text": data["certification"]["notes"][0]["text"], "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast"})
        self.assertTrue(any("leakage" in e for e in self.errors(data)))

    # --- de-identification ---------------------------------------------------

    def test_denylist_marker_caught(self):
        data = deepcopy(base_data())
        data["train"]["notes"][0]["text"] = "the real project codename BLUEHERON leaked into a note"
        errs = self.errors(data, denylist=["BLUEHERON"])
        self.assertTrue(any("de-id marker" in e for e in errs))

    # --- canonical form ------------------------------------------------------

    def test_non_canonical_file_flagged(self):
        root = self.build(base_data())
        recs = base_data()["certification"]["queries"]
        # valid per-line JSON but non-minified (spaces, unsorted keys) -> not canonical
        raw = "\n".join(json.dumps(r) for r in recs) + "\n"
        (root / "certification" / "queries.jsonl").write_text(raw, encoding="utf-8")
        errs, _ = corpus_lint.lint(root, enforce_floors=False)
        self.assertTrue(any("not in canonical form" in e for e in errs))
        self.assertFalse(any("invalid JSON" in e for e in errs))

    # --- coverage floors -----------------------------------------------------

    def test_floors_fire_on_small_corpus(self):
        errs = self.errors(base_data(), enforce_floors=True)
        self.assertTrue(any("certification queries" in e and "< 200" in e for e in errs))
        self.assertTrue(any("contrast family" in e and "< 15" in e for e in errs))

    # --- unseen-certification guard -----------------------------------------

    def test_load_split_seals_certification(self):
        root = self.build(base_data())
        with self.assertRaises(PermissionError):
            corpus_lint.load_split(root, "certification")
        got = corpus_lint.load_split(root, "certification", certified_run=True)
        self.assertEqual(got["queries"][0]["id"], "q-roll")
        self.assertTrue(corpus_lint.load_split(root, "train")["notes"])

    def test_config_referencing_certification_is_flagged(self):
        root = self.build(base_data())
        clean = root / "train.cfg"
        clean.write_text("source = corpus-v2/train/queries.jsonl\n", encoding="utf-8")
        self.assertEqual(corpus_lint.check_no_cert_reference(clean), [])
        bad = root / "bad.cfg"
        bad.write_text("source = corpus-v2/certification/queries.jsonl\n", encoding="utf-8")
        self.assertTrue(corpus_lint.check_no_cert_reference(bad))

    # --- manifest roundtrip --------------------------------------------------

    def test_manifest_check_roundtrips_and_detects_drift(self):
        root = self.build(base_data())
        _, manifest = corpus_lint.lint(root, enforce_floors=False)
        self.assertIs(manifest["frozen"], True)
        (root / corpus_lint.MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _, recomputed = corpus_lint.lint(root, enforce_floors=False)
        self.assertEqual(corpus_lint.check_manifest(root, recomputed), [])
        # mutate a split file, hashes must drift
        extra = base_data()
        extra["train"]["notes"].append({"id": "n-net-extra", "text": "a brand new distinct distractor about parking permits", "language": "en", "scenario_group": "sg-net", "provenance": "synthetic-contrast"})
        (root / "train" / "notes.jsonl").write_bytes(corpus_lint.canonical_bytes(extra["train"]["notes"]))
        _, drifted = corpus_lint.lint(root, enforce_floors=False)
        self.assertTrue(corpus_lint.check_manifest(root, drifted))

    def test_manifest_requires_frozen_marker(self):
        root = self.build(base_data())
        _, manifest = corpus_lint.lint(root, enforce_floors=False)
        declared = {key: value for key, value in manifest.items() if key != "frozen"}
        (root / corpus_lint.MANIFEST).write_text(
            json.dumps(declared, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.assertIn(
            "manifest.json frozen mismatch (declared != recomputed)",
            corpus_lint.check_manifest(root, manifest),
        )

    # --- determinism ---------------------------------------------------------

    def test_canonical_bytes_is_order_independent(self):
        recs = deepcopy(base_data()["certification"]["notes"])
        first = corpus_lint.canonical_bytes(recs)
        second = corpus_lint.canonical_bytes(list(reversed(recs)))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
