# scripts/test_vecidx.py
import importlib.util, sys
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
