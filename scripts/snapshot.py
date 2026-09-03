#!/usr/bin/env python3
"""Content-addressed snapshot and restore for the MNEMOS substrate.

There is no git in this runtime, so rollback is built from sha256-addressed
blobs plus JSON manifests. The substrate is small text, so this is cheap.

Store layout (under --store):
    objects/<ab>/<sha256>        blob, written once, never mutated
    manifests/<snapshot_id>.json {relpath: sha256} recorded against --base

Design rules:
  * blobs are immutable and deduplicated by content
  * every write is atomic (tmp file + os.replace)
  * every path is resolved and proven to sit inside --base before use
  * restore refuses to run against a snapshot that does not verify

CLI:
    snapshot.py create  --store S --base B --paths a.md b/c.json [--label L]
    snapshot.py list    --store S
    snapshot.py verify  --store S --id snap-...
    snapshot.py restore --store S --base B --id snap-... [--apply] [--only p]
    snapshot.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_path(store: Path, digest: str) -> Path:
    return store / "objects" / digest[:2] / digest


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def resolve_inside(base: Path, relpath: str) -> Path:
    """Resolve relpath under base, refusing anything that escapes base."""
    if os.path.isabs(relpath):
        raise ValueError("absolute paths are not accepted: " + relpath)
    base_r = base.resolve()
    target = (base_r / relpath).resolve()
    if target != base_r and base_r not in target.parents:
        raise ValueError("path escapes base: " + relpath)
    return target


def create(store: Path, base: Path, relpaths, label: str = "") -> dict:
    files: dict[str, str] = {}
    missing: list[str] = []
    for rel in relpaths:
        path = resolve_inside(base, rel)
        if not path.is_file():
            missing.append(rel)
            continue
        data = path.read_bytes()
        digest = sha256_bytes(data)
        obj = _object_path(store, digest)
        if not obj.exists():
            atomic_write(obj, data)
        files[rel] = digest
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "id": "snap-" + stamp,
        "created_at": utcnow(),
        "label": label,
        "base": str(base),
        "files": files,
        "missing": missing,
    }
    atomic_write(
        store / "manifests" / (manifest["id"] + ".json"),
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
    )
    return manifest


def load_manifest(store: Path, snapshot_id: str) -> dict:
    path = store / "manifests" / (snapshot_id + ".json")
    if not path.is_file():
        raise FileNotFoundError("unknown snapshot: " + snapshot_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_snapshots(store: Path) -> list[dict]:
    directory = store / "manifests"
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("snap-*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "id": manifest["id"],
                "created_at": manifest["created_at"],
                "label": manifest.get("label", ""),
                "file_count": len(manifest["files"]),
            }
        )
    return out


def verify(store: Path, snapshot_id: str) -> dict:
    manifest = load_manifest(store, snapshot_id)
    problems = []
    for rel, digest in sorted(manifest["files"].items()):
        obj = _object_path(store, digest)
        if not obj.exists():
            problems.append({"path": rel, "problem": "missing_object"})
        elif sha256_bytes(obj.read_bytes()) != digest:
            problems.append({"path": rel, "problem": "corrupt_object"})
    return {
        "id": snapshot_id,
        "ok": not problems,
        "problems": problems,
        "file_count": len(manifest["files"]),
    }


def find_latest_containing(store: Path, relpath: str) -> str | None:
    """Newest snapshot id whose manifest carries relpath, or None."""
    for entry in reversed(list_snapshots(store)):
        manifest = load_manifest(store, entry["id"])
        if relpath in manifest["files"]:
            return entry["id"]
    return None


def restore(
    store: Path,
    base: Path,
    snapshot_id: str,
    apply: bool = False,
    only=None,
) -> dict:
    report = verify(store, snapshot_id)
    if not report["ok"]:
        raise RuntimeError("refusing restore, snapshot does not verify: " + json.dumps(report["problems"]))
    manifest = load_manifest(store, snapshot_id)
    only_set = set(only) if only else None
    planned, restored = [], []
    for rel, digest in sorted(manifest["files"].items()):
        if only_set is not None and rel not in only_set:
            continue
        target = resolve_inside(base, rel)
        current = sha256_bytes(target.read_bytes()) if target.is_file() else None
        if current == digest:
            continue
        planned.append({"path": rel, "from": current, "to": digest})
        if apply:
            atomic_write(target, _object_path(store, digest).read_bytes())
            restored.append(rel)
    return {
        "id": snapshot_id,
        "applied": bool(apply),
        "planned": planned,
        "restored": restored,
    }


# ----------------------------------------------------------------- self-test

def _selftest() -> int:
    import shutil

    failures = []

    def check(name, condition):
        if condition:
            print("  ok   " + name)
        else:
            print("  FAIL " + name)
            failures.append(name)

    work = Path(tempfile.mkdtemp(prefix="snaptest-"))
    try:
        base = work / "base"
        store = work / "store"
        (base / "nested").mkdir(parents=True)
        (base / "a.md").write_text("alpha\n", encoding="utf-8")
        (base / "b.json").write_text('{"k": 1}\n', encoding="utf-8")
        (base / "nested" / "c.txt").write_text("gamma\n", encoding="utf-8")

        manifest = create(store, base, ["a.md", "b.json", "nested/c.txt"], label="t0")
        check("create records every file", len(manifest["files"]) == 3)
        check("create reports no missing", manifest["missing"] == [])
        check("verify passes on fresh snapshot", verify(store, manifest["id"])["ok"])
        check("list sees one snapshot", len(list_snapshots(store)) == 1)

        # dedup: identical content must not create a second blob
        (base / "dup.md").write_text("alpha\n", encoding="utf-8")
        m2 = create(store, base, ["a.md", "dup.md"], label="dedup")
        check("identical content dedupes", m2["files"]["a.md"] == m2["files"]["dup.md"])
        blobs = list((store / "objects").rglob("*"))
        blob_files = [p for p in blobs if p.is_file()]
        check("blob count is content-addressed", len(blob_files) == 3)

        # mutate and delete, then restore
        (base / "a.md").write_text("CORRUPTED\n", encoding="utf-8")
        (base / "nested" / "c.txt").unlink()
        dry = restore(store, base, manifest["id"], apply=False)
        check("dry run plans exactly the two divergent files", len(dry["planned"]) == 2)
        check("dry run changes nothing", (base / "a.md").read_text() == "CORRUPTED\n")

        applied = restore(store, base, manifest["id"], apply=True)
        check("apply restores both", sorted(applied["restored"]) == ["a.md", "nested/c.txt"])
        check("mutated file is byte-exact again", (base / "a.md").read_bytes() == b"alpha\n")
        check("deleted file is back", (base / "nested" / "c.txt").read_bytes() == b"gamma\n")

        again = restore(store, base, manifest["id"], apply=True)
        check("restore is idempotent", again["planned"] == [])

        # selective restore
        (base / "a.md").write_text("x\n", encoding="utf-8")
        (base / "b.json").write_text("x\n", encoding="utf-8")
        sel = restore(store, base, manifest["id"], apply=True, only=["a.md"])
        check("only= restores just that path", sel["restored"] == ["a.md"])
        check("only= leaves the other file alone", (base / "b.json").read_text() == "x\n")

        # path escape
        escaped = False
        try:
            resolve_inside(base, "../outside.txt")
        except ValueError:
            escaped = True
        check("path escape is refused", escaped)

        absolute = False
        try:
            resolve_inside(base, "/etc/passwd")
        except ValueError:
            absolute = True
        check("absolute path is refused", absolute)

        # missing file is recorded, not fatal
        m3 = create(store, base, ["a.md", "ghost.md"], label="ghost")
        check("missing input recorded", m3["missing"] == ["ghost.md"])

        # corruption detection
        victim = _object_path(store, manifest["files"]["b.json"])
        victim.write_bytes(b"tampered")
        bad = verify(store, manifest["id"])
        check("corrupt blob detected", not bad["ok"] and bad["problems"][0]["problem"] == "corrupt_object")

        refused = False
        try:
            restore(store, base, manifest["id"], apply=True)
        except RuntimeError:
            refused = True
        check("restore refuses a corrupt snapshot", refused)

        check("unknown snapshot raises", _raises(lambda: load_manifest(store, "snap-nope")))
        check("find_latest_containing works", find_latest_containing(store, "dup.md") == m2["id"])
        check("find_latest_containing misses cleanly", find_latest_containing(store, "never.md") is None)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASSED")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Content-addressed snapshot and restore.")
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_create = sub.add_parser("create")
    p_create.add_argument("--store", required=True)
    p_create.add_argument("--base", required=True)
    p_create.add_argument("--paths", nargs="+", required=True)
    p_create.add_argument("--label", default="")

    p_list = sub.add_parser("list")
    p_list.add_argument("--store", required=True)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--store", required=True)
    p_verify.add_argument("--id", required=True)

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("--store", required=True)
    p_restore.add_argument("--base", required=True)
    p_restore.add_argument("--id", required=True)
    p_restore.add_argument("--apply", action="store_true")
    p_restore.add_argument("--only", nargs="*")

    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.command == "create":
        out = create(Path(args.store), Path(args.base), args.paths, args.label)
    elif args.command == "list":
        out = list_snapshots(Path(args.store))
    elif args.command == "verify":
        out = verify(Path(args.store), args.id)
    elif args.command == "restore":
        out = restore(Path(args.store), Path(args.base), args.id, args.apply, args.only)
    else:
        parser.print_help()
        return 2

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
