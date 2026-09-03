"""verifier_kit.py — deterministic verification instruments for THIS runtime.

Capability-bound to the SiemensGPT Python 3.12 sandbox (VERIFIED 2026-08-29):
  present : sympy 1.14, jsonschema 4.26, networkx 3.6, numpy, scipy, difflib, ast,
            hashlib, subprocess + resource rlimits + SIGALRM, 6 CPUs
  absent  : z3, cvc5, Lean4, pydantic, pytest, hypothesis, mypy/ruff/pylint,
            Levenshtein/rapidfuzz, RestrictedPython, any network egress, any NLI model

Every function returns a receipt-bearing dict. Nothing here guesses: unavailable
capability yields status UNKNOWN / UNVERIFIABLE, never a fabricated pass.

Run `python verifier_kit.py --selftest` to prove the kit works before trusting it.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "receipt", "run_sandboxed", "functional_clustering", "prove", "check_schema",
    "build_dag", "levenshtein", "ast_distance", "oscillation_check",
    "conformal_threshold", "load_calibration", "validate_report", "REPORT_SCHEMA",
]

# --------------------------------------------------------------------------- #
# receipts
# --------------------------------------------------------------------------- #

def receipt(tool: str, payload: str, **fields: Any) -> Dict[str, Any]:
    """Evidence receipt. invocation_hash binds the receipt to the exact input."""
    r = {
        "tool": tool,
        "invocation_hash": "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "exit_code": None,
        "stdout_excerpt": "",
        "stderr_excerpt": "",
        "solver_status": None,
        "source_urls": [],
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    r.update(fields)
    return r


def _clip(s: str, n: int = 2000) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n...[{len(s)-n} chars truncated]"


# --------------------------------------------------------------------------- #
# 1. code execution  (gVisor substitute: child process + rlimits + timeout)
# --------------------------------------------------------------------------- #

_PREAMBLE = """
import resource, sys
resource.setrlimit(resource.RLIMIT_AS, ({mem}, {mem}))
resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu}))
resource.setrlimit(resource.RLIMIT_FSIZE, ({fsz}, {fsz}))
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
"""


def run_sandboxed(code: str, *, stdin: str = "", timeout_s: float = 10.0,
                  mem_mb: int = 512, cpu_s: int = 10, fsize_mb: int = 16) -> Dict[str, Any]:
    """Execute untrusted code in an isolated child interpreter.

    Isolation actually enforced here: separate process, `-I` (ignore env + user
    site), wall-clock timeout, address-space / CPU-time / file-size / process
    rlimits, and a sandbox that already has zero network egress.
    NOT enforced: filesystem confinement. Do not hand this code paths you care about.
    """
    prelude = _PREAMBLE.format(mem=mem_mb * 1024 * 1024, cpu=cpu_s, fsz=fsize_mb * 1024 * 1024)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir="/tmp") as fh:
        fh.write(prelude + "\n" + code)
        path = fh.name
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            input=stdin, capture_output=True, text=True, timeout=timeout_s,
            cwd="/tmp", env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        rc, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        rc, out, err, timed_out = None, (exc.stdout or ""), (exc.stderr or ""), True
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    wall_ms = int((time.time() - started) * 1000)
    return {
        "success": (rc == 0) and not timed_out,
        "timed_out": timed_out,
        "exit_code": rc,
        "stdout": out,
        "stderr": err,
        "wall_ms": wall_ms,
        "receipt": receipt("code_execution", code, exit_code=rc,
                           stdout_excerpt=_clip(out), stderr_excerpt=_clip(err),
                           solver_status="TIMEOUT" if timed_out else None),
    }


def syntax_check(code: str) -> Dict[str, Any]:
    """Deterministic parse gate. Cheaper than execution; run it first."""
    try:
        ast.parse(code)
        return {"success": True, "error": None,
                "receipt": receipt("static_parse", code, exit_code=0,
                                   stdout_excerpt="ast.parse OK")}
    except SyntaxError as exc:
        msg = f"{type(exc).__name__}: {exc.msg} at line {exc.lineno}, col {exc.offset}"
        return {"success": False, "error": msg, "lineno": exc.lineno,
                "receipt": receipt("static_parse", code, exit_code=1, stderr_excerpt=msg)}


# --------------------------------------------------------------------------- #
# 2. functional clustering  (behavioural equivalence over generated inputs)
# --------------------------------------------------------------------------- #

def functional_clustering(candidates: Sequence[str], entrypoint: str,
                          test_inputs: Sequence[Any], *, timeout_s: float = 10.0
                          ) -> Dict[str, Any]:
    """Group candidate programs by observed I/O behaviour, not by source text.

    Returns clusters keyed by a hash of the output vector, with empirical mass.
    The highest-mass cluster is evidence; a singleton cluster is a smell.
    """
    harness = (
        "{src}\n\n"
        "import json, traceback\n"
        "_res = []\n"
        "for _inp in {inputs!r}:\n"
        "    try:\n"
        "        _res.append(['ok', repr({ep}(*_inp))])\n"
        "    except Exception as _e:\n"
        "        _res.append(['exc', type(_e).__name__])\n"
        "print(json.dumps(_res))\n"
    )
    profiles: List[Optional[str]] = []
    runs: List[Dict[str, Any]] = []
    for src in candidates:
        run = run_sandboxed(harness.format(src=src, inputs=list(test_inputs), ep=entrypoint),
                            timeout_s=timeout_s)
        runs.append(run)
        line = (run["stdout"] or "").strip().splitlines()[-1:] or [""]
        profiles.append(line[0] if run["success"] else None)

    clusters: Dict[str, Dict[str, Any]] = {}
    for idx, prof in enumerate(profiles):
        key = "ERROR" if prof is None else "sha256:" + hashlib.sha256(prof.encode()).hexdigest()[:16]
        c = clusters.setdefault(key, {"members": [], "behaviour": prof, "mass": 0.0})
        c["members"].append(idx)
    n = max(len(candidates), 1)
    for c in clusters.values():
        c["mass"] = round(len(c["members"]) / n, 4)
    dominant = max(clusters.items(), key=lambda kv: len(kv[1]["members"]))[0] if clusters else None
    return {
        "clusters": clusters,
        "dominant_cluster": dominant,
        "dominant_mass": clusters[dominant]["mass"] if dominant else 0.0,
        "unanimous": len(clusters) == 1,
        "runs": runs,
    }


# --------------------------------------------------------------------------- #
# 3. proof layer  (sympy stands in for Z3 — narrower, and says so)
# --------------------------------------------------------------------------- #

def prove(expression: str, *, assumptions: str = "", timeout_s: float = 8.0) -> Dict[str, Any]:
    """Attempt to prove `expression` holds universally by refuting its negation.

    Semantics mirror an SMT solver: UNSAT(negation) => claim holds; SAT => the
    counterexample is returned. sympy is strictly weaker than Z3 (no bitvectors,
    no arrays, no uninterpreted functions, incomplete nonlinear arithmetic), so
    UNKNOWN is a frequent and legitimate outcome. Never upgrade UNKNOWN to UNSAT.
    """
    src = f"""
