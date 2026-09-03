#!/usr/bin/env python3
"""Tier-1 convergence engine: declare the target state, repair the divergence.

This is deliberately NOT "a model notices something is wrong and fixes it".
Invariants are declared in JSON. Checks and repairs come from a fixed enum
implemented here in code. The engine cannot invent a repair, and divergence it
has no repair for becomes a report for the operator rather than an improvisation.

Safety properties, in order of importance:

  1. HARD_IMMUTABLE is compiled into this file. Even if the config is emptied
     or rewritten, the regression suite, the snapshot machinery, the invariants
     declaration and the identity files stay untouchable. This is the fixed
     point: without it, the cheapest way to satisfy a failing check is to
     delete the check.
  2. Nothing is applied without a snapshot first. Every repair is reversible
     via snapshot.py restore.
  3. An unknown check or repair name is a hard error, never a silent skip.
  4. Every path is resolved and proven to sit inside --base.
  5. A rule that fires on three consecutive runs is quarantined. A converged
     system does not need the same repair every tick; a rule that does is
     either oscillating against another rule or fighting an external writer.
  6. The audit log is appended by this program, never rewritten by a caller.

Exit codes: 0 converged, 1 divergence remains, 2 refused or misconfigured.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot as snap  # noqa: E402

SCHEMA_VERSION = 1
OSCILLATION_WINDOW = 3

# The fixed point. Compiled in, unioned with whatever the config declares,
# and never reducible by editing the config.
HARD_IMMUTABLE = (
    "SOUL.md",
    "IDENTITY.md",
    "scripts/self_heal.py",
    "scripts/snapshot.py",
    "scripts/probation.py",
    "scripts/tick.py",
    "scripts/evolution.py",
    "scripts/test_*.py",
    "autonomy/config/invariants.json",
    "autonomy/config/regression.json",
    "autonomy/config/evolution.json",
    "autonomy/state/probation.json",
    "autonomy/snapshots/*",
    "autonomy/snapshots/**",
)

# Destructive repairs are refused on immutable paths. Restorative repairs are
# allowed there and always flagged: putting a file back to a previously
# recorded state is how the loop heals its own core, and a restore cannot
# introduce content that was never snapshotted in the first place.
DESTRUCTIVE_REPAIRS = {"delete_matching"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- checks

def _glob(base: Path, pattern: str) -> list[str]:
    if os.path.isabs(pattern):
        raise ValueError("absolute pattern is not accepted: " + pattern)
    return sorted(
        str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file()
    )


def check_file_exists(base, rule, now):
    target = rule["target"]
    snap.resolve_inside(base, target)
    return [] if (base / target).is_file() else [target]


def check_max_line_count(base, rule, now):
    target = rule["target"]
    path = snap.resolve_inside(base, target)
    if not path.is_file():
        return []
    count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return [target] if count > int(rule["arg"]) else []


def check_files_older_than(base, rule, now):
    cutoff = now - float(rule["arg"])
    out = []
    for rel in _glob(base, rule["target"]):
        if (base / rel).stat().st_mtime < cutoff:
            out.append(rel)
    return out


def check_glob_nonempty(base, rule, now):
    return _glob(base, rule["target"])


CHECKS = {
    "file_exists": check_file_exists,
    "max_line_count": check_max_line_count,
    "files_older_than": check_files_older_than,
    "glob_nonempty": check_glob_nonempty,
}


# --------------------------------------------------------------- repairs

def repair_report_only(base, store, paths, rule):
    return []


def repair_delete_matching(base, store, paths, rule):
    done = []
    for rel in paths:
        path = snap.resolve_inside(base, rel)
        if path.is_file():
            path.unlink()
            done.append({"action": "delete", "path": rel})
    return done


def repair_restore_from_snapshot(base, store, paths, rule):
    done = []
    for rel in paths:
        snap.resolve_inside(base, rel)
        snapshot_id = snap.find_latest_containing(store, rel)
        if snapshot_id is None:
            done.append({"action": "restore_failed", "path": rel, "reason": "no snapshot contains this path"})
            continue
        result = snap.restore(store, base, snapshot_id, apply=True, only=[rel])
        done.append(
            {
                "action": "restore",
                "path": rel,
                "snapshot_id": snapshot_id,
                "restored": bool(result["restored"]),
            }
        )
    return done


REPAIRS = {
    "report_only": repair_report_only,
    "delete_matching": repair_delete_matching,
    "restore_from_snapshot": repair_restore_from_snapshot,
}


# ----------------------------------------------------------------- engine

def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported invariants schema_version")
    seen = set()
    for rule in config["rules"]:
        for key in ("id", "check", "repair", "target"):
            if key not in rule:
                raise ValueError("rule missing '" + key + "': " + json.dumps(rule))
        if rule["id"] in seen:
            raise ValueError("duplicate rule id: " + rule["id"])
        seen.add(rule["id"])
        if rule["check"] not in CHECKS:
            raise ValueError("unknown check '" + rule["check"] + "' in rule " + rule["id"])
        if rule["repair"] not in REPAIRS:
            raise ValueError("unknown repair '" + rule["repair"] + "' in rule " + rule["id"])
    return config


def immutable_patterns(config: dict) -> list[str]:
    return sorted(set(HARD_IMMUTABLE) | set(config.get("immutable", [])))


def is_immutable(relpath: str, patterns) -> bool:
    return any(fnmatch.fnmatch(relpath, pattern) for pattern in patterns)


def _history_path(base: Path) -> Path:
    return base / "autonomy" / "state" / "heal-history.json"


def load_history(base: Path) -> dict:
    path = _history_path(base)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_history(base: Path, history: dict) -> None:
    history["runs"] = history["runs"][-OSCILLATION_WINDOW * 2:]
    snap.atomic_write(
        _history_path(base),
        json.dumps(history, indent=2, sort_keys=True).encode("utf-8"),
    )


def quarantined_rules(history: dict) -> set[str]:
    runs = history.get("runs", [])
    if len(runs) < OSCILLATION_WINDOW:
        return set()
    window = runs[-OSCILLATION_WINDOW:]
    common = set(window[0].get("fired", []))
    for entry in window[1:]:
        common &= set(entry.get("fired", []))
    return common


def append_audit(base: Path, record: dict) -> None:
    path = base / "autonomy" / "audit" / "heal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def run(base: Path, config_path: Path, store: Path, apply: bool = False, now=None) -> dict:
    now = time.time() if now is None else now
    base = base.resolve()
    config = load_config(config_path)
    patterns = immutable_patterns(config)
    history = load_history(base)
    quarantined = quarantined_rules(history)

    divergences, refused, fired, immutable_restores = [], [], [], []

    for rule in config["rules"]:
        paths = CHECKS[rule["check"]](base, rule, now)
        if not paths:
            continue

        entry = {
            "rule": rule["id"],
            "severity": rule.get("severity", "medium"),
            "repair": rule["repair"],
            "paths": paths,
            "status": "pending",
        }

        blocked = [p for p in paths if is_immutable(p, patterns)]
        if blocked and rule["repair"] in DESTRUCTIVE_REPAIRS:
            entry["status"] = "refused_immutable"
            entry["blocked"] = blocked
            refused.append(entry)
            divergences.append(entry)
            continue
        if blocked and rule["repair"] != "report_only":
            entry["immutable_touch"] = blocked
            immutable_restores.append(rule["id"])

        if rule["id"] in quarantined:
            entry["status"] = "quarantined_oscillation"
            divergences.append(entry)
            continue

        if rule["repair"] == "report_only":
            entry["status"] = "reported"
            divergences.append(entry)
            continue

        entry["status"] = "planned" if not apply else "repairing"
        divergences.append(entry)
        if apply:
            fired.append(rule["id"])

    snapshot_id = None
    actions = []
    if apply and fired:
        targets = sorted({p for d in divergences if d["status"] == "repairing" for p in d["paths"]})
        existing = [p for p in targets if (base / p).is_file()]
        if existing:
            snapshot_id = snap.create(store, base, existing, label="pre-heal")["id"]
        for entry in divergences:
            if entry["status"] != "repairing":
                continue
            done = REPAIRS[entry["repair"]](base, store, entry["paths"], entry)
            entry["status"] = "repaired"
            entry["actions"] = done
            actions.extend(done)

    if apply:
        history.setdefault("runs", []).append(
            {"at": utcnow(), "fired": sorted(set(fired))}
        )
        save_history(base, history)

    unresolved = [d for d in divergences if d["status"] not in ("repaired",)]
    result = {
        "schema_version": SCHEMA_VERSION,
        "at": utcnow(),
        "applied": bool(apply),
        "snapshot_id": snapshot_id,
        "rules_evaluated": len(config["rules"]),
        "divergences": divergences,
        "actions": actions,
        "refused": [d["rule"] for d in refused],
        "immutable_restores": sorted(set(immutable_restores)),
        "quarantined": sorted(quarantined),
        "converged": not divergences,
        "unresolved": [d["rule"] for d in unresolved],
    }
    append_audit(base, {k: result[k] for k in ("at", "applied", "snapshot_id", "actions", "refused", "immutable_restores", "quarantined", "unresolved", "converged")})
    return result


# -------------------------------------------------------------- self-test

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

    work = Path(tempfile.mkdtemp(prefix="healtest-"))
    try:
        base = work / "ws"
        store = base / "autonomy" / "snapshots"
        (base / "autonomy" / "config").mkdir(parents=True)
        (base / "autonomy" / "state").mkdir(parents=True)
        (base / "scripts").mkdir(parents=True)
        (base / "junk").mkdir(parents=True)

        (base / "scripts" / "critical.py").write_text("print('live')\n", encoding="utf-8")
        (base / "SOUL.md").write_text("identity\n", encoding="utf-8")
        (base / "MEMORY.md").write_text("\n".join("line %d" % i for i in range(10)) + "\n", encoding="utf-8")

        config = {
            "schema_version": 1,
            "immutable": ["scripts/critical.py"],
            "rules": [
                {"id": "debris", "check": "files_older_than", "target": "junk/*", "arg": 100,
                 "repair": "delete_matching", "severity": "low"},
                {"id": "memcap", "check": "max_line_count", "target": "MEMORY.md", "arg": 200,
                 "repair": "report_only", "severity": "high"},
                {"id": "dispatcher", "check": "file_exists", "target": "scripts/dispatcher.py",
                 "repair": "restore_from_snapshot", "severity": "critical"},
            ],
        }
        config_path = base / "autonomy" / "config" / "invariants.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        # baseline: a real dispatcher exists and is snapshotted, then deleted
        (base / "scripts" / "dispatcher.py").write_text("DISPATCHER v1\n", encoding="utf-8")
        snap.create(store, base, ["scripts/dispatcher.py", "scripts/critical.py"], label="baseline")

        result = run(base, config_path, store, apply=False)
        check("clean tree converges", result["converged"])
        check("exit-clean run records no actions", result["actions"] == [])

        # plant divergence: old debris + missing critical file
        now = time.time()
        for name in ("a.tmp", "b.tmp"):
            path = base / "junk" / name
            path.write_text("x", encoding="utf-8")
            os.utime(path, (now - 9999, now - 9999))
        (base / "scripts" / "dispatcher.py").unlink()

        dry = run(base, config_path, store, apply=False)
        check("dry run sees both divergences", len(dry["divergences"]) == 2)
        check("dry run deletes nothing", (base / "junk" / "a.tmp").is_file())
        check("dry run restores nothing", not (base / "scripts" / "dispatcher.py").is_file())
        check("dry run is not converged", not dry["converged"])

        applied = run(base, config_path, store, apply=True)
        check("debris deleted", not (base / "junk" / "a.tmp").is_file())
        check("critical file restored", (base / "scripts" / "dispatcher.py").read_text() == "DISPATCHER v1\n")
        check("snapshot taken before repair", applied["snapshot_id"] is not None)
        check("repairs are recorded", len(applied["actions"]) >= 2)

        after = run(base, config_path, store, apply=False)
        check("system converges after repair", after["converged"])

        # immutable guard: a rule that would delete a protected file is refused
        evil = dict(config)
        evil["rules"] = config["rules"] + [
            {"id": "attack", "check": "glob_nonempty", "target": "scripts/critical.py",
             "repair": "delete_matching", "severity": "low"}
        ]
        evil_path = base / "autonomy" / "config" / "evil.json"
        evil_path.write_text(json.dumps(evil), encoding="utf-8")
        blocked = run(base, evil_path, store, apply=True)
        check("immutable repair refused", "attack" in blocked["refused"])
        check("immutable file survives", (base / "scripts" / "critical.py").is_file())

        # hard floor holds even when the config declares nothing immutable
        naked = dict(evil)
        naked["immutable"] = []
        naked["rules"] = config["rules"] + [
            {"id": "attack2", "check": "glob_nonempty", "target": "SOUL.md",
             "repair": "delete_matching", "severity": "low"}
        ]
        naked_path = base / "autonomy" / "config" / "naked.json"
        naked_path.write_text(json.dumps(naked), encoding="utf-8")
        floor = run(base, naked_path, store, apply=True)
        check("hard floor refuses SOUL.md deletion", "attack2" in floor["refused"])
        check("SOUL.md survives an emptied immutable list", (base / "SOUL.md").is_file())

        # a restorative repair on an immutable path IS allowed, and is flagged
        (base / "scripts" / "critical.py").unlink()
        heal_cfg = {
            "schema_version": 1,
            "immutable": ["scripts/critical.py"],
            "rules": [
                {"id": "restore-immutable", "check": "file_exists", "target": "scripts/critical.py",
                 "repair": "restore_from_snapshot", "severity": "critical"}
            ],
        }
        heal_path = _write_json(base / "autonomy" / "config" / "heal_imm.json", heal_cfg)
        restored = run(base, heal_path, store, apply=True)
        check("restore of an immutable path is allowed", (base / "scripts" / "critical.py").read_text() == "print('live')\n")
        check("immutable restore is flagged", restored["immutable_restores"] == ["restore-immutable"])
        check("immutable restore is not silently refused", restored["refused"] == [])

        # unknown check and unknown repair are hard errors
        for key, value in (("check", "rm_minus_rf"), ("repair", "improvise")):
            bad = {"schema_version": 1, "rules": [dict(config["rules"][0])]}
            bad["rules"][0][key] = value
            bad_path = base / "autonomy" / "config" / ("bad_" + key + ".json")
            bad_path.write_text(json.dumps(bad), encoding="utf-8")
            check("unknown " + key + " is rejected", _raises(lambda p=bad_path: load_config(p)))

        check("duplicate rule id rejected", _raises(
            lambda: load_config(_write_json(base / "autonomy" / "config" / "dup.json",
                                            {"schema_version": 1,
                                             "rules": [config["rules"][0], config["rules"][0]]}))))

        # path escape in a rule target
        esc = {"schema_version": 1, "rules": [
            {"id": "esc", "check": "file_exists", "target": "../outside.md",
             "repair": "report_only", "severity": "low"}]}
        esc_path = _write_json(base / "autonomy" / "config" / "esc.json", esc)
        check("path escape in target raises", _raises(lambda: run(base, esc_path, store, apply=False)))

        # oscillation: the same rule firing on three consecutive runs is quarantined
        osc_history = base / "autonomy" / "state" / "heal-history.json"
        if osc_history.exists():
            osc_history.unlink()
        for _ in range(OSCILLATION_WINDOW):
            path = base / "junk" / "recurring.tmp"
            path.write_text("x", encoding="utf-8")
            os.utime(path, (now - 9999, now - 9999))
            run(base, config_path, store, apply=True)
        path = base / "junk" / "recurring.tmp"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (now - 9999, now - 9999))
        quarantine = run(base, config_path, store, apply=True)
        check("repeat offender is quarantined", "debris" in quarantine["quarantined"])
        check("quarantined rule stops acting", (base / "junk" / "recurring.tmp").is_file())

        # audit log is append-only and grew across runs
        audit = base / "autonomy" / "audit" / "heal.jsonl"
        lines = audit.read_text(encoding="utf-8").strip().splitlines()
        check("audit log has one line per run", len(lines) >= 10)
        check("audit lines are valid json", all(json.loads(line) for line in lines))
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
    parser = argparse.ArgumentParser(description="Declarative convergence engine.")
    parser.add_argument("--base", help="workspace root")
    parser.add_argument("--config", help="path to invariants.json")
    parser.add_argument("--store", help="snapshot store directory")
    parser.add_argument("--apply", action="store_true", help="execute repairs (default is dry run)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.base:
        parser.error("--base is required")
    base = Path(args.base)
    config = Path(args.config) if args.config else base / "autonomy" / "config" / "invariants.json"
    store = Path(args.store) if args.store else base / "autonomy" / "snapshots"

    try:
        result = run(base, config, store, apply=args.apply)
    except Exception as exc:  # configuration or safety refusal
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    if result["converged"]:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
