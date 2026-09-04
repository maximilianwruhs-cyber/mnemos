#!/usr/bin/env python3
"""Behavioral tests for the staged semantic-recall baseline."""
from __future__ import annotations
import hashlib
import json
import tempfile
import shutil
from pathlib import Path

import unittest
from unittest import mock

import numpy as np

import recall
import semantic_baseline as baseline


class LexicalBaselineTests(unittest.TestCase):
    def test_unicode_tokens_keep_german_words_whole(self):
        self.assertEqual(
            baseline.unicode_tokens("Prüfen für größere Schlüssel"),
            ["prüfen", "für", "grössere", "schlüssel"],
        )

    def test_current_ascii_behavior_remains_visible(self):
        self.assertNotIn("prüfen", recall.tokens("Prüfen"))

    def test_note_id_never_enters_search_tokens(self):
        notes = [{"id": "n-secret-widget", "text": "unrelated body"}]
        docs = baseline.adapt_notes(notes, recall.tokens)
        self.assertNotIn("secret", docs["n-secret-widget"]["tokens"])
        self.assertNotIn("widget", docs["n-secret-widget"]["tokens"])

    def test_zero_overlap_has_no_arbitrary_candidate(self):
        notes = [{"id": "n-a", "text": "alpha beta"}]
        self.assertEqual(baseline.rank_bm25("unseen", notes, recall.tokens), [])

    def test_bm25_ties_break_by_note_id(self):
        notes = [
            {"id": "n-b", "text": "matching token"},
            {"id": "n-a", "text": "matching token"},
        ]
        self.assertEqual(
            [note_id for note_id, _score in baseline.rank_bm25("matching", notes, recall.tokens)],
            ["n-a", "n-b"],
        )


class IdentifierProtectionTests(unittest.TestCase):
    def test_identifier_tokens_include_codes_paths_and_flags(self):
        self.assertEqual(
            baseline.identifier_tokens("Use KX-4471, EVT.908 and --offline, not API"),
            ("--offline", "evt.908", "kx-4471"),
        )

    def test_hyphenated_natural_word_is_not_identifier(self):
        self.assertEqual(baseline.identifier_tokens("read-only mode"), ())

    def test_protected_notes_follow_deterministic_order_then_id(self):
        notes = [
            {"id": "n-b", "text": "KX-4471 alternate"},
            {"id": "n-a", "text": "KX-4471 exact"},
        ]
        ranked = [("n-b", 0.9)]
        self.assertEqual(
            baseline.protected_note_ids("fix KX-4471", notes, ranked),
            ("n-b", "n-a"),
        )

    def test_identifier_matching_is_exact_not_prefix_based(self):
        notes = [{"id": "n-wrong", "text": "Alarm H-171 is unrelated"}]
        self.assertEqual(baseline.protected_note_ids("fix H-17", notes, []), ())

    def test_more_than_twenty_protected_notes_is_an_error(self):
        notes = [{"id": f"n-{index:02}", "text": "Code KX-4471"} for index in range(21)]
        with self.assertRaisesRegex(baseline.BaselineError, "identifier_budget_overflow"):
            baseline.protected_note_ids("fix KX-4471", notes, [])



class CandidateUnionTests(unittest.TestCase):
    def test_dedupes_and_scans_deeper_vector_hits_to_fill_twenty(self):
        lexical = [(f"n-{index:02}", 1.0 - index / 100) for index in range(16)]
        semantic = lexical[:8] + [(f"v-{index:02}", 0.8 - index / 100) for index in range(12)]
        got = baseline.union_candidates(lexical, semantic, (), lexical_reserve=16)
        self.assertEqual(len(got), 20)
        self.assertEqual(got[:16], [note_id for note_id, _score in lexical])

    def test_protected_hits_consume_reserve_and_stay_first(self):
        lexical = [("n-gold", 0.9), ("n-other", 0.8)]
        semantic = [("n-third", 0.95), ("n-gold", 0.7)]
        got = baseline.union_candidates(
            lexical, semantic, ("n-gold",), lexical_reserve=1, k=3
        )
        self.assertEqual(got, ["n-gold", "n-third", "n-other"])

    def test_identifier_overflow_fails_instead_of_truncating(self):
        with self.assertRaisesRegex(baseline.BaselineError, "identifier_budget_overflow"):
            baseline.union_candidates([], [], tuple(f"n-{index}" for index in range(21)), 10)

    def test_twenty_zero_policy_never_reads_semantic_ranking(self):
        class ExplodingRanking:
            def __iter__(self):
                raise AssertionError("semantic ranking was read")

        lexical = [(f"n-{index:02}", 1.0 - index / 100) for index in range(20)]
        got = baseline.union_candidates(
            lexical, ExplodingRanking(), (), lexical_reserve=20
        )
        self.assertEqual(got, [note_id for note_id, _score in lexical])