import json, sympy as sp
from sympy import Symbol, symbols, Eq, Ne, And, Or, Not, Implies, S, Interval, oo
from sympy.abc import a, b, c, d, i, j, k, m, n, p, q, r, s, t, u, v, w, x, y, z
out = {{"status": "UNKNOWN", "counterexample": None, "detail": ""}}
try:
    expr = sp.sympify({expression!r})
    assum = sp.sympify({assumptions!r}) if {assumptions!r}.strip() else True
    goal = sp.simplify(sp.Implies(assum, expr)) if assum is not True else sp.simplify(expr)
    neg = sp.Not(goal)
    if goal is sp.true:
        out["status"] = "UNSAT"; out["detail"] = "goal simplifies to True"
    elif goal is sp.false:
        out["status"] = "SAT"; out["detail"] = "goal simplifies to False"
    elif not goal.free_symbols:
        b = bool(goal)
        out["status"] = "UNSAT" if b else "SAT"; out["detail"] = "ground term evaluated"
    elif all(getattr(sy, "is_Symbol", False) for sy in goal.free_symbols) and goal.atoms(sp.Symbol) and goal.is_Boolean:
        model = sp.satisfiable(neg)
        if model is False:
            out["status"] = "UNSAT"; out["detail"] = "propositional: negation unsatisfiable"
        else:
            out["status"] = "SAT"; out["counterexample"] = {{str(kk): bool(vv) for kk, vv in model.items()}}
            out["detail"] = "propositional counterexample"
    else:
        sol = sp.solveset(neg, list(goal.free_symbols)[0], domain=sp.S.Reals) \
              if len(goal.free_symbols) == 1 else None
        if sol is not None and sol == sp.S.EmptySet:
            out["status"] = "UNSAT"; out["detail"] = "negation has no real solution"
        elif sol is not None and sol != sp.S.EmptySet:
            try:
                witness = next(iter(sol.args)) if sol.args else sol
            except Exception:
                witness = sol
            out["status"] = "SAT"; out["counterexample"] = str(witness)
            out["detail"] = "real-domain counterexample"
        else:
            out["detail"] = "sympy could not decide (multivariate / nonlinear)"
