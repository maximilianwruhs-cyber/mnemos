# scripts/test_vecidx.py
import importlib.util, sys
from pathlib import Path
import unittest
import math
import tempfile, json, shutil

MODULE = Path(__file__).with_name("vecidx.py")
spec = importlib.util.spec_from_file_location("vecidx", MODULE)
vecidx = importlib.util.module_from_spec(spec)
sys.modules["vecidx"] = vecidx
spec.loader.exec_module(vecidx)


class AvailabilityTests(unittest.TestCase):
    def test_available_is_boolean(self):
        self.assertIn(vecidx.available(), (True, False))

    def test_model_dir_is_under_scripts_vectors(self):
        self.assertEqual(vecidx.MODEL_DIR.name, "potion-base-8M")
        self.assertEqual(vecidx.MODEL_DIR.parent.name, "vectors")


@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class EmbedTests(unittest.TestCase):
    def test_content_hash_is_stable_and_stdlib(self):
        self.assertEqual(vecidx.content_hash("abc"), vecidx.content_hash("abc"))
        self.assertNotEqual(vecidx.content_hash("abc"), vecidx.content_hash("abd"))
        self.assertEqual(len(vecidx.content_hash("abc")), 64)

    def test_query_embedding_is_unit_norm(self):
        v = vecidx.embed_query("sandbox has no network egress")
        self.assertGreater(v.shape[0], 0)
        self.assertAlmostEqual(float((v * v).sum()) ** 0.5, 1.0, places=4)

    def test_similar_text_scores_higher_than_unrelated(self):
        q = vecidx.embed_query("cannot reach the internet from the sandbox")
        m = vecidx.embed_texts([
            "sockets to external hosts raise OSError; no outbound network",
            "the third floor coffee machine is broken",
        ])
        near, far = float(m[0] @ q), float(m[1] @ q)
        self.assertGreater(near, far)


@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class IndexTests(unittest.TestCase):
    def _docs(self):
        return {
            "/Memory/a.md": {"title": "no network", "text": "sockets raise OSError"},
            "/Memory/b.md": {"title": "read only out dir", "text": "writes fail EROFS"},
        }

    def test_build_then_load_roundtrips_keys(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        stats = vecidx.build(d, docs=self._docs())
        self.assertEqual(stats["built"], 2)
        keys, mat = vecidx.load(d)
        self.assertEqual(set(keys), {"/Memory/a.md", "/Memory/b.md"})
        self.assertEqual(mat.shape[0], 2)

    def test_unchanged_docs_are_reused_not_reembedded(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        vecidx.build(d, docs=self._docs())
        stats = vecidx.build(d, docs=self._docs())
        self.assertEqual(stats["reused"], 2)
        self.assertEqual(stats["built"], 0)

    def test_changed_doc_reembeds_and_deleted_doc_prunes(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        vecidx.build(d, docs=self._docs())
        docs = {"/Memory/a.md": {"title": "no network", "text": "CHANGED body now"}}
        stats = vecidx.build(d, docs=docs)
        self.assertEqual(stats["built"], 1)
        self.assertEqual(stats["pruned"], 1)
        keys, _ = vecidx.load(d)
        self.assertEqual(keys, ["/Memory/a.md"])

    def test_load_missing_index_returns_none(self):
        d = tempfile.mkdtemp(dir="/tmp"); self.addCleanup(lambda: shutil.rmtree(d, True))
        self.assertIsNone(vecidx.load(d))


@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class SearchTests(unittest.TestCase):
    def test_floor_excludes_unrelated_and_keeps_related(self):
        keys = ["/rel", "/unrel"]
        mat = vecidx.embed_texts([
            "no outbound network; sockets raise OSError",
            "coffee machine broken on floor three",
        ])
        hits = vecidx.search_vectors("cannot reach the internet", keys, mat, floor=0.2)
        self.assertIn("/rel", hits)
        self.assertGreaterEqual(hits["/rel"], 0.2)
        self.assertNotIn("/unrel", [k for k, v in hits.items() if v >= 0.5])


@unittest.skipUnless(vecidx.available(), "vector model not fetched (Task 8)")
class BQTests(unittest.TestCase):
    def test_prefilter_keeps_the_true_neighbour(self):
        import numpy as np
        texts = ["no outbound network sockets oserror"] + \
                [f"unrelated filler topic number {i}" for i in range(20)]
        mat = vecidx.embed_texts(texts)
        qbits = vecidx.pack_bq(vecidx.embed_query("cannot reach internet")[None, :])[0]
        dbits = vecidx.pack_bq(mat)
        keep = vecidx.bq_prefilter(qbits, dbits, k=5)
        self.assertIn(0, keep)  # the true neighbour survives the coarse pass
        self.assertEqual(len(keep), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
