#!/usr/bin/env python3
"""Pinned, CPU-only ONNX boundary for the semantic baseline."""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import platform
from pathlib import Path
from typing import Callable


MODEL_RELATIVE_PATH = Path("onnx/model_quint8_avx2.onnx")
TOKENIZER_RELATIVE_PATH = Path("tokenizer.json")
_ALLOWED_INPUTS = frozenset({"input_ids", "attention_mask", "token_type_ids"})
_REQUIRED_INPUTS = frozenset({"input_ids", "attention_mask"})


class OnnxSetupError(RuntimeError):
    """The pinned ONNX runtime or artifact contract is unavailable."""


def _identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def verify_environment(
    config: dict,
    model_dir: Path,
    *,
    package_version: Callable[[str], str] = importlib.metadata.version,
    available_providers: Callable[[], list[str]] | None = None,
) -> dict:
    model_dir = Path(model_dir)
    identities = {}
    for relative_path, expected in sorted(config["reranker"]["files"].items()):
        path = model_dir / relative_path
        if not path.is_file():
            raise OnnxSetupError(f"missing model file: {relative_path}")
        actual = _identity(path)
        if actual != expected:
            raise OnnxSetupError(
                f"{relative_path}: artifact identity {actual} != {expected}"
            )
        identities[relative_path] = actual

    versions = {}
    for name, expected in sorted(config["dependencies"].items()):
        if name == "python":
            actual = ".".join(platform.python_version().split(".")[:2])
        else:
            try:
                actual = package_version(name)
            except importlib.metadata.PackageNotFoundError as exc:
                raise OnnxSetupError(f"missing package: {name}") from exc
        if actual != expected:
            raise OnnxSetupError(f"{name} version {actual} != {expected}")
        versions[name] = actual

    if available_providers is None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise OnnxSetupError("missing package: onnxruntime") from exc
        available_providers = ort.get_available_providers
    provider = config["reranker"]["provider"]
    if provider not in available_providers():
        raise OnnxSetupError(f"provider unavailable: {provider}")
    return {"files": identities, "dependencies": versions, "provider": provider}


class OnnxReranker:
    def __init__(self, tokenizer, session, max_length: int):
        self.tokenizer = tokenizer
        self.session = session
        self.max_length = max_length
        self.tokenizer.enable_truncation(
            max_length=max_length, strategy="only_second"
        )
        self.tokenizer.enable_padding(pad_id=1, pad_type_id=0, pad_token="<pad>")
        self.input_names = tuple(item.name for item in session.get_inputs())
        unknown = set(self.input_names).difference(_ALLOWED_INPUTS)
        missing = _REQUIRED_INPUTS.difference(self.input_names)
        if unknown:
            raise OnnxSetupError(f"unknown ONNX inputs: {sorted(unknown)}")
        if missing:
            raise OnnxSetupError(f"missing ONNX inputs: {sorted(missing)}")

    @classmethod
    def load(cls, model_dir: Path, max_length: int):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise OnnxSetupError(f"missing reranker dependency: {exc.name}") from exc

        if "CPUExecutionProvider" not in ort.get_available_providers():
            raise OnnxSetupError("provider unavailable: CPUExecutionProvider")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        model_dir = Path(model_dir)
        tokenizer = Tokenizer.from_file(str(model_dir / TOKENIZER_RELATIVE_PATH))
        session = ort.InferenceSession(
            str(model_dir / MODEL_RELATIVE_PATH),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        return cls(tokenizer, session, max_length)

    def score(
        self, query: str, passages: list[str], batch_size: int = 16
    ) -> list[float]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not passages:
            return []
        import numpy as np

        scores = []
        for offset in range(0, len(passages), batch_size):
            batch = passages[offset:offset + batch_size]
            encodings = self.tokenizer.encode_batch([(query, passage) for passage in batch])
            arrays = {
                "input_ids": np.asarray([item.ids for item in encodings], dtype="int64"),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encodings], dtype="int64"
                ),
                "token_type_ids": np.asarray(
                    [item.type_ids for item in encodings], dtype="int64"
                ),
            }
            feed = {name: arrays[name] for name in self.input_names}
            outputs = self.session.run(None, feed)
            if len(outputs) != 1:
                raise OnnxSetupError(f"expected one ONNX output, got {len(outputs)}")
            batch_scores = np.asarray(outputs[0]).reshape(-1)
            if len(batch_scores) != len(batch):
                raise OnnxSetupError(
                    f"score count {len(batch_scores)} != passage count {len(batch)}"
                )
            scores.extend(float(score) for score in batch_scores)
        return scores


def rerank(
    query: str,
    candidate_ids: list[str],
    note_texts: dict[str, str],
    protected_ids: set[str],
    scorer: Callable[[str, list[str]], list[float]],
) -> list[tuple[str, float | None]]:
    if len(candidate_ids) != len(set(candidate_ids)):
        raise OnnxSetupError("duplicate candidate ID")
    protected = [note_id for note_id in candidate_ids if note_id in protected_ids]
    unprotected = [note_id for note_id in candidate_ids if note_id not in protected_ids]
    try:
        passages = [note_texts[note_id] for note_id in unprotected]
    except KeyError as exc:
        raise OnnxSetupError(f"missing candidate text: {exc.args[0]}") from exc
    scores = scorer(query, passages)
    if len(scores) != len(unprotected):
        raise OnnxSetupError(
            f"score count {len(scores)} != candidate count {len(unprotected)}"
        )
    indexed = []
    for index, (note_id, score) in enumerate(zip(unprotected, scores)):
        score = float(score)
        if not math.isfinite(score):
            raise OnnxSetupError(f"non-finite score for {note_id}")
        indexed.append((note_id, score, index))
    indexed.sort(key=lambda item: (-item[1], item[2], item[0]))
    return (
        [(note_id, None) for note_id in protected]
        + [(note_id, score) for note_id, score, _index in indexed]
    )