except Exception as exc:
    out["detail"] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps(out))
"""
    run = run_sandboxed(src, timeout_s=timeout_s)
    parsed = {"status": "UNKNOWN", "counterexample": None, "detail": "solver process failed"}
    if run["success"]:
        try:
            parsed = json.loads((run["stdout"] or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001
            parsed["detail"] = f"unparseable solver output: {exc}"
    elif run["timed_out"]:
        parsed["detail"] = "solver timeout"
    return {
        "status": parsed["status"],                 # UNSAT = claim holds
        "claim_holds": parsed["status"] == "UNSAT",
        "counterexample": parsed.get("counterexample"),
        "detail": parsed.get("detail", ""),
        "engine": "sympy-1.14 (Z3 unavailable in this runtime)",
        "receipt": receipt("symbolic_prover", expression, solver_status=parsed["status"],
                           stdout_excerpt=_clip(run["stdout"]), stderr_excerpt=_clip(run["stderr"]),
                           exit_code=run["exit_code"]),
    }


# --------------------------------------------------------------------------- #
# 4. schema validation
# --------------------------------------------------------------------------- #

def check_schema(instance: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    import jsonschema
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    findings = [{"json_pointer": "/" + "/".join(str(p) for p in e.path),
                 "message": e.message, "validator": e.validator} for e in errors]
    payload = json.dumps({"instance": instance, "schema_title": schema.get("title", "")},
                         sort_keys=True, default=str)
    return {
        "success": not findings,
        "violations": findings,
        "receipt": receipt("schema_validation", payload, exit_code=0 if not findings else 1,
                           stdout_excerpt="valid" if not findings else json.dumps(findings)[:2000]),
    }


# --------------------------------------------------------------------------- #
# 5. claim DAG
# --------------------------------------------------------------------------- #

def build_dag(claims: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate the dependency graph and return a topological verification order.

    A cycle or a dangling dependency is a MalformedTaskGraph — a hard FAILED,
    not something to work around.
    """
    import networkx as nx
    ids = [c["claim_id"] for c in claims]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    g = nx.DiGraph()
    g.add_nodes_from(ids)
    dangling: List[Tuple[str, str]] = []
    for c in claims:
        for dep in c.get("dependencies", []):
            if dep not in ids:
                dangling.append((c["claim_id"], dep))
            else:
                g.add_edge(dep, c["claim_id"])
    try:
        cycles = list(nx.simple_cycles(g))
    except Exception:  # noqa: BLE001
        cycles = []
    acyclic = nx.is_directed_acyclic_graph(g)
    order = list(nx.topological_sort(g)) if acyclic else []
    return {
        "valid": acyclic and not dangling and not dupes,
        "acyclic": acyclic,
        "cycles": cycles,
        "dangling_dependencies": dangling,
        "duplicate_claim_ids": dupes,
        "topological_order": order,
        "descendants": {n: sorted(nx.descendants(g, n)) for n in ids} if acyclic else {},
        "receipt": receipt("dag_analysis", json.dumps(ids, sort_keys=True),
                           exit_code=0 if acyclic and not dangling else 1,
                           stdout_excerpt=json.dumps(order)[:2000]),
    }


