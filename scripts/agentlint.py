#!/usr/bin/env python3
"""
agentlint.py - Agent Library audit: craft scoring, hygiene lint, duplicate
detection and lifecycle triage for a SiemensGPT agent-library export.

INPUT
  Any of:
    * a JSON file that is a list of botConfig objects
    * a JSON file shaped {"botConfigs": [...]}  (exactly what SearchAgentLibrary returns)
    * an NDJSON file (one botConfig per line)
    * a directory containing any mix of the above
  Only these fields are required per agent; everything else is optional:
    id, name, description, systemPrompt, numberOfUses, numberOfUpVotes,
    numberOfFavorites, numberOfPrompts, isVerified, ownerId, updatedAt, createdAt

OUTPUT (written to --out-dir, default /tmp)
    agents_scored.csv   one row per agent, every sub-score and every flag
    duplicates.csv      near-duplicate pairs with similarity and a merge hint
    leaderboard.html    self-contained, sortable, no external assets
    stdout summary

USAGE
    python agentlint.py --input /tmp/export.json --out-dir /tmp
    python agentlint.py --selftest

DESIGN NOTES
  * stdlib + optional pandas only. No network. Safe in the sandbox.
  * argv is parsed with parse_known_args() on purpose: the sandbox harness
    pre-populates sys.argv with unrelated flags and a strict parser dies on them.
  * Every craft criterion records the *evidence string* that triggered it, so a
    human can audit or overrule the machine. Automated detection is a heuristic
    screen, not a verdict.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

VERSION = "1.0"

# --------------------------------------------------------------------------
# Craft rubric. Each criterion: (key, label, [regex patterns], why it matters)
# A hit on ANY pattern scores the point and records the matched text.
# --------------------------------------------------------------------------
CRAFT = [
    ("A", "Scope stated", [
        r"^\s*#*\s*##?\s*SCOPE",
        r"\byou are (?:the |a |an )?[A-Z]",
        r"\b(?:your (?:role|job|purpose|scope) is)\b",
    ], "Reader can tell in one line what this agent is for."),

    ("B", "Boundary + named alternative", [
        r"\bout of scope\b",
        r"\buse\s+[A-Z][\w \-]{2,40}\s+instead\b",
        r"\bif .{0,60}\buse\s+(?:the\s+)?[A-Z][\w \-]{2,40}",
        r"\b(?:delegate|route|redirect)\s+(?:to|the user)\b",
        r"\bdoes not (?:cover|handle|do)\b",
    ], "Discovery works when each agent names its neighbours."),

    ("C", "Anti-fabrication rule", [
        r"\bnever (?:invent|fabricate|guess|assume|make up)\b",
        r"\b(?:do not|don't|dont)\s+(?:invent|fabricate|guess|make up)\b",
        r"\bno fabrication\b",
        r"\bnothing is invented\b",
        r"\bhallucinat",
        r"\bnever (?:state|present) (?:an? )?unverified\b",
    ], "Explicit ban on inventing facts, IDs, clauses or numbers."),

    ("D", "Source / evidence labelling", [
        r"\[(?:RETRIEVED|INTERNAL|PUBLIC|HYPOTHESIS|TO BE CONFIRMED|TO BE COMPLETED)\]",
        r"\bcite the (?:source|module|document)\b",
        r"\btraceable reference",
        r"\bwith (?:a )?(?:source|document) (?:name|id|reference)\b",
        r"\bconfidence(?: level| score)\b",
    ], "Reader can tell evidence from inference."),

    ("E", "Output structure specified", [
        r"^\s*#*\s*##?\s*(?:ANSWER STRUCTURE|OUTPUT|RESPONSE FORMAT|OUTPUT FORMAT)",
        r"\banswer structure\b",
        r"\boutput (?:format|skeleton|template)\b",
        r"\bresponse format",
        r"\buse this (?:format|structure|skeleton)\b",
    ], "Predictable shape beats a wall of prose."),

    ("F", "Tool-effort policy", [
        r"\blevel\s*0\b.{0,40}\bno tools?\b",
        r"\btool policy\b",
        r"\bmatch effort to the\b",
        r"\bat most \d+ (?:rounds?|calls?|searches)\b",
        r"\bmaximum \d+ .{0,20}(?:rounds?|calls?)\b",
        r"\bnever call .{0,30}just to be safe\b",
    ], "Stops needless tool calls; the single biggest latency/cost lever."),

    ("G", "Validation gate before delivery", [
        r"\b(?:quality|pre-?flight|validation) checklist\b",
        r"\bbefore (?:responding|delivery|delivering|output)\b.{0,80}\b(?:confirm|verify|check)\b",
        r"\bmandatory .{0,30}(?:validation|self-?check|gap table)\b",
        r"\bself-?check\b",
        r"\bwait for .{0,40}(?:confirmation|approval)\b",
        r"\bre-?run the complete validation\b",
        r"\bgap table\b",
    ], "The agent checks its own work before handing it over."),
]

BANNED_NAME_TOKENS = [
    "agent", "bot", "llm", " ai ", "gpt", "assistant", "pro", "ultimate",
    "smart", "copilot", "expert",
]
# tokens that are acceptable as a trailing functional noun but not as decoration
SOFT_NAME_TOKENS = {"assistant", "expert", "copilot"}

MODEL_NAME_RE = re.compile(
    r"\b(claude|opus|sonnet|haiku|gpt-?[45o]|o[13]\b|gemini|llama|mistral)\b", re.I)
VERSION_IN_NAME_RE = re.compile(r"\bv?\d+\.\d+\b|\bv\d+\b", re.I)
HTML_RE = re.compile(r"</?(?:p|div|span|strong|em|h[1-6]|ul|ol|li|br|a|table)\b", re.I)
ENTITY_RE = re.compile(r"&(?:amp|nbsp|lt|gt|quot|#\d+);")
CRED_RE = re.compile(
    r"\b(role|warehouse|password|token|api[_ ]?key|credential)s?\s*[:=]\s*\S", re.I)

STALE_FRESH_DAYS = 90
STALE_AGING_DAYS = 180
STALE_STALE_DAYS = 365
DUP_THRESHOLD = 0.55


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _coerce(payload):
    """Return a list of agent dicts from whatever shape the JSON came in."""
    if isinstance(payload, dict):
        for key in ("botConfigs", "agents", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        # a single agent object
        if "systemPrompt" in payload or "name" in payload:
            return [payload]
        return []
    if isinstance(payload, list):
        return payload
    return []


def load_agents(path):
    """Load agents from a file or a directory of files. Deduplicates on id."""
    files = []
    if os.path.isdir(path):
        for root, _dirs, names in os.walk(path):
            for n in sorted(names):
                if n.lower().endswith((".json", ".ndjson", ".jsonl")):
                    files.append(os.path.join(root, n))
    else:
        files = [path]

    agents, seen, problems = [], set(), []
    for fp in files:
        try:
            raw = open(fp, "r", encoding="utf-8").read().strip()
        except OSError as exc:
            problems.append(f"{fp}: {exc}")
            continue
        if not raw:
            continue
        batch = []
        try:
            batch = _coerce(json.loads(raw))
        except json.JSONDecodeError:
            # try NDJSON
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    batch.extend(_coerce(json.loads(line)))
                except json.JSONDecodeError:
                    problems.append(f"{fp}: unparseable line")
        for a in batch:
            if not isinstance(a, dict):
                continue
            key = a.get("id") or (a.get("name"), len(a.get("systemPrompt") or ""))
            if key in seen:
                continue
            seen.add(key)
            agents.append(a)
    return agents, problems


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _age_days(stamp, now=None):
    if not stamp:
        return None
    now = now or datetime.now(timezone.utc)
    txt = str(stamp).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.fromisoformat(txt[:26] + "+00:00")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def freshness(days):
    if days is None:
        return "Unknown"
    if days <= STALE_FRESH_DAYS:
        return "Fresh"
    if days <= STALE_AGING_DAYS:
        return "Aging"
    if days <= STALE_STALE_DAYS:
        return "Stale"
    return "Abandoned"


def score_craft(prompt, description):
    """Return (bits dict, evidence dict). Searches prompt first, then description."""
    haystacks = [("prompt", prompt or ""), ("description", description or "")]
    bits, evidence = {}, {}
    for key, _label, patterns, _why in CRAFT:
        bits[key], evidence[key] = 0, ""
        for where, text in haystacks:
            for pat in patterns:
                m = re.search(pat, text, re.I | re.M)
                if m:
                    snippet = m.group(0).strip().replace("\n", " ")
                    bits[key] = 1
                    evidence[key] = f"{where}: {snippet[:80]}"
                    break
            if bits[key]:
                break
    return bits, evidence


def lint_hygiene(agent):
    """Description and naming hygiene. Returns list of flag strings."""
    flags = []
    name = (agent.get("name") or "").strip()
    desc = agent.get("description") or ""
    prompt = agent.get("systemPrompt") or ""

    if HTML_RE.search(desc) or ENTITY_RE.search(desc):
        flags.append("html-in-description")
    if len(re.sub(r"<[^>]+>", "", desc).strip()) < 80:
        flags.append("description-too-short")
    if len(desc) > 2000:
        flags.append("description-too-long")
    if CRED_RE.search(desc):
        flags.append("credentials-in-description")
    if MODEL_NAME_RE.search(desc):
        flags.append("model-name-in-description")
    if re.search(r"\bcopy the below prompt\b|\bcopy this prompt\b|^\s*\"I am analyzing",
                 desc, re.I | re.M):
        flags.append("description-used-as-manual")
    if re.search(r"\b(?:feedback|suggestions?|questions?)\s+to\s+[A-Z][a-z]+ [A-Z][a-z]+", desc):
        flags.append("person-name-in-description")

    low = f" {name.lower()} "
    decorative = [t for t in BANNED_NAME_TOKENS
                  if t.strip() in low.split() or t in low]
    # a soft token is fine as the LAST word (functional noun), noise elsewhere
    tail = name.lower().split()[-1] if name.split() else ""
    decorative = [t for t in decorative
                  if not (t.strip() in SOFT_NAME_TOKENS and t.strip() == tail)]
    if decorative:
        flags.append("noise-tokens-in-name:" + "/".join(sorted(set(t.strip() for t in decorative))))
    if VERSION_IN_NAME_RE.search(name):
        flags.append("version-in-name")
    if len(name) > 55:
        flags.append("name-too-long")
    if not prompt.strip():
        flags.append("empty-system-prompt")
    return flags


def tokenset(text, cap=4000):
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower())[:cap])


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def triage(rec, median_uses):
    """Rule-based lifecycle verdict. Rules are stated, not learned."""
    craft, uses, fresh = rec["craft_score"], rec["uses"], rec["freshness"]
    reasons = []
    verdict = "KEEP"

    if rec["dup_of"]:
        verdict, reasons = "RETIRE", [f"near-duplicate of {rec['dup_of']}"]
    elif uses == 0 and fresh in ("Stale", "Abandoned"):
        verdict, reasons = "RETIRE", ["zero use and untouched >180d"]
    elif craft <= 3 and fresh == "Abandoned":
        verdict, reasons = "RETIRE", ["craft<=3 and untouched >365d"]
    elif craft >= 6 and uses > 0 and fresh in ("Fresh", "Aging"):
        verdict, reasons = "ENDORSE", [f"craft {craft}/8", "active", fresh.lower()]
    elif craft <= 4 and uses >= max(median_uses, 1):
        verdict, reasons = "FIX", [f"craft {craft}/8 on a high-traffic agent"]
    elif rec["hygiene_flags"]:
        verdict, reasons = "FIX", ["hygiene: " + rec["hygiene_flags"].split(";")[0]]
    else:
        reasons = [f"craft {craft}/8", fresh.lower()]
    return verdict, "; ".join(reasons)


def audit(agents, now=None, dup_threshold=DUP_THRESHOLD):
    recs = []
    for a in agents:
        prompt = a.get("systemPrompt") or ""
        desc = a.get("description") or ""
        bits, evid = score_craft(prompt, desc)
        flags = lint_hygiene(a)
        # H (clean description) is derived from the hygiene lint, not a regex
        desc_dirty = any(f.startswith(("html-in-description", "description-too-short",
                                       "description-used-as-manual",
                                       "credentials-in-description",
                                       "model-name-in-description"))
                         for f in flags)
        bits["H"] = 0 if desc_dirty else 1
        evid["H"] = "derived from hygiene lint"

        days = _age_days(a.get("updatedAt") or a.get("createdAt"), now)
        uses = int(a.get("numberOfUses") or 0)
        favs = int(a.get("numberOfFavorites") or 0)
        recs.append({
            "id": a.get("id", ""),
            "name": a.get("name", "") or "(unnamed)",
            "owner": a.get("ownerId", ""),
            "uses": uses,
            "upvotes": int(a.get("numberOfUpVotes") or 0),
            "favourites": favs,
            "starter_prompts": int(a.get("numberOfPrompts") or 0),
            "verified": bool(a.get("isVerified")),
            "rag_docs": int(a.get("ragDocumentCount") or 0),
            "days_since_update": days if days is not None else "",
            "freshness": freshness(days),
            "prompt_chars": len(prompt),
            "craft_score": sum(bits.values()),
            **{f"craft_{k}": bits[k] for k, _l, _p, _w in CRAFT},
            "craft_H": bits["H"],
            "stickiness_per100": round(100 * favs / uses, 1) if uses else 0.0,
            "hygiene_flags": ";".join(flags),
            "hygiene_count": len(flags),
            "evidence": " | ".join(f"{k}={v}" for k, v in evid.items() if v),
            "dup_of": "",
            "dup_similarity": 0.0,
            "_tokens": tokenset(f"{a.get('name','')} {desc} {prompt[:3000]}"),
        })

    # near-duplicate detection: keep the stronger of each pair
    pairs = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            sim = jaccard(recs[i]["_tokens"], recs[j]["_tokens"])
            if sim >= dup_threshold:
                a_rec, b_rec = recs[i], recs[j]
                # weaker = lower craft, tie-broken by lower usage
                weak, strong = sorted(
                    (a_rec, b_rec), key=lambda r: (r["craft_score"], r["uses"]))
                pairs.append({
                    "keep": strong["name"], "retire": weak["name"],
                    "similarity": round(sim, 3),
                    "keep_uses": strong["uses"], "retire_uses": weak["uses"],
                    "same_owner": strong["owner"] == weak["owner"],
                })
                if not weak["dup_of"] or sim > weak["dup_similarity"]:
                    weak["dup_of"] = strong["name"]
                    weak["dup_similarity"] = round(sim, 3)

    uses_sorted = sorted(r["uses"] for r in recs)
    median_uses = uses_sorted[len(uses_sorted) // 2] if uses_sorted else 0
    for r in recs:
        r["verdict"], r["verdict_reason"] = triage(r, median_uses)
        r.pop("_tokens", None)
    return recs, pairs, median_uses


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def write_csv(path, rows, fieldnames=None):
    if not rows:
        open(path, "w", encoding="utf-8").write("")
        return
    fieldnames = fieldnames or list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


VERDICT_COLOUR = {"ENDORSE": "#137333", "KEEP": "#5f6368",
                  "FIX": "#b06000", "RETIRE": "#b3261e"}


def write_html(path, recs, pairs, stats):
    def td(x):
        return f"<td>{html.escape(str(x))}</td>"

    body = []
    for r in sorted(recs, key=lambda r: (-r["craft_score"], -r["uses"])):
        colour = VERDICT_COLOUR.get(r["verdict"], "#5f6368")
        bar = "".join(
            f'<i class="{"on" if r["craft_"+k] else "off"}" title="{html.escape(lbl)}">{k}</i>'
            for k, lbl, _p, _w in CRAFT + [("H", "Clean description", [], "")])
        body.append(
            "<tr>"
            + td(r["name"])
            + f'<td class="num"><b>{r["craft_score"]}</b>/8</td>'
            + f'<td class="bits">{bar}</td>'
            + td(r["uses"]) + td(r["favourites"]) + td(r["upvotes"])
            + td(r["freshness"])
            + f'<td><span class="pill" style="background:{colour}">{r["verdict"]}</span></td>'
            + f'<td class="small">{html.escape(r["verdict_reason"])}</td>'
            + f'<td class="small">{html.escape(r["hygiene_flags"][:90])}</td>'
            + "</tr>")

    duprows = "".join(
        f"<tr><td>{html.escape(p['keep'])}</td><td>{html.escape(p['retire'])}</td>"
        f"<td class='num'>{p['similarity']}</td>"
        f"<td class='num'>{p['keep_uses']} / {p['retire_uses']}</td>"
        f"<td>{'yes' if p['same_owner'] else 'no'}</td></tr>" for p in pairs)
    if not duprows:
        duprows = "<tr><td colspan=5 class='small'>No near-duplicates above threshold.</td></tr>"

    cards = "".join(
        f'<div class="card"><span class="k">{html.escape(str(v))}</span>'
        f'<span class="l">{html.escape(k)}</span></div>'
        for k, v in stats.items())

    doc = f"""<!doctype html><meta charset="utf-8">