class PotionRankingTests(unittest.TestCase):
    def test_ranks_all_notes_by_cosine_then_note_id_without_floor(self):
        note_ids = ["n-b", "n-c", "n-a"]
        matrix = np.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], dtype="float32")
        with mock.patch.object(
            baseline.vecidx, "embed_query", return_value=np.asarray([1.0, 0.0], dtype="float32")
        ):
            got = baseline.rank_potion("query", note_ids, matrix)
        self.assertEqual([note_id for note_id, _score in got], ["n-a", "n-b", "n-c"])
        self.assertEqual(got[-1], ("n-c", 0.0))


def policy_report(tokenizer, reserve, recall, hard_ok=True):
    return {
        "policy_id": f"{tokenizer}-{reserve}-{20 - reserve}",
        "tokenizer": tokenizer,
        "lexical_reserve": reserve,
        "semantic_reserve": 20 - reserve,
        "overall": {"hit_at_20": int(recall * 100), "queries": 100},
        "gate_errors": [] if hard_ok else ["safety Recall@20 5/6 != 6/6"],
    }


class PolicySelectionTests(unittest.TestCase):
    def test_grid_is_exactly_fourteen_predeclared_policies(self):
        self.assertEqual(len(baseline.policy_grid()), 14)
        self.assertEqual(baseline.policy_grid()[0], ("current_ascii", 20))
        self.assertEqual(baseline.policy_grid()[-1], ("unicode_nfc", 0))

    def test_selection_prefers_recall_then_current_then_larger_lexical_reserve(self):
        reports = [
            policy_report("unicode_nfc", 16, recall=1.0),
            policy_report("current_ascii", 12, recall=1.0),
            policy_report("current_ascii", 16, recall=1.0),
        ]
        self.assertEqual(baseline.select_policy(reports)["policy_id"], "current_ascii-16-4")

    def test_no_hard_gate_policy_returns_none(self):
        self.assertIsNone(
            baseline.select_policy([
                policy_report("unicode_nfc", 10, recall=1.0, hard_ok=False)
            ])
        )

    def test_dev_gate_accepts_exactly_155_of_156_with_complete_hard_slices(self):
        report = {
            "overall": {"hit_at_20": 155, "queries": 156},
            "safety": {"hit_at_20": 120, "queries": 120},
            "identifier": {"hit_at_20": 12, "queries": 12},
            "protected_dropped": 0,
        }
        self.assertEqual(baseline.candidate_gate(report, expected_queries=156), [])

    def test_dev_gate_rejects_incomplete_safety_slice(self):
        report = {
            "overall": {"hit_at_20": 155, "queries": 156},
            "safety": {"hit_at_20": 119, "queries": 120},
            "identifier": {"hit_at_20": 12, "queries": 12},
            "protected_dropped": 0,
        }
        errors = baseline.candidate_gate(report, expected_queries=156)
        self.assertIn("safety Recall@20 119/120 != 120/120", errors)


def candidate_split():
    return {
        "notes": [
            {"id": "n-code-gold", "text": "Alarm H-17 means low inlet pressure."},
            {"id": "n-code-hard", "text": "Alarm H-71 means overheating."},
            {"id": "n-safety-gold", "text": "Restore account access after verification."},
            {"id": "n-safety-hard", "text": "Block account access pending review."},
        ],
        "queries": [
            {
                "id": "q-code", "text": "Fix H-17", "gold": ["n-code-gold"],
                "contrast_families": ["identifier-tokens"],
            },
            {
                "id": "q-safety", "text": "Restore account access", "gold": ["n-safety-gold"],
                "contrast_families": ["permit-vs-prohibit"],
            },
        ],
    }


