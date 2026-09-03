#!/usr/bin/env python3
"""agent_eval.py - score SiemensGPT agent-library entries on craft x adoption.

INPUT
    A JSON file containing any of:
      (a) the raw `botConfigs` array from one SearchAgentLibrary result,
      (b) a full result object with a top-level "botConfigs" key,
      (c) a list of several such objects (several searches concatenated).
    Duplicate agents (same id) are collapsed automatically.

USAGE
    python agent_eval.py agents.json
    python agent_eval.py --selftest

FORMULA (printed with every run, so a reader can disagree with it)
    final    = 0.6*craft + 0.4*adoption                       -> 0..10
    craft    = ground + scope + struct + tools + depth, each 0..2
    adoption = 6*log10(uses+1)/log10(USES_CEIL)
             + 4*min(favs, FAV_CEIL)/FAV_CEIL

WHAT THIS DOES NOT DO
    Craft sub-scores are HEURISTIC. They count DISTINCT marker phrases in the
    system prompt (English, French, Portuguese) and approximate prompt
    discipline. They do not measure behaviour - no agent is executed. Treat the
    output as a reading aid, not a verdict.

OVERRIDES
    Any record may carry a "_craft" key to replace heuristic values by hand:
        {"id": "...", "_craft": {"ground": 2, "depth": 1}}
"""

import json
import math
import re
import sys

USES_CEIL = 600.0
FAV_CEIL = 40.0

# Distinct-pattern matching: each pattern contributes at most 1, however often
# it occurs. Counting occurrences would just reward verbosity.
PATTERNS = {
    "ground": [
        r"never invent", r"do not invent", r"don't invent", r"never fabricate",
        r"fabricat", r"\[INTERNAL\]", r"\[PUBLIC\]", r"\[RETRIEVED\]",
        r"\[TO BE CONFIRMED\]", r"\[HYPOTHESIS\]", r"traceable",
        r"ne jamais (supposer|inventer|deviner)", r"inventer du",
        r"nunca inventar", r"n[aã]o inventar", r"jamais de pseudo-code",
        r"not found in the available sources", r"honn[eê]tet[eé]",
    ],
    "scope": [
        r"out of scope", r"##\s*SCOPE", r"use this agent when",
        r"this belongs to", r"hors de", r"fora de escopo", r"escopo",
        r"ne pas faire", r"limitations", r"tes missions", r"restrictions",
    ],
    "struct": [
        r"answer structure", r"output format", r"skeleton", r"gap table",
        r"response formats?", r"detailed card", r"padrao de entrega",
        r"padr[aã]o de entrega", r"estrutura", r"formatacao obrigatoria",
        r"unified template", r"\bD1\b.*\bD2\b",
    ],
    "tools": [
        r"tool policy", r"level 0", r"level 1", r"level 2", r"level 3",
        r"code interpreter", r"file store", r"ferramentas", r"\bDQE\b",
        r"ExecutePythonCode", r"SiemensMcp", r"semantic search",
    ],
}

THRESHOLDS = ((4, 2), (2, 1))  # distinct hits -> score; else 0


def _score_axis(text, axis):
    hits = [p for p in PATTERNS[axis] if re.search(p, text, re.I | re.S)]
    n = len(hits)
    for need, val in THRESHOLDS:
        if n >= need:
            return val, n
    return 0, n


def _score_depth(rag, prompt_len):
    """Depth = knowledge backing + prompt substance, capped at 2."""
    d = 2 if rag >= 5 else (1 if rag >= 1 else 0)
    if prompt_len > 8000:
        d += 1
    return min(d, 2)


def adoption(uses, favs):
    return (math.log10(uses + 1) / math.log10(USES_CEIL)) * 6 \
         + (min(favs, FAV_CEIL) / FAV_CEIL) * 4


def evaluate(cfg):
    prompt = cfg.get("systemPrompt") or ""
    rag = cfg.get("ragDocumentCount") or 0
    uses = cfg.get("numberOfUses") or 0
    favs = cfg.get("numberOfFavorites") or 0

    craft_parts, detail = {}, {}
    for axis in ("ground", "scope", "struct", "tools"):
        craft_parts[axis], detail[axis] = _score_axis(prompt, axis)
    craft_parts["depth"] = _score_depth(rag, len(prompt))
    detail["depth"] = rag

    for k, v in (cfg.get("_craft") or {}).items():
        craft_parts[k] = v

    craft = sum(craft_parts.values())
    adopt = adoption(uses, favs)
    return {
        "name": (cfg.get("name") or "?")[:38],
        "owner": cfg.get("ownerId") or "?",
        "rag": rag, "uses": uses, "favs": favs,
        "upvotes": cfg.get("numberOfUpVotes") or 0,
        "verified": bool(cfg.get("isVerified")),
        "craft": craft, "adoption": adopt,
        "final": 0.6 * craft + 0.4 * adopt,
        "parts": craft_parts, "detail": detail,
    }


def harvest(blob):
    """Accept any of the three documented input shapes; dedupe by id."""
    out, seen = [], set()

    def take(cfg):
        if not isinstance(cfg, dict):
            return
        key = cfg.get("id") or cfg.get("name")
        if key and key not in seen:
            seen.add(key)
            out.append(cfg)

    def walk(node):
        if isinstance(node, dict):
            if "botConfigs" in node:
                for c in node["botConfigs"] or []:
                    take(c)
            elif "systemPrompt" in node or "numberOfUses" in node:
                take(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blob)
    return out


