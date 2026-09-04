#!/usr/bin/env python3
"""Fetch the pinned semantic baseline reranker and verify every byte."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
from pathlib import Path
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]


def _identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def fetch_files(model_spec: dict, destination: Path, opener=urlopen) -> dict:
    destination = Path(destination)
    root = destination.resolve()
    files = {}
    for relative_path, expected in sorted(model_spec["files"].items()):
        target = (destination / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"model path escapes destination: {relative_path}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _identity(target) == expected:
            files[relative_path] = expected
            continue

        url = (
            f"https://huggingface.co/{model_spec['repository']}/resolve/"
            f"{model_spec['revision']}/{relative_path}"
        )
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle, opener(url) as response:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            actual = {"bytes": size, "sha256": digest.hexdigest()}
            if actual["bytes"] != expected["bytes"]:
                raise ValueError(
                    f"{relative_path}: bytes {actual['bytes']} != {expected['bytes']}"
                )
            if actual["sha256"] != expected["sha256"]:
                raise ValueError(
                    f"{relative_path}: sha256 {actual['sha256']} != {expected['sha256']}"
                )
            os.replace(temporary_name, target)
            files[relative_path] = actual
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    return {
        "repository": model_spec["repository"],
        "revision": model_spec["revision"],
        "files": files,
    }


def _canonical_bytes(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    model_spec = config["reranker"]
    destination = REPO_ROOT / model_spec["model_dir"]
    manifest = fetch_files(model_spec, destination)
    manifest.update({
        "model_dir": model_spec["model_dir"],
        "provider": model_spec["provider"],
        "python": platform.python_version(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in sorted(config["dependencies"])
            if name != "python"
        },
    })
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_bytes(_canonical_bytes(manifest))
    print(f"reranker fetch: PASS revision={manifest['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