class PolicyEvaluationTests(unittest.TestCase):
    def semantic_rankings(self):
        return {
            "q-code": [("n-code-gold", 0.9), ("n-code-hard", 0.8)],
            "q-safety": [("n-safety-gold", 0.9), ("n-safety-hard", 0.8)],
        }

    def test_policy_report_counts_overall_safety_identifier_and_protection(self):
        report = baseline.evaluate_candidate_policy(
            candidate_split(), "current_ascii", 20, self.semantic_rankings()
        )
        self.assertEqual(report["overall"], {"queries": 2, "hit_at_20": 2})
        self.assertEqual(report["safety"], {"queries": 1, "hit_at_20": 1})
        self.assertEqual(report["identifier"], {"queries": 1, "hit_at_20": 1})
        self.assertEqual(report["protected_dropped"], 0)
        self.assertEqual(report["gate_errors"], [])

    def test_all_policy_evaluation_returns_the_frozen_grid(self):
        reports = baseline.evaluate_all_candidate_policies(
            candidate_split(), self.semantic_rankings()
        )
        self.assertEqual([report["policy_id"] for report in reports], [
            f"{tokenizer}-{reserve}-{20 - reserve}"
            for tokenizer, reserve in baseline.policy_grid()
        ])

    def test_identifier_query_requires_an_exact_gold_hit(self):
        split = candidate_split()
        split["queries"][0]["gold"] = ["n-code-hard"]
        with self.assertRaisesRegex(baseline.BaselineError, "no exact identifier gold"):
            baseline.evaluate_candidate_policy(
                split, "current_ascii", 20, self.semantic_rankings()
            )



def metric_query(query_id="q-1", language="de", scenario_group="g-1"):
    return {
        "id": query_id,
        "gold": ["n-en", "n-de"],
        "hard_negatives": [
            {"note_id": "n-hard", "contrast_family": "permit-vs-prohibit"}
        ],
        "language": language,
        "contrast_families": ["permit-vs-prohibit"],
        "provenance": "synthetic-contrast",
        "scenario_group": scenario_group,
    }


def metric_notes():
    return {
        "n-en": {"language": "en"},
        "n-de": {"language": "de"},
        "n-hard": {"language": "de"},
        "n-x": {"language": "en"},
        "n-protected": {"language": "en"},
    }


class MetricTests(unittest.TestCase):
    def test_any_gold_counts_once_and_records_language_and_hard_negative_ranks(self):
        got = baseline.score_query(
            metric_query(), ["n-x", "n-de", "n-en", "n-hard"], set(), metric_notes()
        )
        self.assertTrue(got["hit_at_3"])
        self.assertEqual(got["first_gold_rank"], 2)
        self.assertEqual(got["reciprocal_rank"], 0.5)
        self.assertTrue(got["same_language_gold_at_3"])
        self.assertTrue(got["cross_language_gold_at_3"])
        self.assertTrue(got["both_golds_at_3"])
        self.assertEqual(got["hard_negative_ranks"], {"n-hard": 4})

    def test_hard_negative_top_one_is_explicit(self):
        got = baseline.score_query(
            metric_query(), ["n-hard", "n-de"], set(), metric_notes()
        )
        self.assertEqual(got["top1_hard_negative"], "n-hard")

    def test_empty_ranking_is_zero_candidate_and_not_eligible(self):
        got = baseline.score_query(metric_query(), [], set(), metric_notes())
        self.assertTrue(got["zero_candidates"])
        self.assertFalse(got["eligible"])

    def test_protected_gold_disqualifies_otherwise_eligible_query(self):
        unprotected = baseline.score_query(
            metric_query(), ["n-de", "n-hard"], set(), metric_notes()
        )
        protected = baseline.score_query(
            metric_query(), ["n-de", "n-hard"], {"n-de"}, metric_notes()
        )
        self.assertTrue(unprotected["eligible"])
        self.assertFalse(protected["eligible"])

    def test_rerank_margins_ignore_protected_none_scores(self):
        got = baseline.score_rerank(
            metric_query(),
            [("n-protected", None), ("n-de", 2.5), ("n-hard", 1.0)],
            {"n-protected"}, metric_notes(),
        )
        self.assertEqual(got["semantic_top1_top2_margin"], 1.5)
        self.assertEqual(got["semantic_top1_best_hard_margin"], 1.5)

    def test_aggregate_reports_language_family_and_provenance_slices(self):
        first = metric_query("q-1", "de", "g-1")
        second = metric_query("q-2", "en", "g-2")
        results = {
            "q-1": baseline.score_query(first, ["n-de"], set(), metric_notes()),
            "q-2": baseline.score_query(second, ["n-x"], set(), metric_notes()),
        }
        got = baseline.aggregate_results([first, second], results)
        self.assertEqual(got["overall"]["queries"], 2)
        self.assertEqual(got["overall"]["hit_at_1"], 1)
        self.assertEqual(got["languages"]["de"]["hit_at_1"], 1)
        self.assertEqual(got["languages"]["en"]["hit_at_1"], 0)
        self.assertEqual(got["families"]["permit-vs-prohibit"]["queries"], 2)
        self.assertEqual(got["provenance"]["synthetic-contrast"]["queries"], 2)

    def test_compare_results_counts_corrections_and_regressions(self):
        base = {
            "q-fix": {"hit_at_1": False, "hit_at_20": True},
            "q-break": {"hit_at_1": True, "hit_at_20": True},
        }
        reranked = {
            "q-fix": {"hit_at_1": True, "hit_at_20": True},
            "q-break": {"hit_at_1": False, "hit_at_20": True},
        }
        self.assertEqual(baseline.compare_results(base, reranked), {
            "corrections": 1, "correction_query_ids": ["q-fix"],
            "regressions": 1, "regression_query_ids": ["q-break"],
        })


