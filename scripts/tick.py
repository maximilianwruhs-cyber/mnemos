#!/usr/bin/env python3
"""One autonomy tick, computed entirely in code.

Why this file exists
--------------------
The sandbox cannot write into the FileStore, so something has to carry changes
back. Until now that carrier was a prose instruction telling a model to diff a
tree and remember what to write. Every defect found in the 2026-08-29 audit came
from that arrangement: debris leaked, the lease was never persisted, and the
audit log was rewritten wholesale each hour instead of appended.

So the model stops deciding. This program emits a complete persistence plan --
every write with its exact bytes, every delete -- and the caller's only job is to
apply that plan verbatim. No judgement, no reconstruction, no "stage whatever
looks relevant".

Three prose-shaped hazards are now code-shaped:

  * Flat staging. Files arrive at /tmp/<basename> with their directory structure
    lost. rebuild() restores the real tree from a manifest, and refuses to guess
    when two manifest entries share a basename.
  * Incomplete staging. The manifest declares what the tick needs. Anything
    missing is a hard error naming the absent paths, not a silent partial run.
  * Silent truncation. Files declared append_only are verified to have grown by
    prefix. An audit log that shrank, or whose history changed, aborts the tick.

Usage
-----
    tick.py --stage-dir /tmp --root /tmp/ws --owner tick-<ts> [--heal] [--no-dispatch]
    tick.py --selftest

Exit codes: 0 plan produced, 1 nothing to persist, 2 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_MANIFEST = "autonomy/config/workspace.manifest.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_inside(base: Path, relpath: str) -> Path:
    if os.path.isabs(relpath):
        raise ValueError("absolute path not accepted: " + relpath)
    base_r = base.resolve()
    target = (base_r / relpath).resolve()
    if target != base_r and base_r not in target.parents:
        raise ValueError("path escapes root: " + relpath)
    return target


# ------------------------------------------------------------------ staging

def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported workspace manifest schema_version")
    files = manifest.get("files", [])
    if not files:
        raise ValueError("workspace manifest declares no files")
    basenames: dict[str, str] = {}
    for rel in files:
        name = Path(rel).name
        if name in basenames:
            raise ValueError(
                "flat-staging collision: '" + rel + "' and '" + basenames[name] +
                "' share the basename '" + name + "'"
            )
        basenames[name] = rel
    unknown = set(manifest.get("append_only", [])) - set(files)
    if unknown:
        raise ValueError("append_only names files not in the manifest: " + ", ".join(sorted(unknown)))
    return manifest


def rebuild(stage_dir: Path, manifest: dict, root: Path) -> dict:
    """Restore the nested workspace from flat staging. Returns a staging report."""
    present, missing = [], []
    for rel in manifest["files"]:
        source = stage_dir / Path(rel).name
        target = resolve_inside(root, rel)
        if not source.is_file():
            missing.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        present.append(rel)
    for rel in manifest.get("directories", []):
        resolve_inside(root, rel).mkdir(parents=True, exist_ok=True)
    required = [r for r in manifest.get("required", manifest["files"]) if r in missing]
    return {"present": present, "missing": missing, "missing_required": required}


# -------------------------------------------------------------------- state

def tree_state(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        out[rel] = sha256_bytes(path.read_bytes())
    return out


def capture_bytes(root: Path, relpaths) -> dict[str, bytes]:
    out = {}
    for rel in relpaths:
        path = root / rel
        if path.is_file():
            out[rel] = path.read_bytes()
    return out


# --------------------------------------------------------------------- plan

def make_plan(root: Path, before: dict, after: dict, before_bytes: dict, append_only) -> dict:
    writes, deletes, binary_refused, verified = [], [], [], []
    append_only = set(append_only)

    for rel in sorted(set(after) - set(before)):
        writes.append(rel)
    for rel in sorted(set(after) & set(before)):
        if after[rel] != before[rel]:
            writes.append(rel)
    for rel in sorted(set(before) - set(after)):
        deletes.append(rel)
    writes.sort()

    entries = []
    for rel in writes:
        data = (root / rel).read_bytes()
        if rel in append_only:
            old = before_bytes.get(rel, b"")
            if not data.startswith(old):
                raise RuntimeError(
                    "append-only violation on '" + rel + "': new content is not an extension of the old content"
                )
            verified.append(rel)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary_refused.append(rel)
            continue
        entries.append({"path": rel, "sha256": sha256_bytes(data), "bytes": len(data), "content": text})

    for rel in deletes:
        if rel in append_only:
            raise RuntimeError("refusing to delete an append-only file: " + rel)

    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "writes": entries,
        "deletes": deletes,
        "append_only_verified": verified,
        "binary_refused": binary_refused,
    }
    plan["plan_sha256"] = sha256_bytes(
        json.dumps(
            {"w": [(e["path"], e["sha256"]) for e in entries], "d": deletes},
            sort_keys=True,
        ).encode("utf-8")
    )
    return plan


# --------------------------------------------------------------------- work

def run_dispatcher(script: Path, root: Path, owner: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", str(script), "--root", str(root), "--owner", owner],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"transition": "dispatcher_error", "returncode": proc.returncode, "stderr": proc.stderr[-2000:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"transition": "dispatcher_unparseable", "stdout": proc.stdout[-2000:]}


def run_heal(script: Path, root: Path) -> dict:
    config = root / "autonomy" / "config" / "invariants.json"
    if not config.is_file():
        return {"skipped": "no invariants.json in workspace"}
    proc = subprocess.run(
        [sys.executable, "-B", str(script), "--base", str(root), "--config", str(config),
         "--store", str(root / "autonomy" / "snapshots"), "--apply"],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "self_heal output unparseable", "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-1000:]}


def run_probation(script: Path, root: Path, suite: Path, scripts_dir: Path) -> dict:
    if not suite.is_file():
        return {"skipped": "no regression suite staged"}
    proc = subprocess.run(
        [sys.executable, "-B", str(script), "evaluate", "--base", str(root),
         "--suite", str(suite), "--scripts-dir", str(scripts_dir)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "probation output unparseable", "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-800:]}


def run_evolution(script: Path, root: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", str(script), "--base", str(root)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "evolution output unparseable", "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-800:]}


def tick(stage_dir: Path, root: Path, owner: str, heal: bool = False, dispatch: bool = True,
         probation: bool = False, evolution: bool = False) -> dict:
    manifest_source = stage_dir / Path(DEFAULT_MANIFEST).name
    if not manifest_source.is_file():
        raise RuntimeError("workspace manifest was not staged: " + Path(DEFAULT_MANIFEST).name)
    manifest = load_manifest(manifest_source)

    staging = rebuild(stage_dir, manifest, root)
    if staging["missing_required"]:
        raise RuntimeError("required files were not staged: " + ", ".join(staging["missing_required"]))

    append_only = manifest.get("append_only", [])
    before = tree_state(root)
    before_bytes = capture_bytes(root, append_only)

    dispatcher_result, heal_result = None, None
    probation_result, evolution_result = None, None
    if probation:
        script = stage_dir / "probation.py"
        if not script.is_file():
            raise RuntimeError("probation.py was not staged")
        # Runs before evolution: a previous change must be judged before another
        # can start. Its writes are captured by the same persistence plan.
        probation_result = run_probation(script, root, stage_dir / "regression.json", stage_dir)
    if evolution:
        script = stage_dir / "evolution.py"
        if not script.is_file():
            raise RuntimeError("evolution.py was not staged")
        evolution_result = run_evolution(script, root)
    if dispatch:
        script = stage_dir / "autonomy_dispatcher.py"
        if not script.is_file():
            raise RuntimeError("autonomy_dispatcher.py was not staged")
        # The dispatcher's --root is the autonomy workspace, not the store root.
        # Passing the store root makes it initialise a parallel empty tree and
        # report a meaningless idle. Caught by the live run on 2026-08-29.
        dispatcher_result = run_dispatcher(script, root / "autonomy", owner)
    if heal:
        script = stage_dir / "self_heal.py"
        if not script.is_file():
            raise RuntimeError("self_heal.py was not staged")
        heal_result = run_heal(script, root)

    after = tree_state(root)
    plan = make_plan(root, before, after, before_bytes, append_only)

    return {
        "schema_version": SCHEMA_VERSION,
        "owner": owner,
        "at": utcnow(),
        "staging": {"present": len(staging["present"]), "missing": staging["missing"]},
        "probation": probation_result,
        "evolution": evolution_result,
        "dispatcher": dispatcher_result,
        "heal": heal_result,
        "plan": plan,
        "persist_required": bool(plan["writes"] or plan["deletes"]),
    }


# ---------------------------------------------------------------- self-test

def _selftest() -> int:
    import tempfile

    failures = []

    def check(name, condition):
        if condition:
            print("  ok   " + name)
        else:
            print("  FAIL " + name)
            failures.append(name)

    work = Path(tempfile.mkdtemp(prefix="ticktest-"))
    try:
        stage = work / "stage"
        root = work / "ws"
        stage.mkdir()

        # A stub dispatcher: appends one audit line, writes a lease, moves a queue item.
        stub = stage / "autonomy_dispatcher.py"
        stub.write_text(
            "import json, sys, os, pathlib\n"
            "root = pathlib.Path(sys.argv[sys.argv.index('--root') + 1])\n"
            "(root / 'audit').mkdir(parents=True, exist_ok=True)\n"
            "(root / 'state').mkdir(parents=True, exist_ok=True)\n"
            "with open(root / 'audit' / 'events.jsonl', 'a') as fh:\n"
            "    fh.write('{\\\"event\\\": \\\"tick\\\"}\\n')\n"
            "(root / 'state' / 'lease.json').write_text('{\\\"owner\\\": \\\"x\\\"}')\n"
            "src = root / 'reports' / 'pending' / 'r1.json'\n"
            "if src.is_file():\n"
            "    dst = root / 'reports' / 'archive' / 'r1.json'\n"
            "    dst.parent.mkdir(parents=True, exist_ok=True)\n"
            "    dst.write_bytes(src.read_bytes()); src.unlink()\n"
            "print(json.dumps({'transition': 'report_to_task'}))\n",
            encoding="utf-8",
        )

        manifest = {
            "schema_version": 1,
            "files": [
                "autonomy/config/workspace.manifest.json",
                "autonomy/config/policy.json",
                "autonomy/audit/events.jsonl",
                "autonomy/state/circuit-breaker.json",
                "autonomy/reports/pending/r1.json",
            ],
            "required": ["autonomy/config/policy.json"],
            "append_only": ["autonomy/audit/events.jsonl"],
            "directories": ["autonomy/tasks/pending"],
        }
        (stage / "workspace.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (stage / "policy.json").write_text("{}\n", encoding="utf-8")
        (stage / "events.jsonl").write_text('{"event": "old"}\n', encoding="utf-8")
        (stage / "circuit-breaker.json").write_text('{"open": false}\n', encoding="utf-8")
        (stage / "r1.json").write_text('{"id": "r1"}\n', encoding="utf-8")

        result = tick(stage, root, owner="test-1")
        paths = {e["path"] for e in result["plan"]["writes"]}

        check("nested tree rebuilt from flat staging", (root / "autonomy" / "config" / "policy.json").is_file())
        check("declared directories created", (root / "autonomy" / "tasks" / "pending").is_dir())
        check("dispatcher ran", result["dispatcher"]["transition"] == "report_to_task")
        check("audit append captured", "autonomy/audit/events.jsonl" in paths)
        check("lease is persisted without being hand-listed", "autonomy/state/lease.json" in paths)
        check("queue move captured as a write", "autonomy/reports/archive/r1.json" in paths)
        check("queue move captured as a delete", "autonomy/reports/pending/r1.json" in result["plan"]["deletes"])
        check("untouched file is not rewritten", "autonomy/config/policy.json" not in paths)
        check("append-only growth verified", result["plan"]["append_only_verified"] == ["autonomy/audit/events.jsonl"])
        check("persist_required set", result["persist_required"])
        audit_entry = next(e for e in result["plan"]["writes"] if e["path"].endswith("events.jsonl"))
        check("audit content preserves history", audit_entry["content"].startswith('{"event": "old"}'))
        check("plan is content-hashed", len(result["plan"]["plan_sha256"]) == 64)

        # missing required file is a hard error, not a partial run
        stage2 = work / "stage2"
        stage2.mkdir()
        shutil.copy(stage / "workspace.manifest.json", stage2 / "workspace.manifest.json")
        shutil.copy(stub, stage2 / "autonomy_dispatcher.py")
        check("missing required staging aborts", _raises(lambda: tick(stage2, work / "ws2", owner="t")))

        # flat-staging collision is refused
        collide = {"schema_version": 1, "files": ["a/policy.json", "b/policy.json"]}
        collide_path = work / "collide.json"
        collide_path.write_text(json.dumps(collide), encoding="utf-8")
        check("basename collision refused", _raises(lambda: load_manifest(collide_path)))

        # append_only naming an unmanaged file is refused
        stray = {"schema_version": 1, "files": ["a.json"], "append_only": ["ghost.jsonl"]}
        stray_path = work / "stray.json"
        stray_path.write_text(json.dumps(stray), encoding="utf-8")
        check("append_only outside manifest refused", _raises(lambda: load_manifest(stray_path)))

        # truncating an append-only file aborts the tick
        before = {"autonomy/audit/events.jsonl": "x"}
        after = {"autonomy/audit/events.jsonl": "y"}
        trunc_root = work / "trunc"
        (trunc_root / "autonomy" / "audit").mkdir(parents=True)
        (trunc_root / "autonomy" / "audit" / "events.jsonl").write_text("SHORT\n", encoding="utf-8")
        check("append-only truncation aborts", _raises(
            lambda: make_plan(trunc_root, before, after,
                              {"autonomy/audit/events.jsonl": b'{"event": "old"}\n'},
                              ["autonomy/audit/events.jsonl"])))

        # deleting an append-only file is refused
        check("append-only deletion refused", _raises(
            lambda: make_plan(trunc_root, {"autonomy/audit/events.jsonl": "x"}, {},
                              {}, ["autonomy/audit/events.jsonl"])))

        # binary content is reported, never silently dropped
        bin_root = work / "bin"
        bin_root.mkdir()
        (bin_root / "blob.dat").write_bytes(b"\xff\xfe\x00binary")
        binplan = make_plan(bin_root, {}, {"blob.dat": "z"}, {}, [])
        check("binary file refused, not silently written", binplan["binary_refused"] == ["blob.dat"])
        check("binary file absent from writes", binplan["writes"] == [])

        # path escape
        check("path escape refused", _raises(lambda: resolve_inside(root, "../escape.txt")))

        # probation runs INSIDE the tick, so its writes land in the same plan
        here = Path(__file__).resolve().parent
        if (here / "probation.py").is_file() and (here / "snapshot.py").is_file():
            pstage = work / "stage4"
            pstage.mkdir()
            for rel in manifest["files"]:
                src = root / rel
                if src.is_file():
                    shutil.copy(src, pstage / Path(rel).name)
            shutil.copy(stub, pstage / "autonomy_dispatcher.py")
            shutil.copy(here / "probation.py", pstage / "probation.py")
            shutil.copy(here / "snapshot.py", pstage / "snapshot.py")
            (pstage / "regression.json").write_text(
                json.dumps({"schema_version": 1, "suite": [{"id": "noop", "script": "noop.py"}]}),
                encoding="utf-8")
            (pstage / "noop.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            pres = tick(pstage, work / "ws4", owner="test-p", probation=True)
            check("probation runs inside the tick", pres["probation"]["status"] == "no_active_probation")
        else:
            check("probation integration (siblings absent, skipped)", True)

        # a second identical tick produces no writes for unchanged files
        idle_stage = work / "stage3"
        idle_stage.mkdir()
        for rel in manifest["files"]:
            src = root / rel
            if src.is_file():
                shutil.copy(src, idle_stage / Path(rel).name)
        shutil.copy(stub, idle_stage / "autonomy_dispatcher.py")
        second = tick(idle_stage, work / "ws3", owner="test-2")
        untouched = [e["path"] for e in second["plan"]["writes"]]
        check("unchanged files stay out of the second plan", "autonomy/config/policy.json" not in untouched)
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
    parser = argparse.ArgumentParser(description="Run one autonomy tick and emit a persistence plan.")
    parser.add_argument("--stage-dir", default="/tmp")
    parser.add_argument("--root", default="/tmp/ws")
    parser.add_argument("--owner", default="tick")
    parser.add_argument("--heal", action="store_true")
    parser.add_argument("--probation", action="store_true")
    parser.add_argument("--evolution", action="store_true")
    parser.add_argument("--no-dispatch", action="store_true")
    parser.add_argument("--out", default="/tmp/plan.json")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    try:
        result = tick(Path(args.stage_dir), Path(args.root), args.owner,
                      heal=args.heal, dispatch=not args.no_dispatch,
                      probation=args.probation, evolution=args.evolution)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2))
        return 2

    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["persist_required"] else 1


if __name__ == "__main__":
    sys.exit(main())