def blocked_descendants(dag: Dict[str, Any], refuted: Iterable[str]) -> List[str]:
    """Claims that cannot be evaluated because a premise they depend on is REFUTED."""
    out: set = set()
    for r in refuted:
        out.update(dag.get("descendants", {}).get(r, []))
    return sorted(out - set(refuted))


# --------------------------------------------------------------------------- #
# 6. oscillation detection
# --------------------------------------------------------------------------- #

def levenshtein(a: str, b: str) -> int:
    """Exact edit distance. python-Levenshtein is absent; DP is fast enough here."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ast_signature(code: str) -> Optional[List[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    return [f"{type(n).__name__}:{getattr(n, 'name', getattr(n, 'id', ''))}"
            for n in ast.walk(tree)]


def ast_distance(a: str, b: str) -> Dict[str, Any]:
    """Structural distance that ignores formatting, comments and whitespace."""
    sa, sb = _ast_signature(a), _ast_signature(b)
    if sa is None or sb is None:
        return {"comparable": False, "ratio": None,
                "reason": "at least one revision does not parse"}
    ratio = difflib.SequenceMatcher(None, sa, sb).ratio()
    return {"comparable": True, "ratio": round(ratio, 4),
            "structural_distance": round(1.0 - ratio, 4),
            "identical_structure": sa == sb}


def oscillation_check(history: Sequence[str], *, epsilon: int = 5) -> Dict[str, Any]:
    """Halt condition: near-identical resubmission, or A-B-A alternation."""
    if len(history) < 2:
        return {"halt": False, "reason": "insufficient history", "last_distance": None}
    d = levenshtein(history[-1], history[-2])
    stalled = d < epsilon
    alternating = len(history) >= 3 and history[-1].strip() == history[-3].strip()
    reason = ("near-identical resubmission" if stalled else
              "A-B-A alternation" if alternating else "progressing")
    return {"halt": bool(stalled or alternating), "reason": reason, "last_distance": d,
            "ast": ast_distance(history[-1], history[-2])}


# --------------------------------------------------------------------------- #
# 7. conformal calibration
# --------------------------------------------------------------------------- #

def load_calibration(path: str) -> Dict[str, Any]:
    """Read nonconformity scores from a JSONL calibration file.

    Each line: {"score": <float>}  (optionally other fields, ignored).
    No file => no statistical guarantee. That is reported, never papered over.
    """
    if not os.path.exists(path):
        return {"available": False, "n": 0, "scores": [],
                "reason": f"no calibration file at {path}"}
    scores: List[float] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                scores.append(float(json.loads(line)["score"]))
            except Exception:  # noqa: BLE001
                continue
    return {"available": bool(scores), "n": len(scores), "scores": scores,
            "reason": "" if scores else "file present but no parseable scores"}


def conformal_threshold(cal_scores: Sequence[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Split-conformal quantile: q = ceil((n+1)(1-alpha))/n empirical quantile.

    With n < ceil(1/alpha) - 1 the bound is not attainable at all; that is
    reported as valid=False rather than emitted as a number that looks earned.
    """
    n = len(cal_scores)
    n_min = math.ceil(1.0 / alpha) - 1
    if n == 0:
        return {"valid": False, "n": 0, "alpha": alpha, "quantile": None,
                "guarantee": "none — uncalibrated",
                "reason": "no calibration data; conformal guarantee unavailable"}
    rank = math.ceil((n + 1) * (1.0 - alpha))
    if rank > n:
        return {"valid": False, "n": n, "alpha": alpha, "quantile": None,
                "guarantee": "none — insufficient calibration",
                "reason": f"need n >= {n_min} for alpha={alpha}, have {n}"}
    q = sorted(cal_scores)[rank - 1]
    return {"valid": True, "n": n, "alpha": alpha, "quantile": float(q), "rank": rank,
            "guarantee": f"marginal error rate <= {alpha} under exchangeability",
            "reason": ""}


