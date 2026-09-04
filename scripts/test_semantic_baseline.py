#!/usr/bin/env python3
"""Behavioral tests for the staged semantic-recall baseline."""
from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