def report(rows, verbose=False):
    rows.sort(key=lambda r: r["final"], reverse=True)
    w = 38
    print(f"{'AGENT':{w}}{'FINAL':>7}{'CRAFT':>7}{'ADOPT':>7}{'RAG':>5}"
          f"{'USES':>6}{'FAV':>5}")
    print("-" * (w + 37))
    for r in rows:
        print(f"{r['name']:{w}}{r['final']:7.2f}{r['craft']:7.1f}"
              f"{r['adoption']:7.2f}{r['rag']:5}{r['uses']:6}{r['favs']:5}")
    if verbose:
        print()
        for r in rows:
            parts = " ".join(f"{k}={v}" for k, v in r["parts"].items())
            print(f"  {r['name']:{w}} {parts}")
    print()
    print("FORMULA  final = 0.6*craft + 0.4*adoption")
    print("  craft    = ground + scope + struct + tools + depth, each 0-2")
    print(f"  adoption = 6*log10(uses+1)/log10({USES_CEIL:.0f})"
          f" + 4*min(fav,{FAV_CEIL:.0f})/{FAV_CEIL:.0f}")
    print("  craft is heuristic (marker-phrase counting), not behavioural.")
    print()
    print(f"agents scored: {len(rows)}"
          f" | owners: {len(set(r['owner'] for r in rows))}"
          f" | with RAG: {sum(1 for r in rows if r['rag'] > 0)}"
          f" | zero upvotes: {sum(1 for r in rows if r['upvotes'] == 0)}"
          f" | verified: {sum(1 for r in rows if r['verified'])}")


def selftest():
    ok = fail = 0

    def check(label, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"[PASS] {label}")
        else:
            fail += 1
            print(f"[FAIL] {label}")

    rich = {
        "id": "a", "name": "Rich", "ownerId": "O1", "numberOfUses": 500,
        "numberOfFavorites": 40, "ragDocumentCount": 10,
        "systemPrompt": (
            "## SCOPE\nOut of scope: other things. Use this agent when needed.\n"
            "TOOL POLICY Level 0 Level 1 Level 2 with Code Interpreter and DQE.\n"
            "ANSWER STRUCTURE with a gap table and an output format.\n"
            "Never invent document numbers. Tag [INTERNAL] and [RETRIEVED]; "
            "everything must be traceable. Never fabricate."),
    }
    bare = {"id": "b", "name": "Bare", "ownerId": "O2", "numberOfUses": 0,
            "numberOfFavorites": 0, "ragDocumentCount": 0, "systemPrompt": "hi"}

    r_rich, r_bare = evaluate(rich), evaluate(bare)

    check("rich prompt outscores bare prompt", r_rich["final"] > r_bare["final"])
    check("bare prompt scores craft 0", r_bare["craft"] == 0)
    check("bare prompt scores adoption 0", abs(r_bare["adoption"]) < 1e-9)
    check("craft is bounded at 10", r_rich["craft"] <= 10)
    check("final is bounded at 10", r_rich["final"] <= 10.0001)
    check("rich hits max ground", r_rich["parts"]["ground"] == 2)
    check("rich hits max tools", r_rich["parts"]["tools"] == 2)
    check("depth 10 rag -> 2", _score_depth(10, 0) == 2)
    check("depth 1 rag -> 1", _score_depth(1, 0) == 1)
    check("depth 0 rag short -> 0", _score_depth(0, 100) == 0)
    check("depth capped at 2 by long prompt", _score_depth(10, 90000) == 2)
    check("adoption monotonic in uses", adoption(100, 0) > adoption(10, 0))
    check("adoption monotonic in favs", adoption(0, 20) > adoption(0, 2))
    check("favs saturate at ceiling",
          abs(adoption(0, 100) - adoption(0, FAV_CEIL)) < 1e-9)

    override = dict(bare, _craft={"ground": 2, "depth": 2})
    check("manual override applies", evaluate(override)["craft"] == 4)

    shapes = [
        ("raw array", [rich, bare]),
        ("result object", {"botConfigs": [rich, bare]}),
        ("list of results", [{"botConfigs": [rich]}, {"botConfigs": [bare]}]),
    ]
    for label, blob in shapes:
        check(f"harvest handles {label}", len(harvest(blob)) == 2)
    check("harvest dedupes by id",
          len(harvest([{"botConfigs": [rich]}, {"botConfigs": [rich]}])) == 1)
    check("harvest tolerates empty", harvest({"botConfigs": []}) == [])
    check("harvest tolerates junk", harvest({"nothing": 1}) == [])

    print()
    print(f"selftest: {ok} passed, {fail} failed")
    if not fail:
        print()
        print("--- demo report on the two synthetic agents ---")
        report([r_rich, r_bare], verbose=True)
    return 0 if not fail else 1


def main(argv):
    # The sandbox pre-populates sys.argv with Lambda bootstrap junk, so only
    # trust arguments that look like something we were actually given.
    args = [a for a in argv[1:]
            if a == "--selftest" or a == "-v" or a.endswith(".json")]
    if not args or "--selftest" in args:
        return selftest()
    path = next(a for a in args if a.endswith(".json"))
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    cfgs = harvest(blob)
    if not cfgs:
        print(f"no agent records found in {path}")
        return 1
    report([evaluate(c) for c in cfgs], verbose="-v" in args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