def conformal_decide(score: float, thr: Dict[str, Any]) -> Dict[str, Any]:
    if not thr.get("valid"):
        return {"passed_conformal_check": False, "abstain": True,
                "reason": thr.get("reason", "uncalibrated"),
                "calibration_source": "uncalibrated"}
    inside = score <= thr["quantile"]
    return {"passed_conformal_check": bool(inside), "abstain": not inside,
            "nonconformity_score": float(score), "threshold": thr["quantile"],
            "reason": "within bound" if inside else "score exceeds conformal quantile",
            "calibration_source": f"n={thr['n']}, alpha={thr['alpha']}"}


# --------------------------------------------------------------------------- #
# 8. report schema + validation
# --------------------------------------------------------------------------- #

REPORT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "VerificationReport",
    "type": "object",
    "required": ["report_id", "task_id", "verdict", "confidence_score", "conformal_bound",
                 "claim_evaluations", "repair_directives", "self_audit"],
    "properties": {
        "report_id": {"type": "string"},
        "task_id": {"type": "string"},
        "iteration_index": {"type": "integer", "minimum": 0},
        "verdict": {"type": "string",
                    "enum": ["PASSED", "FAILED", "NEEDS_REPAIR", "ESCALATED"]},
        "confidence_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "conformal_bound": {
            "type": "object",
            "required": ["alpha_target", "passed_conformal_check", "calibration_source"],
            "properties": {
                "alpha_target": {"type": "number"},
                "empirical_quantile": {"type": ["number", "null"]},
                "passed_conformal_check": {"type": "boolean"},
                "calibration_source": {"type": "string"},
            },
        },
        "claim_evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim_id", "status", "criticality", "verification_method",
                             "grounding_tier", "evidence_receipt"],
                "properties": {
                    "claim_id": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["VERIFIED", "REFUTED", "UNVERIFIABLE"]},
                    "criticality": {"type": "string",
                                    "enum": ["blocking", "major", "minor", "informational"]},
                    "verification_method": {
                        "type": "string",
                        "enum": ["code_execution", "functional_clustering", "symbolic_prover",
                                 "static_parse", "schema_validation", "web_grounding",
                                 "internal_doc_grounding", "self_consistency", "heuristic",
                                 "blocked_by_premise"],
                    },
                    "grounding_tier": {"type": "integer", "minimum": 1, "maximum": 5},
                    "evidence_citation": {"type": "string"},
                    "evidence_receipt": {"type": ["object", "null"]},
                },
            },
        },
        "repair_directives": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["target_path", "failure_type", "localized_diff",
                             "actionable_instruction", "freeze_bounds"],
                "properties": {
                    "target_path": {"type": "string"},
                    "failure_type": {"type": "string"},
                    "localized_diff": {"type": "string"},
                    "actionable_instruction": {"type": "string"},
                    "freeze_bounds": {"type": "array", "items": {"type": "string"}},
                    "evidence_receipt": {"type": ["object", "null"]},
                },
            },
        },
        "self_audit": {
            "type": "object",
            "required": ["tools_invoked", "claims_without_receipt", "capability_gaps"],
            "properties": {
                "tools_invoked": {"type": "integer", "minimum": 0},
                "claims_without_receipt": {"type": "integer", "minimum": 0},
                "capability_gaps": {"type": "array", "items": {"type": "string"}},
                "blinded_pass_verdict": {"type": "string"},
                "post_trace_verdict": {"type": "string"},
                "injection_attempts_detected": {"type": "integer", "minimum": 0},
            },
        },
    },
}

_TIER = {"code_execution": 1, "functional_clustering": 1, "symbolic_prover": 1,
         "static_parse": 2, "schema_validation": 2, "internal_doc_grounding": 3,
         "web_grounding": 3, "self_consistency": 4, "heuristic": 4,
         "blocked_by_premise": 5}


