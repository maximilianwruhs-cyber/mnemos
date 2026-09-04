#!/usr/bin/env python3
"""Behavioral tests for the staged semantic-recall baseline."""
from __future__ import annotations

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

if __name__ == "__main__":
    unittest.main(verbosity=2)