class BootstrapTests(unittest.TestCase):
    def test_resamples_whole_scenario_groups(self):
        rows = [
            {"scenario_group": "a", "correct": 1.0},
            {"scenario_group": "a", "correct": 1.0},
            {"scenario_group": "b", "correct": 0.0},
        ]
        got = baseline.cluster_interval(rows, "correct", seed=0, samples=1)
        self.assertEqual(got["groups"], 2)
        self.assertAlmostEqual(got["observed"], 2 / 3)
        self.assertEqual((got["lower"], got["upper"]), (0.0, 0.0))

    def test_paired_interval_requires_matching_queries(self):
        left = [{"query_id": "q-1", "scenario_group": "a", "correct": 0.0}]
        right = [{"query_id": "q-2", "scenario_group": "a", "correct": 1.0}]
        with self.assertRaisesRegex(baseline.BaselineError, "paired query IDs"):
            baseline.paired_cluster_interval(left, right, "correct", seed=7, samples=10)

    def test_paired_interval_is_deterministic_and_uses_aligned_groups(self):
        left = [
            {"query_id": "q-1", "scenario_group": "a", "correct": 0.0},
            {"query_id": "q-2", "scenario_group": "a", "correct": 0.0},
            {"query_id": "q-3", "scenario_group": "b", "correct": 1.0},
        ]
        right = [
            {"query_id": "q-1", "scenario_group": "a", "correct": 1.0},
            {"query_id": "q-2", "scenario_group": "a", "correct": 1.0},
            {"query_id": "q-3", "scenario_group": "b", "correct": 1.0},
        ]
        first = baseline.paired_cluster_interval(left, right, "correct", seed=7, samples=100)
        second = baseline.paired_cluster_interval(left, right, "correct", seed=7, samples=100)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["observed"], 2 / 3)


