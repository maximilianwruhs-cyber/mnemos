#!/usr/bin/env python3
"""Probation: no self-modification is trusted until the next run judges it.

Tiers 2 and 3 let the loop add and rewrite its own machinery. The property that
makes that survivable is not review, it is reversibility with a deadline:

    begin     snapshot the affected paths, record a probationary change
    apply     (the caller makes the change)
    evaluate  run the regression suite
                 all green  -> promote, keep the snapshot as history
                 any red    -> restore the snapshot byte-exact,
                               open the circuit breaker, stop the loop

The tick after a change is the one that judges it. A change that breaks the
system is reverted by the system, without a human in the loop and without the
changed code getting a vote.

Two refusals worth naming:
  * A suite in which every entry was skipped is NOT a pass. Absence of a
    failure is not evidence of success.
  * Only one probation may be active at a time. Stacking unjudged changes
    destroys the property that makes rollback meaningful.

Exit codes: 0 promoted or nothing pending, 1 reverted, 2 refused.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot as snap  # noqa: E402

SCHEMA_VERSION = 1
STATE = "autonomy/state/probation.json"
CIRCUIT = "autonomy/state/circuit-breaker.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_state(base: Path) -> dict:
    path = base / STATE
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "active": None, "history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(base: Path, state: dict) -> None:
    state["history"] = state.get("history", [])[-25:]
    snap.atomic_write(base / STATE, json.dumps(state, indent=2, sort_keys=True).encode("utf-8"))


def open_circuit(base: Path, reason: str) -> None:
    snap.atomic_write(
        base / CIRCUIT,
        json.dumps({"open": True, "opened_at": utcnow(), "reason": reason}, indent=2, sort_keys=True).encode("utf-8"),
    )


def begin(base: Path, store: Path, paths, reason: str) -> dict:
    state = load_state(base)
    if state.get("active"):
        raise RuntimeError("a probation is already active: " + state["active"]["change_id"] + " (evaluate it first)")
    for rel in paths:
        snap.resolve_inside(base, rel)
    manifest = snap.create(store, base, list(paths), label="probation")
    change_id = "chg-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    state["active"] = {
        "change_id": change_id,
        "started_at": utcnow(),
        "snapshot_id": manifest["id"],
        "paths": sorted(paths),
        "missing_at_snapshot": sorted(manifest["missing"]),
        "reason": reason,
        "evaluations": 0,
    }
    save_state(base, state)
    return {"status": "probation_started", "change_id": change_id, "snapshot_id": manifest["id"],
            "paths": sorted(paths), "missing_at_snapshot": manifest["missing"]}


def load_suite(path: Path) -> list[dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported regression suite schema_version")
    suite = config.get("suite", [])
    if not suite:
        raise ValueError("regression suite is empty")
    for entry in suite:
        for key in ("id", "script"):
            if key not in entry:
                raise ValueError("suite entry missing '" + key + "'")
    return suite


def run_suite(suite, scripts_dir: Path, timeout: int = 300) -> dict:
    results, executed = [], 0
    for entry in suite:
        script = scripts_dir / Path(entry["script"]).name
        if not script.is_file():
            if entry.get("optional"):
                results.append({"id": entry["id"], "status": "skipped", "reason": "not staged"})
                continue
            results.append({"id": entry["id"], "status": "failed", "reason": "required script not staged"})
            continue
        proc = subprocess.run(
            [sys.executable, "-B", str(script)] + list(entry.get("args", [])),
            capture_output=True, text=True, timeout=timeout,
        )
        executed += 1
        results.append({
            "id": entry["id"],
            "status": "passed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "tail": (proc.stdout or proc.stderr)[-300:].strip(),
        })
    failed = [r["id"] for r in results if r["status"] == "failed"]
    return {
        "results": results,
        "executed": executed,
        "failed": failed,
        # an all-skipped suite is not a pass: absence of failure is not success
        "green": bool(executed) and not failed,
    }


def evaluate(base: Path, store: Path, suite_path: Path, scripts_dir: Path) -> dict:
    state = load_state(base)
    active = state.get("active")
    if not active:
        return {"status": "no_active_probation", "green": True}

    suite = run_suite(load_suite(suite_path), scripts_dir)
    active["evaluations"] = active.get("evaluations", 0) + 1
    record = {
        "change_id": active["change_id"],
        "snapshot_id": active["snapshot_id"],
        "at": utcnow(),
        "suite": suite,
    }

    if suite["green"]:
        record["outcome"] = "promoted"
        state["history"].append(record)
        state["active"] = None
        save_state(base, state)
        return {"status": "promoted", "change_id": record["change_id"], "suite": suite}

    restored = snap.restore(store, base, active["snapshot_id"], apply=True, only=active["paths"])
    removed = []
    for rel in active.get("missing_at_snapshot", []):
        target = snap.resolve_inside(base, rel)
        if target.is_file():
            target.unlink()
            removed.append(rel)
    reason = "probation_failed:" + active["change_id"] + ":" + ",".join(suite["failed"] or ["all_skipped"])
    open_circuit(base, reason)
    record["outcome"] = "reverted"
    record["restored"] = restored["restored"]
    record["removed_new"] = removed
    state["history"].append(record)
    state["active"] = None
    save_state(base, state)
    return {"status": "reverted", "change_id": record["change_id"], "restored": restored["restored"],
            "removed_new": removed, "circuit_opened": True, "reason": reason, "suite": suite}


# ---------------------------------------------------------------- self-test

def _selftest() -> int:
    import shutil
    import tempfile

    failures = []

    def check(name, condition):
        if condition:
            print("  ok   " + name)
        else:
            print("  FAIL " + name)
            failures.append(name)

    work = Path(tempfile.mkdtemp(prefix="probtest-"))
    try:
        base = work / "ws"
        store = base / "autonomy" / "snapshots"
        scripts = work / "scripts"
        scripts.mkdir()
        (base / "autonomy" / "state").mkdir(parents=True)
        (base / "scripts").mkdir(parents=True)
        (base / "scripts" / "engine.py").write_text("VERSION = 1\n", encoding="utf-8")

        (scripts / "pass.py").write_text("import sys; print('ok'); sys.exit(0)\n", encoding="utf-8")
        (scripts / "fail.py").write_text("import sys; print('boom'); sys.exit(1)\n", encoding="utf-8")

        good = {"schema_version": 1, "suite": [{"id": "a", "script": "pass.py"}]}
        bad = {"schema_version": 1, "suite": [{"id": "a", "script": "pass.py"}, {"id": "b", "script": "fail.py"}]}
        allskip = {"schema_version": 1, "suite": [{"id": "z", "script": "ghost.py", "optional": True}]}
        good_p = _write_json(work / "good.json", good)
        bad_p = _write_json(work / "bad.json", bad)
        skip_p = _write_json(work / "skip.json", allskip)

        check("evaluate with nothing pending is a no-op",
              evaluate(base, store, good_p, scripts)["status"] == "no_active_probation")

        started = begin(base, store, ["scripts/engine.py"], reason="bump version")
        check("begin snapshots the path", started["status"] == "probation_started")
        check("state records the active change", load_state(base)["active"]["change_id"] == started["change_id"])
        check("second begin is refused", _raises(lambda: begin(base, store, ["scripts/engine.py"], reason="again")))

        # a good change survives evaluation
        (base / "scripts" / "engine.py").write_text("VERSION = 2\n", encoding="utf-8")
        promoted = evaluate(base, store, good_p, scripts)
        check("green suite promotes", promoted["status"] == "promoted")
        check("promoted change is kept", (base / "scripts" / "engine.py").read_text() == "VERSION = 2\n")
        check("active is cleared after promotion", load_state(base)["active"] is None)
        check("history records the promotion", load_state(base)["history"][-1]["outcome"] == "promoted")

        # a bad change is reverted and the circuit opens
        begin(base, store, ["scripts/engine.py"], reason="risky rewrite")
        (base / "scripts" / "engine.py").write_text("SYNTAX ERROR ((((\n", encoding="utf-8")
        reverted = evaluate(base, store, bad_p, scripts)
        check("red suite reverts", reverted["status"] == "reverted")
        check("file restored byte-exact", (base / "scripts" / "engine.py").read_text() == "VERSION = 2\n")
        check("circuit breaker opened", json.loads((base / CIRCUIT).read_text())["open"] is True)
        check("revert reason names the failing test",
              "fail" in reverted["reason"] or "b" in reverted["reason"])
        check("active cleared after revert", load_state(base)["active"] is None)

        # a file created during probation must be deleted again on failure
        (base / CIRCUIT).write_text('{"open": false}', encoding="utf-8")
        begin(base, store, ["scripts/new_engine.py"], reason="new file")
        (base / "scripts" / "new_engine.py").write_text("BROKEN = True\n", encoding="utf-8")
        new_revert = evaluate(base, store, bad_p, scripts)
        check("new file rollback reports deletion", "scripts/new_engine.py" in new_revert["removed_new"])
        check("new file removed on rollback", not (base / "scripts" / "new_engine.py").exists())

        # an all-skipped suite must not count as a pass
        (base / CIRCUIT).write_text('{"open": false}', encoding="utf-8")
        begin(base, store, ["scripts/engine.py"], reason="sneaky")
        (base / "scripts" / "engine.py").write_text("VERSION = 999\n", encoding="utf-8")
        skipped = evaluate(base, store, skip_p, scripts)
        check("all-skipped suite is not a pass", skipped["status"] == "reverted")
        check("all-skipped revert restores", (base / "scripts" / "engine.py").read_text() == "VERSION = 2\n")

        # a required script that was not staged fails rather than skipping
        req = {"schema_version": 1, "suite": [{"id": "r", "script": "ghost.py"}]}
        out = run_suite(load_suite(_write_json(work / "req.json", req)), scripts)
        check("missing required script fails", out["failed"] == ["r"] and not out["green"])

        check("empty suite refused", _raises(lambda: load_suite(_write_json(work / "empty.json",
                                                                            {"schema_version": 1, "suite": []}))))
        check("bad schema refused", _raises(lambda: load_suite(_write_json(work / "sch.json",
                                                                           {"schema_version": 9, "suite": [{"id": "a", "script": "x"}]}))))
        check("path escape refused", _raises(lambda: begin(base, store, ["../outside.py"], reason="escape")))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASSED")
    return 0


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Snapshot-backed probation for self-modification.")
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_begin = sub.add_parser("begin")
    p_begin.add_argument("--base", required=True)
    p_begin.add_argument("--store")
    p_begin.add_argument("--paths", nargs="+", required=True)
    p_begin.add_argument("--reason", default="")

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--base", required=True)
    p_eval.add_argument("--store")
    p_eval.add_argument("--suite")
    p_eval.add_argument("--scripts-dir", default="/tmp")

    p_status = sub.add_parser("status")
    p_status.add_argument("--base", required=True)

    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.command:
        parser.print_help()
        return 2

    base = Path(args.base)
    store = Path(args.store) if getattr(args, "store", None) else base / "autonomy" / "snapshots"

    try:
        if args.command == "begin":
            out = begin(base, store, args.paths, args.reason)
        elif args.command == "status":
            out = load_state(base)
        else:
            suite = Path(args.suite) if args.suite else base / "autonomy" / "config" / "regression.json"
            out = evaluate(base, store, suite, Path(args.scripts_dir))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2))
        return 2

    print(json.dumps(out, indent=2, sort_keys=True))
    return 1 if out.get("status") == "reverted" else 0


if __name__ == "__main__":
    sys.exit(main())
