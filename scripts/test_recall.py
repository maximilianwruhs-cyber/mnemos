#!/usr/bin/env python3
"""Regression tests for recall.py against real temporary corpora.

Complements recall.py --selftest: the self-test proves the happy path, these
tests pin the properties that would silently rot - idf ordering, hop decay,
tie-break totality, and refusal to invent a hit when nothing matches.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path("/tmp/recall.py")
spec = importlib.util.spec_from_file_location("recall", MODULE_PATH)
recall = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = recall
spec.loader.exec_module(recall)


class RecallTests(unittest.TestCase):
    def corpus(self, files, manifest=None):
        work = tempfile.mkdtemp(prefix="recallcase_", dir="/tmp")
        self.addCleanup(lambda: __import__("shutil").rmtree(work, ignore_errors=True))
        for name, body in files.items():
            Path(work, name).write_text(body, encoding="utf-8")
        mapping = manifest or {n: f"/Memory/context/{n}" for n in files}
        Path(work, "_manifest.json").write_text(json.dumps(mapping), encoding="utf-8")
        return recall.build_corpus(work)

    def test_rare_term_outranks_common_term(self):
        docs = self.corpus({
            "a.md": "# a\nwidget widget widget common common common\n",
            "b.md": "# b\ncommon common common common common common\n",
            "c.md": "# c\ncommon common common\n",
        })
        hits = recall.search("widget", docs)
        self.assertTrue(hits)
        self.assertTrue(hits[0]["path"].endswith("a.md"), hits)

    def test_graph_hop_decay_orders_neighbours(self):
        docs = self.corpus({
            "seed.md": "# MEM-2026-0001 seed\nkryptonite lives here.\n",
            "one.md": "# MEM-2026-0002 one\nrefers to MEM-2026-0001 only.\n",
            "two.md": "# MEM-2026-0003 two\nrefers to MEM-2026-0002 only.\n",
        })
        hits = {h["path"]: h for h in recall.search("kryptonite", docs, limit=10)}
        self.assertIn("/Memory/context/one.md", hits)
        self.assertIn("/Memory/context/two.md", hits)
        self.assertGreater(hits["/Memory/context/one.md"]["graph"],
                           hits["/Memory/context/two.md"]["graph"])

    def test_hop_limit_is_enforced(self):
        docs = self.corpus({
            "seed.md": "# MEM-2026-0001 seed\nkryptonite.\n",
            "one.md": "# MEM-2026-0002\nsee MEM-2026-0001.\n",
            "two.md": "# MEM-2026-0003\nsee MEM-2026-0002.\n",
            "three.md": "# MEM-2026-0004\nsee MEM-2026-0003.\n",
        })
        paths = [h["path"] for h in recall.search("kryptonite", docs, limit=10)]
        self.assertNotIn("/Memory/context/three.md", paths)

    def test_no_match_returns_nothing(self):
        docs = self.corpus({"a.md": "# a\nnothing relevant here.\n"})
        self.assertEqual(recall.search("zzzzunmatchable", docs), [])

    def test_empty_query_raises(self):
        docs = self.corpus({"a.md": "# a\ncontent.\n"})
        with self.assertRaises(ValueError):
            recall.search("!!! ...", docs)

    def test_ordering_is_total_under_score_ties(self):
        body = "# t\nidentical identical identical\n"
        docs = self.corpus({"b.md": body, "a.md": body, "c.md": body})
        first = [h["path"] for h in recall.search("identical", docs, limit=10)]
        second = [h["path"] for h in recall.search("identical", docs, limit=10)]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))

    def test_l2_notes_are_split_into_separate_documents(self):
        memory = (
            "# MEMORY.md\n\n## 2. Atomic Notes\n\n"
            "### [MEM-2026-0001] first topic\n\n- **Stub.** alpha content.\n\n"
            "### [MEM-2026-0002] second topic\n\n- **Stub.** beta content.\n\n"
            "## 3. Ephemeral Scratchpad\n\nignored tail.\n"
        )
        docs = self.corpus({"MEMORY.md": memory}, manifest={"MEMORY.md": "/MEMORY.md"})
        self.assertEqual(set(docs), {"MEM-2026-0001", "MEM-2026-0002"})
        hits = recall.search("beta", docs)
        self.assertEqual(hits[0]["id"], "MEM-2026-0002")

    def test_archive_and_generated_indexes_are_excluded(self):
        docs = self.corpus(
            {"old.md": "# old\nkryptonite.\n", "INDEX.md": "# idx\nkryptonite.\n"},
            manifest={"old.md": "/Memory/_archive/old.md",
                      "INDEX.md": "/Memory/INDEX.md"},
        )
        self.assertEqual(docs, {})

    def test_salience_field_is_honoured(self):
        docs = self.corpus({
            "hi.md": "# hi\n- **Salience:** 0.90\nmatch token\n",
            "lo.md": "# lo\n- **Salience:** 0.10\nmatch token\n",
        })
        hits = recall.search("match token", docs)
        self.assertEqual(hits[0]["path"], "/Memory/context/hi.md")

    def test_selftest_entrypoint_passes(self):
        self.assertEqual(recall.selftest(), 0)

    def test_search_without_stage_is_unchanged(self):
        docs = self.corpus({"a.md": "# a\nwidget widget common\n",
                            "b.md": "# b\ncommon common common\n"})
        rows = recall.search("widget", docs)
        self.assertTrue(rows[0]["path"].endswith("a.md"))
        self.assertNotIn("vec", rows[0])  # legacy shape untouched when stage is None

    def test_vec_disabled_env_forces_lexical(self):
        import os
        os.environ["RECALL_VEC"] = "0"
        self.addCleanup(lambda: os.environ.pop("RECALL_VEC", None))
        self.assertFalse(recall._vec_enabled(100000))

    def test_vec_activation_threshold(self):
        import os
        os.environ.pop("RECALL_VEC", None)
        self.assertFalse(recall._vec_enabled(10))
        self.assertTrue(recall._vec_enabled(recall.VEC_ACTIVATE_N))

    def test_hybrid_surfaces_paraphrase_only_note(self):
        import os, importlib.util, sys, shutil, json, tempfile
        prev = sys.modules.get("vecidx")
        vspec = importlib.util.spec_from_file_location(
            "vecidx", Path(__file__).resolve().with_name("vecidx.py"))
        vecidx = importlib.util.module_from_spec(vspec)
        sys.modules["vecidx"] = vecidx
        def _restore_vecidx():
            if prev is None:
                sys.modules.pop("vecidx", None)
            else:
                sys.modules["vecidx"] = prev
        self.addCleanup(_restore_vecidx)
        vspec.loader.exec_module(vecidx)
        if not vecidx.available():
            self.skipTest("vector model not fetched (Task 8)")
        work = tempfile.mkdtemp(prefix="hybrid_", dir="/tmp")
        self.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
        files = {
            "net.md": "# net\nSockets to external hosts raise OSError; no outbound egress.\n",
            "cof.md": "# cof\nThe floor-three coffee machine is broken.\n",
        }
        for n, b in files.items():
            Path(work, n).write_text(b, encoding="utf-8")
        Path(work, "_manifest.json").write_text(
            json.dumps({n: f"/Memory/{n}" for n in files}), encoding="utf-8")
        docs = recall.build_corpus(work)
        vecidx.build(work, docs=docs)
        os.environ["RECALL_VEC"] = "1"
        self.addCleanup(lambda: os.environ.pop("RECALL_VEC", None))
        # query shares NO content token with net.md ("egress"/"sockets" absent)
        hits = recall.search("cannot reach the internet", docs, stage=work)
        self.assertTrue(hits and hits[0]["path"] == "/Memory/net.md", hits)
        self.assertGreater(hits[0]["vec"], 0.0)

    def test_empty_vec_hits_preserve_lexical_order(self):
        """Index present + RECALL_VEC=1 but no cosine above floor → same as stage=None."""
        import os, importlib.util, sys, shutil, json, tempfile
        prev = sys.modules.get("vecidx")
        vspec = importlib.util.spec_from_file_location(
            "vecidx", Path(__file__).resolve().with_name("vecidx.py"))
        vecidx = importlib.util.module_from_spec(vspec)
        sys.modules["vecidx"] = vecidx
        def _restore_vecidx():
            if prev is None:
                sys.modules.pop("vecidx", None)
            else:
                sys.modules["vecidx"] = prev
        self.addCleanup(_restore_vecidx)
        vspec.loader.exec_module(vecidx)
        if not vecidx.available():
            self.skipTest("vector model not fetched (Task 8)")
        work = tempfile.mkdtemp(prefix="emptyvec_", dir="/tmp")
        self.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
        files = {
            "a.md": "# a\nwidget widget common\n",
            "b.md": "# b\ncommon common common\n",
            "c.md": "# c\nunrelated filler text about coffee machines\n",
        }
        for n, b in files.items():
            Path(work, n).write_text(b, encoding="utf-8")
        Path(work, "_manifest.json").write_text(
            json.dumps({n: f"/Memory/{n}" for n in files}), encoding="utf-8")
        docs = recall.build_corpus(work)
        vecidx.build(work, docs=docs)
        # Force zero above-floor hits: search_vectors only returns cosines >= VEC_FLOOR;
        # empty dict is the observed outcome for non-matching paraphrases and is what
        # must leave blend weights at the legacy W_* set (exact-match non-regression).
        vecidx.search_vectors = lambda *a, **k: {}
        os.environ["RECALL_VEC"] = "1"
        self.addCleanup(lambda: os.environ.pop("RECALL_VEC", None))
        base = recall.search("widget", docs)
        hybrid = recall.search("widget", docs, stage=work)
        self.assertEqual([r["path"] for r in base], [r["path"] for r in hybrid])
        self.assertEqual([r["id"] for r in base], [r["id"] for r in hybrid])
        for r in hybrid:
            self.assertNotIn("vec", r)



if __name__ == "__main__":
    unittest.main(verbosity=2)
