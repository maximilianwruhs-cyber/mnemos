# scripts/test_vecidx.py
import importlib.util, sys
from pathlib import Path
import unittest
import math

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