def validate_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Schema check plus the invariants the schema cannot express.

    This is the last gate before emission: it mechanically enforces
    'no PASSED without receipts' and 'no blocking claim VERIFIED on a heuristic'.
    """
    res = check_schema(report, REPORT_SCHEMA)
    violations = [f"schema: {v['json_pointer']} {v['message']}" for v in res["violations"]]

    evals = report.get("claim_evaluations", []) or []
    no_receipt = [c["claim_id"] for c in evals
                  if c.get("status") == "VERIFIED" and not c.get("evidence_receipt")]
    if no_receipt:
        violations.append(f"invariant#2: VERIFIED without receipt -> {no_receipt}")

    laundered = [c["claim_id"] for c in evals
                 if c.get("status") == "VERIFIED"
                 and c.get("criticality") == "blocking"
                 and _TIER.get(c.get("verification_method", "heuristic"), 4) >= 4]
    if laundered:
        violations.append(f"invariant#4: blocking claim VERIFIED on tier>=4 -> {laundered}")

    tier_mismatch = [c["claim_id"] for c in evals
                     if c.get("grounding_tier") != _TIER.get(c.get("verification_method"))]
    if tier_mismatch:
        violations.append(f"grounding_tier does not match method -> {tier_mismatch}")

    if report.get("verdict") == "PASSED":
        bad = [c["claim_id"] for c in evals
               if c.get("criticality") != "informational" and c.get("status") != "VERIFIED"]
        if bad:
            violations.append(f"invariant#2: PASSED with non-verified claims -> {bad}")
        if not report.get("conformal_bound", {}).get("passed_conformal_check", False):
            violations.append("PASSED while conformal check did not pass")

    if report.get("self_audit", {}).get("claims_without_receipt", 0) > 0 \
            and report.get("verdict") == "PASSED":
        violations.append("self_audit reports missing receipts but verdict is PASSED")

    return {"valid": not violations, "violations": violations,
            "forced_verdict": None if not violations else "ESCALATED"}


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #

def _selftest() -> int:  # noqa: C901
    checks: List[Tuple[str, bool, str]] = []

    def chk(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    r = run_sandboxed("print(6*7)")
    chk("exec ok", r["success"] and "42" in r["stdout"], r["stdout"].strip())

    r = run_sandboxed("def f(v): return v[0]/0\nprint(f([1]))")
    chk("exec catches ZeroDivisionError",
        not r["success"] and "ZeroDivisionError" in r["stderr"], str(r["exit_code"]))

    r = run_sandboxed("import time\nwhile True: time.sleep(0.01)", timeout_s=2)
    chk("timeout enforced", r["timed_out"] and not r["success"], f"{r['wall_ms']}ms")

    r = run_sandboxed("x = bytearray(2_000_000_000)", mem_mb=128, timeout_s=15)
    chk("memory rlimit enforced", not r["success"], (r["stderr"] or "")[-60:].strip())

    r = syntax_check("def broken(:\n  pass")
    chk("syntax gate", not r["success"] and "SyntaxError" in (r["error"] or ""), r.get("error", ""))

    fc = functional_clustering(
        ["def f(a, b): return a + b", "def f(a, b): return b + a", "def f(a, b): return a - b"],
        "f", [(1, 2), (5, 5), (-3, 7)])
    chk("functional clustering splits behaviour",
        len(fc["clusters"]) == 2 and fc["dominant_mass"] > 0.6,
        f"{len(fc['clusters'])} clusters, mass={fc['dominant_mass']}")

    p = prove("Implies(And(A, B), A)")
    chk("prover: tautology UNSAT-negation", p["status"] == "UNSAT" and p["claim_holds"], p["detail"])

    p = prove("And(A, Not(A))")
    chk("prover: contradiction SAT", p["status"] == "SAT" and not p["claim_holds"], p["detail"])

    p = prove("x**2 >= 0")
    chk("prover: real-domain tautology", p["status"] in ("UNSAT", "UNKNOWN"),
        f"{p['status']} / {p['detail']}")

    p = prove("x >= 0")
    chk("prover: finds counterexample or admits UNKNOWN",
        p["status"] in ("SAT", "UNKNOWN") and not p["claim_holds"], p["status"])

    dag = build_dag([{"claim_id": "c1", "dependencies": []},
                     {"claim_id": "c2", "dependencies": ["c1"]},
                     {"claim_id": "c3", "dependencies": ["c2"]}])
    chk("dag valid + ordered", dag["valid"] and dag["topological_order"] == ["c1", "c2", "c3"],
        str(dag["topological_order"]))
    chk("dag blocks descendants of refuted premise",
        blocked_descendants(dag, ["c1"]) == ["c2", "c3"], str(blocked_descendants(dag, ["c1"])))

    bad = build_dag([{"claim_id": "a", "dependencies": ["b"]},
                     {"claim_id": "b", "dependencies": ["a"]}])
    chk("dag rejects cycle", not bad["valid"] and not bad["acyclic"], str(bad["cycles"]))

    dang = build_dag([{"claim_id": "a", "dependencies": ["ghost"]}])
    chk("dag rejects dangling dep", not dang["valid"] and dang["dangling_dependencies"],
        str(dang["dangling_dependencies"]))

    sv = check_schema({"name": 1}, {"$schema": "https://json-schema.org/draft/2020-12/schema",
                                    "title": "T", "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"]})
    chk("schema violation caught", not sv["success"] and sv["violations"],
        sv["violations"][0]["message"] if sv["violations"] else "")

    chk("levenshtein exact", levenshtein("kitten", "sitting") == 3,
        str(levenshtein("kitten", "sitting")))
    chk("ast distance ignores formatting",
        ast_distance("def f(x):\n    return x+1", "def f(x):\n\n    # note\n    return x + 1"
                     )["identical_structure"], "")
    osc = oscillation_check(["def f(): return 1", "def f(): return 1"])
    chk("oscillation halt on resubmission", osc["halt"], osc["reason"])
    osc = oscillation_check(["A" * 40, "B" * 40, "A" * 40])
    chk("oscillation halt on A-B-A", osc["halt"], osc["reason"])

    thr = conformal_threshold([], 0.05)
    chk("conformal: refuses without calibration", not thr["valid"], thr["reason"])
    thr = conformal_threshold([0.1] * 5, 0.05)
    chk("conformal: refuses when n too small", not thr["valid"], thr["reason"])
    scores = [i / 100.0 for i in range(100)]
    thr = conformal_threshold(scores, 0.05)
    chk("conformal: valid quantile at n=100", thr["valid"] and abs(thr["quantile"] - 0.95) < 1e-9,
        str(thr["quantile"]))
    chk("conformal decide inside", conformal_decide(0.5, thr)["passed_conformal_check"], "")
    chk("conformal decide abstains outside", conformal_decide(0.99, thr)["abstain"], "")

    good_report = {
        "report_id": "r1", "task_id": "t1", "iteration_index": 0, "verdict": "PASSED",
        "confidence_score": 0.9,
        "conformal_bound": {"alpha_target": 0.05, "empirical_quantile": 0.95,
                            "passed_conformal_check": True, "calibration_source": "n=100"},
        "claim_evaluations": [{"claim_id": "c1", "status": "VERIFIED", "criticality": "blocking",
                               "verification_method": "code_execution", "grounding_tier": 1,
                               "evidence_receipt": receipt("code_execution", "print(1)")}],
        "repair_directives": [],
        "self_audit": {"tools_invoked": 1, "claims_without_receipt": 0, "capability_gaps": []},
    }
    chk("validate_report accepts a grounded PASS", validate_report(good_report)["valid"],
        str(validate_report(good_report)["violations"]))

    lazy = json.loads(json.dumps(good_report))
    lazy["claim_evaluations"][0]["evidence_receipt"] = None
    v = validate_report(lazy)
    chk("validate_report blocks PASS without receipt", not v["valid"], str(v["violations"])[:90])

    laundered = json.loads(json.dumps(good_report))
    laundered["claim_evaluations"][0]["verification_method"] = "heuristic"
    laundered["claim_evaluations"][0]["grounding_tier"] = 4
    v = validate_report(laundered)
    chk("validate_report blocks heuristic-grounded blocking claim", not v["valid"],
        str(v["violations"])[:90])

    uncal = json.loads(json.dumps(good_report))
    uncal["conformal_bound"] = {"alpha_target": 0.05, "empirical_quantile": None,
                                "passed_conformal_check": False,
                                "calibration_source": "uncalibrated"}
    chk("validate_report blocks PASS while uncalibrated", not validate_report(uncal)["valid"], "")

    width = max(len(n) for n, _, _ in checks)
    passed = 0
    for name, cond, detail in checks:
        passed += cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _selftest())