<title>Agent Library Audit</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
        background:var(--artifact-bg,#fff); color:var(--artifact-text,#1a1a1a);
        margin:0; padding:18px; }}
 h1 {{ font-size:19px; margin:0 0 4px; }}
 h2 {{ font-size:15px; margin:26px 0 8px; }}
 .sub {{ color:#777; font-size:12px; margin-bottom:14px; }}
 .cards {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }}
 .card {{ border:1px solid #8883; border-radius:8px; padding:8px 12px; min-width:88px; }}
 .card .k {{ display:block; font-size:19px; font-weight:600; }}
 .card .l {{ display:block; font-size:11px; color:#777; text-transform:uppercase;
             letter-spacing:.4px; }}
 table {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
 th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #8882;
          vertical-align:top; }}
 th {{ font-size:11px; text-transform:uppercase; letter-spacing:.4px; color:#777;
       cursor:pointer; user-select:none; white-space:nowrap; }}
 td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .small {{ font-size:11px; color:#777; }}
 .pill {{ color:#fff; padding:2px 7px; border-radius:9px; font-size:10.5px;
          font-weight:600; letter-spacing:.3px; }}
 .bits i {{ font-style:normal; display:inline-block; width:15px; text-align:center;
            font-size:10px; font-weight:700; border-radius:3px; margin-right:1px; }}
 .bits .on {{ background:#137333; color:#fff; }}
 .bits .off {{ background:#8883; color:#8886; }}
 tbody tr:hover {{ background:#8881; }}
</style>
<h1>Agent Library Audit</h1>
<div class="sub">agentlint v{VERSION} &middot; generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC
&middot; craft bits A&ndash;H, hover a letter for its meaning &middot; click a header to sort</div>
<div class="cards">{cards}</div>
<h2>Agents</h2>
<table id="t"><thead><tr>
<th>Agent</th><th>Craft</th><th>A&ndash;H</th><th>Uses</th><th>Favs</th><th>Votes</th>
<th>Freshness</th><th>Verdict</th><th>Why</th><th>Hygiene flags</th>
</tr></thead><tbody>{''.join(body)}</tbody></table>
<h2>Near-duplicates</h2>
<table><thead><tr><th>Keep</th><th>Retire</th><th>Similarity</th>
<th>Uses keep/retire</th><th>Same owner</th></tr></thead><tbody>{duprows}</tbody></table>
<script>
document.querySelectorAll('#t th').forEach(function(th,i){{
  th.onclick=function(){{
    var tb=th.closest('table').tBodies[0];
    var rows=[].slice.call(tb.rows);
    var asc=th.dataset.asc==='1'; th.dataset.asc=asc?'0':'1';
    rows.sort(function(a,b){{
      var x=a.cells[i].innerText.trim(), y=b.cells[i].innerText.trim();
      var nx=parseFloat(x), ny=parseFloat(y);
      if(!isNaN(nx)&&!isNaN(ny)) return asc?nx-ny:ny-nx;
      return asc?x.localeCompare(y):y.localeCompare(x);
    }});
    rows.forEach(function(r){{tb.appendChild(r);}});
  }};
}});
</script>"""
    open(path, "w", encoding="utf-8").write(doc)


def summarise(recs, pairs, median_uses):
    n = len(recs)
    total_uses = sum(r["uses"] for r in recs)
    total_votes = sum(r["upvotes"] for r in recs)
    total_favs = sum(r["favourites"] for r in recs)
    verified = sum(1 for r in recs if r["verified"])
    stats = {
        "agents": n,
        "total uses": total_uses,
        "upvotes": total_votes,
        "favourites": total_favs,
        "verified": f"{verified}/{n}",
        "median craft": sorted(r["craft_score"] for r in recs)[n // 2] if n else 0,
        "duplicate pairs": len(pairs),
        "endorse": sum(1 for r in recs if r["verdict"] == "ENDORSE"),
        "fix": sum(1 for r in recs if r["verdict"] == "FIX"),
        "retire": sum(1 for r in recs if r["verdict"] == "RETIRE"),
    }
    if total_votes:
        stats["uses per upvote"] = round(total_uses / total_votes)
    return stats


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------
GOOD_PROMPT = """# Demo Expert v1.0
You are the Demo Expert for everyone in the org.

## SCOPE
Answering demo questions.

## TOOL POLICY - match effort to the question
Level 0 - NO tools. Answer directly for greetings.
Stop after at most 2 rounds.

## ANSWER STRUCTURE
1. Answer first.
2. Evidence - tag every fact [RETRIEVED] or [TO BE CONFIRMED].

## GROUNDING
Never invent document numbers, clause numbers or dates.

## GAP TABLE
End with a gap table listing everything still open.

## OUT OF SCOPE
Out of scope for Demo - use Other Expert instead.
"""

BAD_PROMPT = "you help with stuff. do your best."


def selftest():
    checks, failures = [], []

    def ck(label, cond):
        checks.append((label, bool(cond)))
        if not cond:
            failures.append(label)

    good = {"id": "g", "name": "Demo Expert", "ownerId": "u1",
            "description": "Demo support for the whole org. Use this when you need a demo "
                           "answer. If you need something else, use Other Expert instead.",
            "systemPrompt": GOOD_PROMPT, "numberOfUses": 40, "numberOfUpVotes": 2,
            "numberOfFavorites": 5, "numberOfPrompts": 3, "isVerified": False,
            "updatedAt": datetime.now(timezone.utc).isoformat()}
    bad = {"id": "b", "name": "Ultimate Helper Bot V2.1", "ownerId": "u2",
           "description": "<p>Does <strong>things</strong> &amp; stuff. Powered by Claude Opus. "
                          "Role: prd_admin_analyst</p>",
           "systemPrompt": BAD_PROMPT, "numberOfUses": 0, "numberOfUpVotes": 0,
           "numberOfFavorites": 0, "numberOfPrompts": 0, "isVerified": False,
           "updatedAt": "2024-01-01T00:00:00Z"}
    twin = dict(good, id="t", name="Demo Expert Copy", numberOfUses=3)

    recs, pairs, med = audit([good, bad, twin])
    by = {r["id"]: r for r in recs}

    ck("good agent scores 8/8", by["g"]["craft_score"] == 8)
    ck("bad agent scores <=1", by["b"]["craft_score"] <= 1)
    for k, _l, _p, _w in CRAFT:
        ck(f"criterion {k} fires on good prompt", by["g"]["craft_" + k] == 1)
    ck("H clean on good", by["g"]["craft_H"] == 1)
    ck("H dirty on bad", by["b"]["craft_H"] == 0)

    bf = by["b"]["hygiene_flags"]
    ck("detects html-in-description", "html-in-description" in bf)
    ck("detects credentials", "credentials-in-description" in bf)
    ck("detects model name", "model-name-in-description" in bf)
    ck("detects version-in-name", "version-in-name" in bf)
    ck("detects noise tokens", "noise-tokens-in-name" in bf)
    ck("clean name has no noise flag", "noise-tokens-in-name" not in by["g"]["hygiene_flags"])

    ck("finds the duplicate pair", len(pairs) == 1)
    ck("duplicate keeps the busier twin", pairs and pairs[0]["keep"] == "Demo Expert")
    ck("twin marked RETIRE", by["t"]["verdict"] == "RETIRE")
    ck("good marked ENDORSE", by["g"]["verdict"] == "ENDORSE")
    ck("abandoned zero-use marked RETIRE", by["b"]["verdict"] == "RETIRE")
    ck("freshness Abandoned on 2024 stamp", by["b"]["freshness"] == "Abandoned")
    ck("freshness Fresh on today", by["g"]["freshness"] == "Fresh")
    ck("stickiness computed", by["g"]["stickiness_per100"] == 12.5)
    ck("evidence recorded", "prompt:" in by["g"]["evidence"])

    # --- triage: every verdict exercised directly, no median interference ---
    def mkrec(craft=5, uses=10, fresh="Fresh", dup="", flags=""):
        return {"craft_score": craft, "uses": uses, "freshness": fresh,
                "dup_of": dup, "hygiene_flags": flags}

    ck("triage KEEP on mid craft, clean, fresh",
       triage(mkrec(craft=5, uses=10), 10)[0] == "KEEP")
    ck("triage FIX on low craft at median traffic",
       triage(mkrec(craft=4, uses=10), 10)[0] == "FIX")
    ck("triage FIX on a hygiene flag alone",
       triage(mkrec(craft=5, uses=1, flags="name-too-long"), 10)[0] == "FIX")
    ck("triage ENDORSE on high craft, active, aging",
       triage(mkrec(craft=6, uses=1, fresh="Aging"), 10)[0] == "ENDORSE")
    ck("triage RETIRE on duplicate",
       triage(mkrec(dup="Other Agent"), 10)[0] == "RETIRE")
    ck("triage RETIRE on zero use and stale",
       triage(mkrec(uses=0, fresh="Stale"), 10)[0] == "RETIRE")
    ck("triage RETIRE on craft<=3 and abandoned",
       triage(mkrec(craft=3, uses=99, fresh="Abandoned"), 10)[0] == "RETIRE")
    _reason = triage(mkrec(craft=6, uses=1), 10)[1]
    ck("triage reason denominator is /8, not /7",
       "/8" in _reason and "/7" not in _reason)

    # --- freshness: all four bands plus the unknown case ---
    ck("freshness Fresh at 90d", freshness(90) == "Fresh")
    ck("freshness Aging at 120d", freshness(120) == "Aging")
    ck("freshness Stale at 300d", freshness(300) == "Stale")
    ck("freshness Abandoned at 400d", freshness(400) == "Abandoned")
    ck("freshness Unknown on missing date", freshness(None) == "Unknown")

    # --- hygiene: the flags the headline fixtures do not reach ---
    def mkflags(**kw):
        base = {"name": "Clean Domain Object Writer", "description": "x" * 200,
                "systemPrompt": "You are a demo."}
        base.update(kw)
        return lint_hygiene(base)

    ck("detects description-too-short",
       "description-too-short" in mkflags(description="short"))
    ck("detects description-too-long",
       "description-too-long" in mkflags(description="y" * 2100))
    ck("detects description-used-as-manual",
       "description-used-as-manual" in
       mkflags(description="Copy the below prompt for use. " + "z" * 120))
    ck("detects person-name-in-description",
       "person-name-in-description" in
       mkflags(description="Please send feedback to Lucca Richter. " + "z" * 120))
    ck("detects name-too-long", "name-too-long" in mkflags(name="N" * 60))
    ck("detects empty-system-prompt",
       "empty-system-prompt" in mkflags(systemPrompt="   "))
    ck("clean fixture raises no hygiene flags", mkflags() == [])

    # loader shapes
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p1 = os.path.join(d, "a.json")
        json.dump({"botConfigs": [good]}, open(p1, "w"))
        p2 = os.path.join(d, "b.ndjson")
        open(p2, "w").write(json.dumps(bad) + "\n" + json.dumps(twin) + "\n")
        loaded, probs = load_agents(d)
        ck("loads botConfigs wrapper + ndjson from a directory", len(loaded) == 3)
        ck("no loader problems", not probs)
        again, _ = load_agents(p1)
        ck("loads a single wrapped file", len(again) == 1)

    for label, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\n{sum(1 for _l, ok in checks if ok)}/{len(checks)} checks passed")
    return 0 if not failures else 1


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--input", help="export .json/.ndjson file, or a directory of them")
    ap.add_argument("--out-dir", default="/tmp")
    ap.add_argument("--dup-threshold", type=float, default=DUP_THRESHOLD)
    ap.add_argument("--selftest", action="store_true")
    # parse_known_args on purpose - the sandbox harness pollutes sys.argv
    args, _unknown = ap.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.selftest or not args.input:
        if not args.selftest:
            print("No --input given; running self-test instead.\n")
        return selftest()

    agents, problems = load_agents(args.input)
    for p in problems:
        print(f"WARN {p}", file=sys.stderr)
    if not agents:
        print("No agents found in the input.", file=sys.stderr)
        return 2

    recs, pairs, median_uses = audit(agents, dup_threshold=args.dup_threshold)
    stats = summarise(recs, pairs, median_uses)

    os.makedirs(args.out_dir, exist_ok=True)
    csv_path = os.path.join(args.out_dir, "agents_scored.csv")
    dup_path = os.path.join(args.out_dir, "duplicates.csv")
    html_path = os.path.join(args.out_dir, "leaderboard.html")
    write_csv(csv_path, recs)
    write_csv(dup_path, pairs, ["keep", "retire", "similarity",
                                "keep_uses", "retire_uses", "same_owner"])
    write_html(html_path, recs, pairs, stats)

    print(f"agentlint v{VERSION}")
    for k, v in stats.items():
        print(f"  {k:>16}: {v}")
    print("\nTop by craft:")
    for r in sorted(recs, key=lambda r: (-r["craft_score"], -r["uses"]))[:10]:
        print(f"  {r['craft_score']}/8  {r['name'][:46]:46s} uses={r['uses']:5d}  {r['verdict']}")
    retire = [r for r in recs if r["verdict"] == "RETIRE"]
    if retire:
        print("\nRetire candidates:")
        for r in sorted(retire, key=lambda r: -r["uses"]):
            print(f"  {r['name'][:46]:46s} uses={r['uses']:5d}  {r['verdict_reason']}")
    print(f"\nWrote:\n  {csv_path}\n  {dup_path}\n  {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