class CanonicalArtifactTests(unittest.TestCase):
    def test_write_json_uses_canonical_utf8_and_returns_its_hash(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "report.json"
            digest = baseline.write_json(path, {"b": 2, "a": "ü"})
            expected = b'{"a":"\xc3\xbc","b":2}\n'
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(digest, hashlib.sha256(expected).hexdigest())


class BaselineStateTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="baselinecase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        source = Path(__file__).parent / "fixtures/vector-semantics/baseline-v1/config.json"
        self.config = self.work / "config.json"
        shutil.copy2(source, self.config)

    def test_train_writes_all_fourteen_policies_and_freezes_one(self):
        split = {"notes": [], "queries": []}
        reports = [policy_report(name, reserve, 1.0) for name, reserve in baseline.policy_grid()]
        result = baseline.run_train(
            self.config,
            self.work,
            preflight_fn=lambda *_args, **_kwargs: None,
            load_split=lambda _root, _split, certified_run=False: split,
            evaluate_policies=lambda _split: reports,
            metadata_fn=lambda **_kwargs: {"git_commit": "abc123"},
        )
        self.assertEqual(result["status"], "POLICY_SELECTED")
        report = json.loads((self.work / "train-report.json").read_text(encoding="utf-8"))
        policy = json.loads((self.work / "selected-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["policies"]), 14)
        self.assertEqual(policy["config_sha256"], result["config_sha256"])
        self.assertEqual(report["b0"]["current_ascii"]["confidence"], {})
        self.assertEqual(report["b0"]["unicode_nfc"]["confidence"], {})

    def test_train_no_go_writes_terminal_manifest(self):
        baseline.write_json(self.work / "model-manifest.json", {"revision": "pinned"})
        reports = [
            policy_report(name, reserve, recall=1.0, hard_ok=False)
            for name, reserve in baseline.policy_grid()
        ]
        result = baseline.run_train(
            self.config,
            self.work,
            preflight_fn=lambda *_args, **_kwargs: None,
            load_split=lambda _root, _split, certified_run=False: {"notes": [], "queries": []},
            evaluate_policies=lambda _split: reports,
            metadata_fn=lambda **_kwargs: {"git_commit": "abc123"},
        )
        self.assertEqual(result["status"], "CANDIDATE_NO_GO")
        selected = json.loads((self.work / "selected-policy.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.work / "manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(selected["selected"])
        self.assertEqual(manifest["status"], "CANDIDATE_NO_GO")

    def test_dev_rejects_policy_hash_drift_before_loading_split(self):
        (self.work / "selected-policy.json").write_bytes(
            baseline.canonical_json_bytes({"config_sha256": "0" * 64})
        )
        with self.assertRaisesRegex(baseline.SetupError, "config_sha256"):
            baseline.run_dev(
                self.config,
                self.work,
                self.work / "model-manifest.json",
                preflight_fn=lambda *_args, **_kwargs: None,
                load_split=lambda *_args, **_kwargs: self.fail(
                    "loader called before policy check"
                ),
            )

    def test_candidate_failure_does_not_invoke_reranker(self):
        called = []
        result = baseline.finish_dev(
            candidate_report={"gate_errors": ["safety Recall@20 5/6 != 6/6"]},
            reranker_factory=lambda *_args: called.append(True),
        )
        self.assertEqual(result["status"], "CANDIDATE_NO_GO")
        self.assertEqual(called, [])

    def test_split_guard_never_opens_certification(self):
        calls = []

        def loader(_root, split_name, certified_run=False):
            calls.append((split_name, certified_run))
            return {"notes": [], "queries": []}

        baseline.load_baseline_split(Path("corpus-v2"), "train", loader=loader)
        self.assertEqual(calls, [("train", False)])
        with self.assertRaisesRegex(baseline.BaselineError, "certification"):
            baseline.load_baseline_split(
                Path("corpus-v2"), "certification", loader=loader
            )

    def test_repeatability_rejects_changed_order(self):
        runs = [{"q-1": ["n-a", "n-b"]} for _index in range(19)]
        runs.append({"q-1": ["n-b", "n-a"]})
        with self.assertRaisesRegex(baseline.SetupError, "determinism"):
            baseline.assert_repeatable_rankings(runs)

    def test_repeatability_returns_hash_for_twenty_identical_runs(self):
        runs = [{"q-1": ["n-a", "n-b"]} for _index in range(20)]
        digest = baseline.assert_repeatable_rankings(runs)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_missing_query_result_is_a_setup_error(self):
        with self.assertRaisesRegex(baseline.SetupError, "q-2"):
            baseline.validate_query_coverage(["q-1", "q-2"], {"q-1": {}})

    def test_split_counts_must_match_frozen_manifest(self):
        with self.assertRaisesRegex(baseline.SetupError, "train notes 1 != 0"):
            baseline.validate_split_counts(
                {"notes": [{"id": "n-1"}], "queries": []},
                {"notes": 0, "queries": 0},
                "train",
            )

    def test_existing_dev_report_is_immutable(self):
        path = self.work / "dev-report.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(baseline.SetupError, "already exists"):
            baseline.require_new_output(path)

    def test_environment_metadata_has_required_identity(self):
        got = baseline.environment_metadata(
            ("numpy",),
            "CPUExecutionProvider",
            package_version=lambda _name: "1.0",
            git_commit=lambda: "abc123",
            platform_info=lambda: "Windows-test",
            cpu_name=lambda: "CPU-test",
        )
        self.assertEqual(got["git_commit"], "abc123")
        self.assertEqual(got["dependencies"], {"numpy": "1.0"})
        self.assertEqual(got["provider"], "CPUExecutionProvider")
        self.assertEqual(got["cpu"], "CPU-test")
        self.assertEqual(got["platform"], "Windows-test")
        self.assertIn("python", got)


def write_valid_baseline_artifacts(root):
    config = {
        "baseline_version": "v1",
        "corpus": {"corpus_hash": "abc123"},
        "reranker": {
            "repository": "owner/model", "revision": "rev",
            "provider": "CPUExecutionProvider", "files": {},
        },
    }
    config_hash = baseline.write_json(root / "config.json", config)
    baseline.write_json(root / "model-manifest.json", {
        "repository": "owner/model", "revision": "rev",
        "provider": "CPUExecutionProvider", "files": {},
    })
    train_hash = baseline.write_json(root / "train-report.json", {
        "config_sha256": config_hash, "corpus_hash": "abc123",
    })
    baseline.write_json(root / "selected-policy.json", {
        "config_sha256": config_hash, "corpus_hash": "abc123",
        "train_report_sha256": train_hash, "selected": {"policy_id": "current_ascii-20-0"},
    })
    baseline.write_json(root / "dev-report.json", {
        "config_sha256": config_hash, "corpus_hash": "abc123",
        "status": "BASELINE_COMPLETE",
    })


class BaselineManifestTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="manifestcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        write_valid_baseline_artifacts(self.work)

    def test_complete_manifest_hashes_every_required_artifact(self):
        manifest = baseline.build_baseline_manifest(self.work, "BASELINE_COMPLETE")
        self.assertEqual(set(manifest["files"]), {
            "config.json", "dev-report.json", "model-manifest.json",
            "selected-policy.json", "train-report.json",
        })
        self.assertTrue(all(
            __import__("re").fullmatch(r"[0-9a-f]{64}", value)
            for value in manifest["files"].values()
        ))
        baseline.write_json(self.work / "manifest.json", manifest)
        self.assertEqual(baseline.check_baseline_artifacts(self.work), [])

    def test_manifest_check_detects_report_drift(self):
        manifest = baseline.build_baseline_manifest(self.work, "BASELINE_COMPLETE")
        baseline.write_json(self.work / "manifest.json", manifest)
        baseline.write_json(self.work / "train-report.json", {"changed": True})
        errors = baseline.check_baseline_artifacts(self.work)
        self.assertIn("train-report.json hash mismatch", errors)

    def test_manifest_check_detects_cross_file_identity_drift(self):
        selected = json.loads((self.work / "selected-policy.json").read_text(encoding="utf-8"))
        selected["config_sha256"] = "0" * 64
        baseline.write_json(self.work / "selected-policy.json", selected)
        baseline.write_json(
            self.work / "manifest.json",
            baseline.build_baseline_manifest(self.work, "BASELINE_COMPLETE"),
        )
        errors = baseline.check_baseline_artifacts(self.work)
        self.assertIn("selected-policy config_sha256 mismatch", errors)

    def test_manifest_check_rejects_noncanonical_report_even_when_hash_matches(self):
        dev_path = self.work / "dev-report.json"
        dev = json.loads(dev_path.read_text(encoding="utf-8"))
        dev_path.write_text(json.dumps(dev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        baseline.write_json(
            self.work / "manifest.json",
            baseline.build_baseline_manifest(self.work, "BASELINE_COMPLETE"),
        )
        errors = baseline.check_baseline_artifacts(self.work)
        self.assertIn("dev-report.json is not canonical JSON", errors)


class EvidenceRenderTests(unittest.TestCase):
    def test_render_uses_reported_counts_without_recalculation(self):
        train = {
            "status": "POLICY_SELECTED",
            "selected_policy_id": "unicode_nfc-16-4",
            "b0": {
                "current_ascii": {"metrics": {"overall": {"hit_at_20": 70, "queries": 84}}},
                "unicode_nfc": {"metrics": {"overall": {"hit_at_20": 75, "queries": 84}}},
            },
        }
        dev = {
            "status": "BASELINE_COMPLETE",
            "b1": {
                "overall": {"hit_at_20": 155, "queries": 156},
                "safety": {"hit_at_20": 120, "queries": 120},
                "identifier": {"hit_at_20": 12, "queries": 12},
            },
            "b2": {
                "metrics": {
                    "overall": {
                        "hit_at_1": 149, "hit_at_3": 154, "queries": 156,
                        "mean_reciprocal_rank": 0.97,
                    },
                    "languages": {}, "families": {},
                },
                "determinism": {"repeats": 20, "ranking_sha256": "a" * 64},
            },
        }
        text = baseline.render_evidence(train, dev, {"status": "BASELINE_COMPLETE"})
        self.assertIn("70/84", text)
        self.assertIn("75/84", text)
        self.assertIn("155/156", text)
        self.assertIn("149/156", text)
        self.assertIn("Windows timings are diagnostic", text)

    def test_render_candidate_no_go_states_that_b2_did_not_run(self):
        train = {
            "status": "CANDIDATE_NO_GO", "selected_policy_id": None,
            "b0": {
                "current_ascii": {"metrics": {"overall": {"hit_at_20": 60, "queries": 84}}},
                "unicode_nfc": {"metrics": {"overall": {"hit_at_20": 70, "queries": 84}}},
            },
        }
        text = baseline.render_evidence(train, None, {"status": "CANDIDATE_NO_GO"})
        self.assertIn("B2 was not run", text)


class BaselineCliTests(unittest.TestCase):
    def test_check_command_returns_zero_for_valid_manifest(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            write_valid_baseline_artifacts(root)
            baseline.write_json(
                root / "manifest.json",
                baseline.build_baseline_manifest(root, "BASELINE_COMPLETE"),
            )
            self.assertEqual(
                baseline.main([
                    "check", "--config", str(root / "config.json"), "--out", str(root)
                ]),
                0,
            )



class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="preflightcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.corpus = self.work / "corpus"
        (self.corpus / "train").mkdir(parents=True)
        for name in ("notes.jsonl", "queries.jsonl"):
            (self.corpus / "train" / name).write_bytes(b"")
        empty_hash = hashlib.sha256(b"").hexdigest()
        baseline.write_json(self.corpus / "manifest.json", {
            "corpus_version": "v2", "frozen": True, "corpus_hash": "abc123",
            "counts": {"train": {"notes": 0, "queries": 0}},
            "file_hashes": {
                "train/notes.jsonl": empty_hash,
                "train/queries.jsonl": empty_hash,
            },
        })
        self.potion = self.work / "potion"
        self.potion.mkdir()
        (self.potion / "model.bin").write_bytes(b"potion")
        self.config = self.work / "config.json"
        baseline.write_json(self.config, {
            "baseline_version": "v1",
            "corpus": {
                "root": "corpus", "corpus_version": "v2",
                "corpus_hash": "abc123", "splits": ["train", "dev"],
            },
            "dependencies": {
                "python": "3.12", "numpy": "1.0", "model2vec": "1.0",
                "tokenizers": "1.0", "onnxruntime": "1.0",
            },
            "potion_files": {
                "model.bin": hashlib.sha256(b"potion").hexdigest(),
            },
        })

    def test_train_preflight_verifies_only_the_allowed_split_and_potion(self):
        got = baseline.preflight_inputs(
            self.config, "train",
            package_version=lambda _name: "1.0",
            python_version=lambda: "3.12.9",
            potion_dir=self.potion,
        )
        self.assertTrue(got["corpus_manifest"]["frozen"])
        self.assertEqual(got["corpus_manifest"]["counts"]["train"]["queries"], 0)
        self.assertEqual(got["potion_files"]["model.bin"], hashlib.sha256(b"potion").hexdigest())

    def test_preflight_rejects_missing_frozen_marker(self):
        manifest = json.loads((self.corpus / "manifest.json").read_text(encoding="utf-8"))
        manifest.pop("frozen")
        baseline.write_json(self.corpus / "manifest.json", manifest)
        with self.assertRaisesRegex(baseline.SetupError, "frozen"):
            baseline.preflight_inputs(
                self.config, "train",
                package_version=lambda _name: "1.0",
                python_version=lambda: "3.12.9",
                potion_dir=self.potion,
            )


class DevExecutionTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="devcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        source = Path(__file__).parent / "fixtures/vector-semantics/baseline-v1/config.json"
        self.config = self.work / "config.json"
        shutil.copy2(source, self.config)
        train_hash = baseline.write_json(self.work / "train-report.json", {"status": "POLICY_SELECTED"})
        config_hash = hashlib.sha256(self.config.read_bytes()).hexdigest()
        baseline.write_json(self.work / "selected-policy.json", {
            "baseline_version": "v1", "config_sha256": config_hash,
            "corpus_hash": "f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3",
            "train_report_sha256": train_hash, "gate_errors": [],
            "selected": {
                "policy_id": "unicode_nfc-16-4", "tokenizer": "unicode_nfc",
                "lexical_reserve": 16, "semantic_reserve": 4,
            },
        })
        baseline.write_json(self.work / "model-manifest.json", {"revision": "pinned"})
        self.split = {
            "notes": [
                {"id": "n-gold", "text": "Restore access after verification.", "language": "en"},
                {"id": "n-hard", "text": "Block access pending review.", "language": "en"},
                {"id": "n-other", "text": "Unrelated storage operation.", "language": "en"},
            ],
            "queries": [{
                "id": "q-1", "text": "Let the verified user enter again", "language": "en",
                "scenario_group": "g-1", "provenance": "synthetic-contrast",
                "contrast_families": ["permit-vs-prohibit"], "gold": ["n-gold"],
                "hard_negatives": [{"note_id": "n-hard", "contrast_family": "permit-vs-prohibit"}],
                "easy_negatives": ["n-other"], "rationale": "permit versus prohibit",
            }],
        }
        self.candidate_report = {
            "policy_id": "unicode_nfc-16-4", "tokenizer": "unicode_nfc",
            "lexical_reserve": 16, "semantic_reserve": 4,
            "overall": {"queries": 1, "hit_at_20": 1},
            "safety": {"queries": 1, "hit_at_20": 1},
            "identifier": {"queries": 0, "hit_at_20": 0},
            "protected_dropped": 0, "gate_errors": [],
            "queries": [{
                "query_id": "q-1", "candidates": ["n-hard", "n-gold", "n-other"],
                "protected": [], "hit_at_20": True, "safety": True, "identifier": False,
            }],
        }

    def test_dev_runs_b2_twenty_times_and_writes_complete_report(self):
        calls = []

        class FakeReranker:
            def score(self, _query, passages):
                calls.append(tuple(passages))
                return [0.1, 0.9, 0.0]

        result = baseline.run_dev(
            self.config,
            self.work,
            self.work / "model-manifest.json",
            preflight_fn=lambda *_args, **_kwargs: {
                "corpus_manifest": {"counts": {"dev": {"notes": 3, "queries": 1}}}
            },
            load_split=lambda _root, _split, certified_run=False: self.split,
            evaluate_policy=lambda *_args: dict(self.candidate_report),
            reranker_factory=lambda *_args: FakeReranker(),
            metadata_fn=lambda **_kwargs: {"git_commit": "abc123"},
        )
        self.assertEqual(result["status"], "BASELINE_COMPLETE")
        self.assertEqual(len(calls), 20)
        report = json.loads((self.work / "dev-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["b2"]["metrics"]["overall"]["hit_at_1"], 1)
        self.assertRegex(report["b2"]["determinism"]["ranking_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            json.loads((self.work / "manifest.json").read_text(encoding="utf-8"))["status"],
            "BASELINE_COMPLETE",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
