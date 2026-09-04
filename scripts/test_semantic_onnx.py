#!/usr/bin/env python3
"""Behavioral tests for pinned model provisioning and ONNX reranking."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fetch_reranker
import semantic_onnx


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fetchcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_fetch_writes_only_verified_bytes(self):
        payload = b"model-bytes"
        model_spec = {
            "repository": "owner/model",
            "revision": "abc123",
            "files": {
                "model.onnx": {
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            },
        }
        urls = []

        def opener(url):
            urls.append(url)
            return io.BytesIO(payload)

        manifest = fetch_reranker.fetch_files(model_spec, self.root, opener=opener)
        self.assertEqual((self.root / "model.onnx").read_bytes(), payload)
        self.assertEqual(
            manifest["files"]["model.onnx"]["sha256"],
            model_spec["files"]["model.onnx"]["sha256"],
        )
        self.assertEqual(
            urls,
            ["https://huggingface.co/owner/model/resolve/abc123/model.onnx"],
        )

    def test_hash_mismatch_leaves_no_partial_file(self):
        model_spec = {
            "repository": "owner/model",
            "revision": "abc123",
            "files": {"model.onnx": {"bytes": 3, "sha256": "0" * 64}},
        }
        with self.assertRaisesRegex(ValueError, "sha256"):
            fetch_reranker.fetch_files(
                model_spec, self.root, opener=lambda _url: io.BytesIO(b"bad")
            )
        self.assertFalse((self.root / "model.onnx").exists())

    def test_existing_file_is_rehashed_before_reuse(self):
        payload = b"model-bytes"
        expected = hashlib.sha256(payload).hexdigest()
        (self.root / "model.onnx").write_bytes(b"wrong-bytes")
        model_spec = {
            "repository": "owner/model",
            "revision": "abc123",
            "files": {"model.onnx": {"bytes": len(payload), "sha256": expected}},
        }
        fetch_reranker.fetch_files(
            model_spec, self.root, opener=lambda _url: io.BytesIO(payload)
        )
        self.assertEqual((self.root / "model.onnx").read_bytes(), payload)


class RerankTests(unittest.TestCase):
    def test_only_unprotected_candidates_are_scored(self):
        seen = []

        def scorer(query, passages):
            seen.extend(passages)
            return [0.2, 0.9]

        got = semantic_onnx.rerank(
            "q",
            ["protected", "a", "b"],
            {"protected": "p", "a": "a", "b": "b"},
            {"protected"},
            scorer,
        )
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual([note_id for note_id, _score in got], ["protected", "b", "a"])
        self.assertIsNone(got[0][1])

    def test_equal_logits_preserve_b1_order(self):
        got = semantic_onnx.rerank(
            "q",
            ["b", "a"],
            {"a": "A", "b": "B"},
            set(),
            lambda query, passages: [1.0, 1.0],
        )
        self.assertEqual([note_id for note_id, _score in got], ["b", "a"])

    def test_wrong_score_count_fails_closed(self):
        with self.assertRaisesRegex(semantic_onnx.OnnxSetupError, "score count"):
            semantic_onnx.rerank(
                "q", ["a", "b"], {"a": "A", "b": "B"}, set(),
                lambda query, passages: [1.0],
            )


class FakeInput:
    def __init__(self, name):
        self.name = name


class FakeSession:
    def __init__(self, input_names=("input_ids", "attention_mask")):
        self.input_names = input_names
        self.feed = None

    def get_inputs(self):
        return [FakeInput(name) for name in self.input_names]

    def run(self, _outputs, feed):
        self.feed = feed
        batch = len(feed["input_ids"])
        return [np.asarray([[0.25]] * batch, dtype="float32")]


class FakeEncoding:
    ids = [0, 10, 2, 2, 20, 2]
    attention_mask = [1, 1, 1, 1, 1, 1]
    type_ids = [0, 0, 0, 1, 1, 1]


class FakeTokenizer:
    def __init__(self):
        self.truncation = None
        self.padding = None
        self.pairs = None

    def enable_truncation(self, **kwargs):
        self.truncation = kwargs

    def enable_padding(self, **kwargs):
        self.padding = kwargs

    def encode_batch(self, pairs):
        self.pairs = pairs
        return [FakeEncoding() for _pair in pairs]


class RuntimeBoundaryTests(unittest.TestCase):
    def test_pair_order_truncation_and_graph_feed(self):
        tokenizer, session = FakeTokenizer(), FakeSession()
        reranker = semantic_onnx.OnnxReranker(tokenizer, session, max_length=512)
        self.assertEqual(reranker.score("query", ["passage"]), [0.25])
        self.assertEqual(tokenizer.pairs, [("query", "passage")])
        self.assertEqual(
            tokenizer.truncation, {"max_length": 512, "strategy": "only_second"}
        )
        self.assertEqual(tokenizer.padding["pad_id"], 1)
        self.assertEqual(set(session.feed), {"input_ids", "attention_mask"})
        self.assertEqual(session.feed["input_ids"].dtype, np.int64)

    def test_optional_token_type_ids_use_encoding_values(self):
        session = FakeSession(("input_ids", "attention_mask", "token_type_ids"))
        reranker = semantic_onnx.OnnxReranker(FakeTokenizer(), session, max_length=512)
        reranker.score("query", ["passage"])
        self.assertEqual(
            session.feed["token_type_ids"].tolist(), [[0, 0, 0, 1, 1, 1]]
        )

    def test_unknown_required_graph_input_fails_closed(self):
        with self.assertRaisesRegex(semantic_onnx.OnnxSetupError, "mystery_input"):
            semantic_onnx.OnnxReranker(
                FakeTokenizer(), FakeSession(("input_ids", "mystery_input")), max_length=512
            )


class EnvironmentVerificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="modelcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        payload = b"verified-model"
        (self.root / "model.onnx").write_bytes(payload)
        self.config = {
            "dependencies": {"onnxruntime": "1.20.1", "tokenizers": "0.21.0"},
            "reranker": {
                "provider": "CPUExecutionProvider",
                "files": {
                    "model.onnx": {
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            },
        }

    def test_verified_environment_returns_file_and_provider_identity(self):
        got = semantic_onnx.verify_environment(
            self.config,
            self.root,
            package_version=lambda name: self.config["dependencies"][name],
            available_providers=lambda: ["CPUExecutionProvider"],
        )
        self.assertEqual(got["provider"], "CPUExecutionProvider")
        self.assertEqual(
            got["files"]["model.onnx"],
            self.config["reranker"]["files"]["model.onnx"],
        )

    def test_tampered_model_is_rejected(self):
        (self.root / "model.onnx").write_bytes(b"tampered")
        with self.assertRaisesRegex(semantic_onnx.OnnxSetupError, "model.onnx"):
            semantic_onnx.verify_environment(
                self.config,
                self.root,
                package_version=lambda name: self.config["dependencies"][name],
                available_providers=lambda: ["CPUExecutionProvider"],
            )


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scripts/fixtures/vector-semantics/baseline-v1/config.json"
REAL_MODEL = ROOT / ".cache/semantic-baseline-v1/mmarco/onnx/model_quint8_avx2.onnx"


@unittest.skipUnless(
    importlib.util.find_spec("onnxruntime") is not None and REAL_MODEL.exists(),
    "pinned ONNX runtime/model unavailable",
)
class RealModelSmokeTests(unittest.TestCase):
    def test_real_model_returns_one_finite_score_per_pair(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        reranker = semantic_onnx.OnnxReranker.load(
            ROOT / config["reranker"]["model_dir"], config["reranker"]["max_length"]
        )
        scores = reranker.score("How do I restore access?", ["Restore the account.", "Block access."])
        self.assertEqual(len(scores), 2)
        self.assertTrue(all(np.isfinite(score) for score in scores))


if __name__ == "__main__":
    unittest.main(verbosity=2)
