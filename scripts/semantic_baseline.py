#!/usr/bin/env python3
"""Orchestrate the reproducible train/dev semantic-recall baseline."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import corpus_lint
import recall
import semantic_onnx
import vecidx
from semantic_baseline_core import (
    CANDIDATE_K,
    TOKENIZER_NAMES,
    BaselineError,
    SetupError,
    adapt_notes,
    aggregate_results,
    candidate_gate,
    canonical_json_bytes,
    cluster_interval,
    compare_results,
    evaluate_all_candidate_policies,
    evaluate_candidate_policy,
    identifier_tokens,
    paired_cluster_interval,
    policy_grid,
    protected_note_ids,
    rank_bm25,
    rank_potion,
    score_query,
    score_rerank,
    select_policy,
    unicode_tokens,
    union_candidates,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPLETE_FILES = (
    "config.json",
    "dev-report.json",
    "model-manifest.json",
    "selected-policy.json",
    "train-report.json",
)


def load_baseline_split(
    root: Path, split: str, loader=corpus_lint.load_split
) -> dict:
    if split not in {"train", "dev"}:
        raise BaselineError(f"baseline may not open split: {split}")
    return loader(root, split, certified_run=False)


def validate_query_coverage(
    expected_ids: list[str], results: dict[str, dict]
) -> None:
    if len(expected_ids) != len(set(expected_ids)):
        raise SetupError("duplicate expected query ID")
    missing = sorted(set(expected_ids).difference(results))
    unexpected = sorted(set(results).difference(expected_ids))
    if missing or unexpected:
        raise SetupError(
            f"query coverage mismatch; missing={missing}, unexpected={unexpected}"
        )


def validate_split_counts(split_data: dict, expected: dict, split: str) -> None:
    for kind in ("notes", "queries"):
        actual = len(split_data[kind])
        if actual != expected[kind]:
            raise SetupError(f"{split} {kind} {actual} != {expected[kind]}")


def assert_repeatable_rankings(runs: list[dict[str, list[str]]]) -> str:
    if len(runs) != 20:
        raise SetupError(f"determinism requires 20 runs, got {len(runs)}")
    expected = canonical_json_bytes(runs[0])
    for index, run in enumerate(runs[1:], 2):
        if canonical_json_bytes(run) != expected:
            raise SetupError(f"determinism mismatch on run {index}")
    return hashlib.sha256(expected).hexdigest()


def require_new_output(path: Path) -> None:
    if Path(path).exists():
        raise SetupError(f"output already exists: {path}")


def current_git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def environment_metadata(
    package_names: tuple[str, ...],
    provider: str | None,
    *,
    package_version=importlib.metadata.version,
    git_commit=current_git_commit,
    platform_info=platform.platform,
    cpu_name=platform.processor,
) -> dict:
    return {
        "git_commit": git_commit(),
        "platform": platform_info(),
        "python": platform.python_version(),
        "cpu": cpu_name(),
        "dependencies": {
            name: package_version(name) for name in sorted(package_names)
        },
        "provider": provider,
    }


def _config(path: Path) -> tuple[dict, str]:
    path = Path(path)
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SetupError(f"invalid config: {exc}") from exc
    if payload != canonical_json_bytes(value):
        raise SetupError("config is not canonical JSON")
    return value, hashlib.sha256(payload).hexdigest()


def _corpus_root(config_path: Path, config: dict) -> Path:
    return (Path(config_path).parent / config["corpus"]["root"]).resolve()


def _sha256_file(path: Path) -> str:
    try:
        with Path(path).open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except FileNotFoundError as exc:
        raise SetupError(f"missing file: {path}") from exc


def _read_json_file(path: Path, label: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SetupError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SetupError(f"invalid {label}: {exc}") from exc


def preflight_inputs(
    config_path: Path,
    stage: str,
    model_manifest: Path | None = None,
    *,
    package_version=importlib.metadata.version,
    python_version=platform.python_version,
    potion_dir: Path | None = None,
    onnx_verifier=semantic_onnx.verify_environment,
) -> dict:
    if stage not in {"train", "dev"}:
        raise SetupError(f"unknown baseline stage: {stage}")
    config, config_sha256 = _config(config_path)
    cert_errors = corpus_lint.check_no_cert_reference(config_path)
    if cert_errors:
        raise SetupError("; ".join(cert_errors))

    actual_python = ".".join(python_version().split(".")[:2])
    if actual_python != config["dependencies"]["python"]:
        raise SetupError(
            f"python version {actual_python} != {config['dependencies']['python']}"
        )
    required_packages = ["numpy", "model2vec", "tokenizers"]
    if stage == "dev":
        required_packages.append("onnxruntime")
    versions = {}
    for name in required_packages:
        try:
            actual = package_version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SetupError(f"missing package: {name}") from exc
        expected = config["dependencies"][name]
        if actual != expected:
            raise SetupError(f"{name} version {actual} != {expected}")
        versions[name] = actual

    corpus_root = _corpus_root(config_path, config)
    corpus_manifest = _read_json_file(corpus_root / "manifest.json", "corpus manifest")
    for key, expected in (
        ("corpus_version", config["corpus"]["corpus_version"]),
        ("corpus_hash", config["corpus"]["corpus_hash"]),
        ("frozen", True),
    ):
        if corpus_manifest.get(key) != expected:
            raise SetupError(
                f"corpus manifest {key} {corpus_manifest.get(key)!r} != {expected!r}"
            )
    for kind in ("notes", "queries"):
        relative = f"{stage}/{kind}.jsonl"
        path = corpus_root / relative
        if not path.is_file():
            raise SetupError(f"missing corpus file: {relative}")
        actual = _sha256_file(path)
        expected = corpus_manifest["file_hashes"].get(relative)
        if actual != expected:
            raise SetupError(f"{relative} hash {actual} != {expected}")

    potion_dir = Path(potion_dir) if potion_dir is not None else vecidx.MODEL_DIR
    potion_files = {}
    for relative, expected in sorted(config["potion_files"].items()):
        path = potion_dir / relative
        if not path.is_file():
            raise SetupError(f"missing Potion file: {relative}")
        actual = _sha256_file(path)
        if actual != expected:
            raise SetupError(f"Potion {relative} hash {actual} != {expected}")
        potion_files[relative] = actual

    result = {
        "config": config,
        "config_sha256": config_sha256,
        "corpus_root": str(corpus_root),
        "corpus_manifest": corpus_manifest,
        "dependencies": versions,
        "potion_files": potion_files,
    }
    if stage == "dev":
        if model_manifest is None:
            raise SetupError("model manifest required for dev")
        declared_model = _read_json_file(model_manifest, "model manifest")
        spec = config["reranker"]
        for key in ("repository", "revision", "provider", "files"):
            if declared_model.get(key) != spec[key]:
                raise SetupError(f"model manifest {key} mismatch")
        for name, expected in sorted(config["dependencies"].items()):
            actual = (
                ".".join(str(declared_model.get("python", "")).split(".")[:2])
                if name == "python"
                else declared_model.get("dependencies", {}).get(name)
            )
            if actual != expected:
                raise SetupError(f"model manifest dependency {name} mismatch")
        model_dir = REPO_ROOT / spec["model_dir"]
        try:
            result["reranker"] = onnx_verifier(
                config, model_dir, package_version=package_version
            )
        except semantic_onnx.OnnxSetupError as exc:
            raise SetupError(str(exc)) from exc
    return result


def _evaluate_b0(split_data: dict, tokenizer_name: str) -> dict:
    tokenizer = {
        "current_ascii": recall.tokens,
        "unicode_nfc": unicode_tokens,
    }[tokenizer_name]
    notes = split_data["notes"]
    notes_by_id = {note["id"]: note for note in notes}
    results = {}
    for query in split_data["queries"]:
        ranked = rank_bm25(query["text"], notes, tokenizer)
        ranking = [note_id for note_id, _score in ranked][:CANDIDATE_K]
        protected = set(protected_note_ids(query["text"], notes, ranked))
        results[query["id"]] = score_query(query, ranking, protected, notes_by_id)
    validate_query_coverage([query["id"] for query in split_data["queries"]], results)
    return {
        "metrics": aggregate_results(split_data["queries"], results),
        "queries": results,
    }


def _semantic_rankings(split_data: dict) -> dict[str, list[tuple[str, float]]]:
    notes = sorted(split_data["notes"], key=lambda note: note["id"])
    note_ids = [note["id"] for note in notes]
    matrix = vecidx.embed_texts([note["text"] for note in notes])
    return {
        query["id"]: rank_potion(query["text"], note_ids, matrix)
        for query in split_data["queries"]
    }


def evaluate_split_policies(split_data: dict) -> list[dict]:
    return evaluate_all_candidate_policies(split_data, _semantic_rankings(split_data))


def evaluate_split_policy(
    split_data: dict, tokenizer_name: str, lexical_reserve: int
) -> dict:
    return evaluate_candidate_policy(
        split_data, tokenizer_name, lexical_reserve, _semantic_rankings(split_data)
    )


def _candidate_query_results(split_data: dict, candidate_report: dict) -> dict:
    queries_by_id = {query["id"]: query for query in split_data["queries"]}
    notes_by_id = {note["id"]: note for note in split_data["notes"]}
    rows_by_id = {row["query_id"]: row for row in candidate_report["queries"]}
    validate_query_coverage(list(queries_by_id), rows_by_id)
    return {
        query_id: score_query(
            query,
            rows_by_id[query_id]["candidates"],
            set(rows_by_id[query_id]["protected"]),
            notes_by_id,
        )
        for query_id, query in queries_by_id.items()
    }


def _confidence(results: dict[str, dict], config: dict) -> dict:
    rows = list(results.values())
    if not rows:
        return {}
    bootstrap = config["bootstrap"]
    return {
        metric: cluster_interval(rows, metric, bootstrap["seed"], bootstrap["samples"])
        for metric in ("hit_at_1", "hit_at_3", "hit_at_20", "reciprocal_rank")
    }


def _paired_confidence(
    left: dict[str, dict], right: dict[str, dict], config: dict
) -> dict:
    if not left and not right:
        return {}
    bootstrap = config["bootstrap"]
    return {
        metric: paired_cluster_interval(
            list(left.values()),
            list(right.values()),
            metric,
            bootstrap["seed"],
            bootstrap["samples"],
        )
        for metric in ("hit_at_1", "hit_at_3", "hit_at_20", "reciprocal_rank")
    }


def run_train(
    config_path: Path,
    out: Path,
    *,
    preflight_fn=None,
    load_split=corpus_lint.load_split,
    evaluate_policies=None,
    metadata_fn=environment_metadata,
) -> dict:
    config, config_sha256 = _config(config_path)
    preflight_fn = preflight_fn or preflight_inputs
    preflight = preflight_fn(config_path, "train")
    split_data = load_baseline_split(
        _corpus_root(config_path, config), "train", loader=load_split
    )
    if preflight:
        validate_split_counts(
            split_data, preflight["corpus_manifest"]["counts"]["train"], "train"
        )
    started = time.perf_counter_ns()
    b0 = {
        tokenizer_name: _evaluate_b0(split_data, tokenizer_name)
        for tokenizer_name in TOKENIZER_NAMES
    }
    for item in b0.values():
        item["confidence"] = _confidence(item["queries"], config)
    policies = (evaluate_policies or evaluate_split_policies)(split_data)
    selected = select_policy(policies)
    status = "POLICY_SELECTED" if selected is not None else "CANDIDATE_NO_GO"
    report = {
        "baseline_version": config["baseline_version"],
        "status": status,
        "split": "train",
        "corpus_hash": config["corpus"]["corpus_hash"],
        "config_sha256": config_sha256,
        "environment": metadata_fn(
            package_names=("model2vec", "numpy", "tokenizers"), provider=None
        ),
        "diagnostic_timings_ms": {
            "total": (time.perf_counter_ns() - started) / 1_000_000
        },
        "b0": b0,
        "b0_comparison": {
            "counts": compare_results(
                b0["current_ascii"]["queries"], b0["unicode_nfc"]["queries"]
            ),
            "paired_confidence": _paired_confidence(
                b0["current_ascii"]["queries"], b0["unicode_nfc"]["queries"], config
            ),
        },
        "policies": policies,
        "selected_policy_id": selected["policy_id"] if selected else None,
    }
    out = Path(out)
    train_sha256 = write_json(out / "train-report.json", report)
    selected_payload = {
        "baseline_version": config["baseline_version"],
        "config_sha256": config_sha256,
        "corpus_hash": config["corpus"]["corpus_hash"],
        "train_report_sha256": train_sha256,
        "selected": (
            {
                key: selected[key]
                for key in (
                    "policy_id", "tokenizer", "lexical_reserve", "semantic_reserve"
                )
            }
            if selected else None
        ),
        "gate_errors": [] if selected else [
            error for policy in policies for error in policy["gate_errors"]
        ],
    }
    write_json(out / "selected-policy.json", selected_payload)
    if selected is None:
        write_json(
            out / "manifest.json", build_baseline_manifest(out, "CANDIDATE_NO_GO")
        )
    return report


def finish_dev(candidate_report: dict, reranker_factory: Callable) -> dict:
    if candidate_report["gate_errors"]:
        return {
            "status": "CANDIDATE_NO_GO",
            "gate_errors": list(candidate_report["gate_errors"]),
        }
    return {"status": "B1_PASS", "reranker": reranker_factory()}


def run_dev(
    config_path: Path,
    out: Path,
    model_manifest: Path,
    *,
    preflight_fn=None,
    load_split=corpus_lint.load_split,
    evaluate_policy=None,
    reranker_factory=semantic_onnx.OnnxReranker.load,
    metadata_fn=environment_metadata,
) -> dict:
    config, config_sha256 = _config(config_path)
    out = Path(out)
    selected_artifact = _read_json_file(out / "selected-policy.json", "selected policy")
    if selected_artifact.get("config_sha256") != config_sha256:
        raise SetupError("selected-policy config_sha256 mismatch")
    require_new_output(out / "dev-report.json")
    selected = selected_artifact.get("selected")
    if selected is None:
        raise SetupError("selected-policy has no selected candidate policy")
    train_path = out / "train-report.json"
    if _sha256_file(train_path) != selected_artifact.get("train_report_sha256"):
        raise SetupError("train-report hash mismatch")

    preflight_fn = preflight_fn or preflight_inputs
    preflight = preflight_fn(config_path, "dev", model_manifest)
    split_data = load_baseline_split(
        _corpus_root(config_path, config), "dev", loader=load_split
    )
    if preflight:
        validate_split_counts(
            split_data, preflight["corpus_manifest"]["counts"]["dev"], "dev"
        )
    started = time.perf_counter_ns()
    b0 = {
        tokenizer_name: _evaluate_b0(split_data, tokenizer_name)
        for tokenizer_name in TOKENIZER_NAMES
    }
    for item in b0.values():
        item["confidence"] = _confidence(item["queries"], config)

    candidate_report = (evaluate_policy or evaluate_split_policy)(
        split_data, selected["tokenizer"], selected["lexical_reserve"]
    )
    expected_queries = (
        preflight.get("corpus_manifest", {})
        .get("counts", {})
        .get("dev", {})
        .get("queries", len(split_data["queries"]))
        if preflight
        else len(split_data["queries"])
    )
    candidate_report["gate_errors"] = candidate_gate(
        candidate_report, expected_queries=expected_queries
    )
    b1_results = _candidate_query_results(split_data, candidate_report)
    candidate_report["metrics"] = aggregate_results(split_data["queries"], b1_results)
    candidate_report["confidence"] = _confidence(b1_results, config)

    base_report = {
        "baseline_version": config["baseline_version"],
        "split": "dev",
        "corpus_hash": config["corpus"]["corpus_hash"],
        "config_sha256": config_sha256,
        "environment": metadata_fn(
            package_names=("model2vec", "numpy", "onnxruntime", "tokenizers"),
            provider=config["reranker"]["provider"],
        ),
        "b0": b0,
        "b1": candidate_report,
    }
    if candidate_report["gate_errors"]:
        report = {
            **base_report,
            "status": "CANDIDATE_NO_GO",
            "b2": None,
            "diagnostic_timings_ms": {
                "total": (time.perf_counter_ns() - started) / 1_000_000
            },
        }
        write_json(out / "dev-report.json", report)
        write_json(
            out / "manifest.json", build_baseline_manifest(out, "CANDIDATE_NO_GO")
        )
        return report

    model_dir = REPO_ROOT / config["reranker"]["model_dir"]
    try:
        reranker = reranker_factory(model_dir, config["reranker"]["max_length"])
    except semantic_onnx.OnnxSetupError as exc:
        raise SetupError(str(exc)) from exc
    notes_by_id = {note["id"]: note for note in split_data["notes"]}
    note_texts = {note_id: note["text"] for note_id, note in notes_by_id.items()}
    candidate_rows = {row["query_id"]: row for row in candidate_report["queries"]}
    repeated_rankings = []
    first_results = None
    for _repeat in range(20):
        results = {}
        rank_map = {}
        for query in split_data["queries"]:
            row = candidate_rows[query["id"]]
            ranked_scores = semantic_onnx.rerank(
                query["text"], row["candidates"], note_texts,
                set(row["protected"]), reranker.score,
            )
            rank_map[query["id"]] = [note_id for note_id, _score in ranked_scores]
            results[query["id"]] = score_rerank(
                query, ranked_scores, set(row["protected"]), notes_by_id
            )
        validate_query_coverage([query["id"] for query in split_data["queries"]], results)
        repeated_rankings.append(rank_map)
        if first_results is None:
            first_results = results
    ranking_sha256 = assert_repeatable_rankings(repeated_rankings)
    b2 = {
        "metrics": aggregate_results(split_data["queries"], first_results),
        "confidence": _confidence(first_results, config),
        "queries": first_results,
        "comparisons": {
            tokenizer_name: compare_results(item["queries"], first_results)
            for tokenizer_name, item in b0.items()
        },
        "paired_confidence": {
            tokenizer_name: _paired_confidence(item["queries"], first_results, config)
            for tokenizer_name, item in b0.items()
        },
        "determinism": {"repeats": 20, "ranking_sha256": ranking_sha256},
    }
    report = {
        **base_report,
        "status": "BASELINE_COMPLETE",
        "b2": b2,
        "diagnostic_timings_ms": {
            "total": (time.perf_counter_ns() - started) / 1_000_000
        },
    }
    write_json(out / "dev-report.json", report)
    write_json(
        out / "manifest.json", build_baseline_manifest(out, "BASELINE_COMPLETE")
    )
    return report


def build_baseline_manifest(root: Path, status: str) -> dict:
    root = Path(root)
    if status == "BASELINE_COMPLETE":
        names = _COMPLETE_FILES
    elif status == "CANDIDATE_NO_GO":
        names = tuple(
            name for name in _COMPLETE_FILES
            if name != "dev-report.json" or (root / name).exists()
        )
    else:
        raise SetupError(f"invalid terminal status: {status}")
    files = {}
    for name in names:
        path = root / name
        if not path.is_file():
            raise SetupError(f"missing baseline artifact: {name}")
        files[name] = _sha256_file(path)
    return {"baseline_version": "v1", "files": files, "status": status}


def _cross_file_errors(root: Path) -> list[str]:
    errors = []
    try:
        config, config_sha256 = _config(root / "config.json")
        train = _read_json_file(root / "train-report.json", "train report")
        selected = _read_json_file(root / "selected-policy.json", "selected policy")
        model = _read_json_file(root / "model-manifest.json", "model manifest")
        corpus_hash = config["corpus"]["corpus_hash"]
        for label, artifact in (("train-report", train), ("selected-policy", selected)):
            if artifact.get("config_sha256") != config_sha256:
                errors.append(f"{label} config_sha256 mismatch")
            if artifact.get("corpus_hash") != corpus_hash:
                errors.append(f"{label} corpus_hash mismatch")
        if selected.get("train_report_sha256") != _sha256_file(root / "train-report.json"):
            errors.append("selected-policy train_report_sha256 mismatch")
        dev_path = root / "dev-report.json"
        if dev_path.exists():
            dev = _read_json_file(dev_path, "dev report")
            if dev.get("config_sha256") != config_sha256:
                errors.append("dev-report config_sha256 mismatch")
            if dev.get("corpus_hash") != corpus_hash:
                errors.append("dev-report corpus_hash mismatch")
        reranker = config.get("reranker", {})
        for key in ("repository", "revision", "provider", "files"):
            if model.get(key) != reranker.get(key):
                errors.append(f"model-manifest {key} mismatch")
    except (KeyError, SetupError) as exc:
        errors.append(str(exc))
    return errors


def check_baseline_artifacts(root: Path) -> list[str]:
    root = Path(root)
    path = root / "manifest.json"
    if not path.is_file():
        return ["manifest.json missing"]
    try:
        declared = json.loads(path.read_text(encoding="utf-8"))
        computed = build_baseline_manifest(root, declared["status"])
    except (json.JSONDecodeError, KeyError, SetupError) as exc:
        return [str(exc)]
    if path.read_bytes() != canonical_json_bytes(declared):
        errors = ["manifest.json is not canonical JSON"]
    else:
        errors = []
    for name in computed["files"]:
        artifact_path = root / name
        try:
            value = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{name} invalid JSON: {exc}")
            continue
        if artifact_path.read_bytes() != canonical_json_bytes(value):
            errors.append(f"{name} is not canonical JSON")
    # Hash and cross-file identity checks run even when canonicality already failed.
    if declared.get("baseline_version") != computed["baseline_version"]:
        errors.append("baseline_version mismatch")
    if set(declared.get("files", {})) != set(computed["files"]):
        errors.append("manifest file set mismatch")
    for name, digest in computed["files"].items():
        if declared.get("files", {}).get(name) != digest:
            errors.append(f"{name} hash mismatch")
    errors.extend(_cross_file_errors(root))
    return errors


def _ratio(counts: dict, key: str) -> str:
    return f"{counts[key]}/{counts['queries']}"


def _slice_table(title: str, slices: dict) -> list[str]:
    if not slices:
        return []
    lines = ["", f"### {title}", "", "| Slice | Top-1 | Recall@3 | Recall@20 |", "|---|---:|---:|---:|"]
    for name, counts in sorted(slices.items()):
        lines.append(
            f"| {name} | {_ratio(counts, 'hit_at_1')} | "
            f"{_ratio(counts, 'hit_at_3')} | {_ratio(counts, 'hit_at_20')} |"
        )
    return lines


def render_evidence(train_report: dict, dev_report: dict | None, manifest: dict) -> str:
    current = train_report["b0"]["current_ascii"]["metrics"]["overall"]
    unicode = train_report["b0"]["unicode_nfc"]["metrics"]["overall"]
    lines = [
        "# Evidence — Semantic Baseline v1",
        "",
        f"**Status:** `{manifest['status']}`  ",
        f"**Selected policy:** `{train_report.get('selected_policy_id')}`",
        "",
        "## Train lexical references",
        "",
        f"- B0-current Recall@20: **{_ratio(current, 'hit_at_20')}**",
        f"- B0-unicode Recall@20: **{_ratio(unicode, 'hit_at_20')}**",
    ]
    if dev_report is None:
        lines += ["", "B2 was not run because candidate retrieval did not clear its gate."]
    else:
        b1 = dev_report["b1"]["overall"]
        lines += [
            "", "## Dev candidate gate", "",
            f"- B1 Recall@20: **{_ratio(b1, 'hit_at_20')}**",
            f"- Safety Recall@20: **{_ratio(dev_report['b1']['safety'], 'hit_at_20')}**",
            f"- Identifier Recall@20: **{_ratio(dev_report['b1']['identifier'], 'hit_at_20')}**",
        ]
        b1_metrics = dev_report["b1"].get("metrics")
        if b1_metrics:
            lines += _slice_table("B1 By language", b1_metrics.get("languages", {}))
            lines += _slice_table("B1 By contrast family", b1_metrics.get("families", {}))

        missed_queries = [q for q in dev_report["b1"].get("queries", []) if not q.get("hit_at_20")]
        if missed_queries:
            lines += ["", "### B1 Missed Queries", ""]
            for mq in sorted(missed_queries, key=lambda x: x["query_id"]):
                lines.append(f"- `{mq['query_id']}`: Candidates searched: {mq.get('candidates', [])}")

        if dev_report.get("b2"):
            b2 = dev_report["b2"]["metrics"]
            overall = b2["overall"]
            lines += [
                "", "## Dev zero-shot reranking", "",
                f"- B2 Top-1: **{_ratio(overall, 'hit_at_1')}**",
                f"- B2 Recall@3: **{_ratio(overall, 'hit_at_3')}**",
                f"- B2 MRR: **{overall['mean_reciprocal_rank']:.6f}**",
                f"- Ranking determinism: **{dev_report['b2']['determinism']['repeats']} repeats**, "
                f"`{dev_report['b2']['determinism']['ranking_sha256']}`",
            ]
            lines += _slice_table("B2 By language", b2.get("languages", {}))
            lines += _slice_table("B2 By contrast family", b2.get("families", {}))
        else:
            lines += ["", "B2 was not run because candidate retrieval did not clear its gate."]

    lines += ["", "## File Identity & Provenance", ""]
    lines.append(f"- Corpus hash: `{train_report.get('corpus_hash')}`")
    if manifest.get("files"):
        for name, digest in sorted(manifest["files"].items()):
            lines.append(f"- `{name}`: `{digest}`")

    if dev_report and dev_report.get("environment"):
        env = dev_report["environment"]
        lines += ["", "## Environment", "", f"- Python: `{env.get('python')}`", f"- Platform: `{env.get('platform')}`", f"- Provider: `{env.get('provider')}`"]
        if env.get("dependencies"):
            deps_str = ", ".join(f"`{k}=={v}`" for k, v in sorted(env["dependencies"].items()))
            lines.append(f"- Dependencies: {deps_str}")
    else:
        lines += [
            "", "## Environment", "",
            "Windows timings are diagnostic; Linux x86-64 performance remains uncertified.",
        ]
    lines.append("")
    return "\n".join(lines)

def _write_text(path: Path, text: str) -> None:
    path = Path(path)
    payload = text if text.endswith("\n") else text + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--config", type=Path, required=True)
    preflight_parser.add_argument("--model-manifest", type=Path, required=True)

    train_parser = commands.add_parser("train")
    train_parser.add_argument("--config", type=Path, required=True)
    train_parser.add_argument("--out", type=Path, required=True)

    dev_parser = commands.add_parser("dev")
    dev_parser.add_argument("--config", type=Path, required=True)
    dev_parser.add_argument("--out", type=Path, required=True)
    dev_parser.add_argument("--model-manifest", type=Path, required=True)

    check_parser = commands.add_parser("check")
    check_parser.add_argument("--config", type=Path, required=True)
    check_parser.add_argument("--out", type=Path, required=True)

    render_parser = commands.add_parser("render")
    render_parser.add_argument("--out", type=Path, required=True)
    render_parser.add_argument("--evidence", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight_inputs(args.config, "dev", args.model_manifest)
            print(f"preflight: PASS corpus_hash={result['corpus_manifest']['corpus_hash']}")
            return 0
        if args.command == "train":
            result = run_train(args.config, args.out)
            print(f"baseline train: {result['status']}")
            return 3 if result["status"] == "CANDIDATE_NO_GO" else 0
        if args.command == "dev":
            result = run_dev(args.config, args.out, args.model_manifest)
            print(f"baseline dev: {result['status']}")
            return 3 if result["status"] == "CANDIDATE_NO_GO" else 0
        if args.command == "check":
            config_path = args.out / "config.json"
            if args.config.resolve() != config_path.resolve():
                raise SetupError("--config must name <out>/config.json")
            errors = check_baseline_artifacts(args.out)
            if errors:
                raise SetupError("; ".join(errors))
            print("baseline artifacts: PASS")
            return 0
        train = _read_json_file(args.out / "train-report.json", "train report")
        dev_path = args.out / "dev-report.json"
        dev = _read_json_file(dev_path, "dev report") if dev_path.exists() else None
        manifest = _read_json_file(args.out / "manifest.json", "baseline manifest")
        _write_text(args.evidence, render_evidence(train, dev, manifest))
        print(f"baseline evidence: wrote {args.evidence}")
        return 0
    except (BaselineError, OSError) as exc:
        print(f"baseline: FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
