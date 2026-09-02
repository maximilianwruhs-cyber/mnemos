#!/usr/bin/env python3
"""recall.py - deterministic hybrid recall over the MNEMOS note graph.

WHY THIS EXISTS. Retrieval was lexical-only and single-hop: a note phrased
differently from the query, or reachable only through a [[MEM-]] link, never
surfaced. A vector layer was proposed and measured down on 2026-08-30 - this
runtime has no embedding model and no network to fetch one, and pure-stdlib
cosine was ~9x slower than the claimed budget. What survives that measurement
is the part of the design needing no model: BM25 + graph expansion +
deterministic reranking.

NO DERIVED ARTIFACTS. The corpus is small enough to score at query time
(44 notes / ~79 KB; whole-corpus read measured at 1.5 ms), so there is no index
to build, invalidate, or leave behind. Files stay the database.

SCORING
    S(d,q) = 0.45*L + 0.25*G + 0.10*R + 0.20*I
    L  BM25 (k1=1.5, b=0.75); heading tokens counted twice
    G  best link-decayed lexical seed score, 0.5^hops, at most 2 hops
    R  recency from `created:` frontmatter, half-life 90 days, measured against
       the newest note in the corpus - no wall clock, so runs are reproducible
    I  salience from `**Salience:**`, default 0.5 when the field is absent
    Ties break on document id, so ordering is total and stable.

SANDBOX CONTRACT
    Stage notes flat into /tmp together with _manifest.json (staged filename ->
    real store path). Query comes from argv or RECALL_QUERY; self-test from
    --selftest or RECALL_MODE=selftest. No argparse: MEM-2026-0007, the harness
    injects foreign argv.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True

STAGE = "/tmp"
W_LEX, W_GRAPH, W_REC, W_SAL = 0.45, 0.25, 0.10, 0.20
K1, B = 1.5, 0.75
HALFLIFE_DAYS = 90.0
HOP_DECAY = 0.5
MAX_HOPS = 2
MAX_SEEDS = 5
DEFAULT_SALIENCE = 0.5
VEC_WEIGHTS = {"lex": 0.30, "vec": 0.25, "graph": 0.25, "rec": 0.05, "sal": 0.15}
VEC_ACTIVATE_N = 1000


def _vec_enabled(count):
    flag = os.environ.get("RECALL_VEC")
    if flag == "1":
        return True
    if flag == "0":
        return False
    return count >= VEC_ACTIVATE_N


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
MEMID_RE = re.compile(r"MEM-\d{4}-\d{4}")
L3PATH_RE = re.compile(r"(Memory/[\w./-]+\.md)")
NOTE_RE = re.compile(r"^### \[(MEM-\d{4}-\d{4})\]\s+(.+?)\s*$", re.M)
CREATED_RE = re.compile(r"^created:\s*(\d{4})-(\d{2})-(\d{2})", re.M)
SALIENCE_RE = re.compile(r"\*\*Salience:\*\*\s*([01](?:\.\d+)?)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
TITLE_RE = re.compile(r"^title:\s*(.+)$", re.M)

STOP = {
    "the", "and", "for", "that", "this", "with", "from", "not", "but", "are",
    "was", "were", "has", "have", "had", "its", "it", "is", "of", "to", "in",
    "on", "as", "at", "by", "an", "or", "be", "a", "no", "so", "if", "then",
    "der", "die", "das", "und", "ist", "mit", "von", "den", "dem", "des",
}

SKIP_NAMES = {"INDEX.md", "INDEX-L3.md", "PROTOCOL.md", "AGENTS.md"}


def tokens(text):
    out = []
    for m in TOKEN_RE.finditer(text.lower()):
        t = m.group(0).strip(".-_")
        if len(t) >= 2 and t not in STOP:
            out.append(t)
    return out


def load_manifest(stage=STAGE):
    path = os.path.join(stage, "_manifest.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_corpus(stage=STAGE, manifest=None):
    """Return docs: id -> {path, title, text, created, salience, mentions}."""
    manifest = manifest if manifest is not None else load_manifest(stage)
    docs = {}
    for name in sorted(os.listdir(stage)):
        if not name.endswith(".md"):
            continue
        real = manifest.get(name, "/" + name)
        if "/_archive/" in real or name in SKIP_NAMES:
            continue
        full = os.path.join(stage, name)
        try:
            text = Path(full).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if name == "MEMORY.md":
            docs.update(_split_l2(text, real))
            continue
        docs[real] = _make_doc(real, _title_of(text, name), text)
    return docs


def _title_of(text, fallback):
    m = TITLE_RE.search(text)
    if m:
        return m.group(1).strip()
    m = HEADING_RE.search(text)
    if m:
        return m.group(1).strip()
    return fallback


def _split_l2(text, real):
    out = {}
    hits = list(NOTE_RE.finditer(text))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[m.end():end]
        cut = body.find("\n## ")
        if cut >= 0:
            body = body[:cut]
        out[m.group(1)] = _make_doc(real + "#" + m.group(1), m.group(2), body,
                                    doc_id=m.group(1))
    return out


def _make_doc(path, title, text, doc_id=None):
    created = None
    m = CREATED_RE.search(text)
    if m:
        created = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    sal = SALIENCE_RE.search(text)
    # A note is addressable by every ID it declares as its OWN - the filename
    # and any heading. That is the graphcheck C5 self-ID convention, and it is
    # what makes an L3 file reachable by ID and not only by path.
    self_ids = set(MEMID_RE.findall(os.path.basename(path)))
    for heading in HEADING_RE.findall(text):
        self_ids |= set(MEMID_RE.findall(heading))
    if doc_id:
        self_ids.add(doc_id)
    mentions = (set(MEMID_RE.findall(text)) | {
        "/" + p.lstrip("/") for p in L3PATH_RE.findall(text)
    }) - self_ids
    body_tokens = tokens(text) + tokens(title) * 2
    return {
        "path": path,
        "title": title.strip(),
        "text": text,
        "created": created,
        "salience": float(sal.group(1)) if sal else DEFAULT_SALIENCE,
        "mentions": mentions,
        "self_ids": self_ids,
        "tokens": body_tokens,
    }


def build_edges(docs):
    """Undirected adjacency: a doc links to another it names by ID or path."""
    # One key may address several documents on purpose: an ID names both the
    # L2 stub and the L3 file that carries it, so a reference reaches both tiers.
    by_key = defaultdict(set)
    for did, d in docs.items():
        by_key[did].add(did)
        by_key[d["path"]].add(did)
        for sid in d.get("self_ids", ()):
            by_key[sid].add(did)
    adj = defaultdict(set)
    for did, d in docs.items():
        for ref in d["mentions"]:
            for tgt in by_key.get(ref, ()):
                if tgt != did:
                    adj[did].add(tgt)
                    adj[tgt].add(did)
    return adj


def bm25(docs, query_tokens):
    n = len(docs)
    if n == 0:
        return {}
    freqs, lengths = {}, {}
    df = defaultdict(int)
    for did, d in docs.items():
        tf = defaultdict(int)
        for t in d["tokens"]:
            tf[t] += 1
        freqs[did] = tf
        lengths[did] = len(d["tokens"]) or 1
        for t in tf:
            df[t] += 1
    avgdl = sum(lengths.values()) / n
    scores = {}
    for did in docs:
        tf, dl = freqs[did], lengths[did]
        s = 0.0
        for q in query_tokens:
            f = tf.get(q, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * dl / avgdl))
        scores[did] = s
    return scores


def _normalise(scores):
    top = max(scores.values()) if scores else 0.0
    if top <= 0:
        return {k: 0.0 for k in scores}
    return {k: v / top for k, v in scores.items()}


def graph_scores(lex, adj):
    seeds = [d for d, s in lex.items() if s > 0]
    seeds.sort(key=lambda d: (-lex[d], d))
    seeds = seeds[:MAX_SEEDS]
    out = defaultdict(float)
    for seed in seeds:
        frontier = {seed}
        seen = {seed}
        for hop in range(1, MAX_HOPS + 1):
            nxt = set()
            for node in frontier:
                for nb in adj.get(node, ()):
                    if nb in seen:
                        continue
                    nxt.add(nb)
                    out[nb] = max(out[nb], lex[seed] * (HOP_DECAY ** hop))
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
    return out


def recency_scores(docs):
    dates = [d["created"] for d in docs.values() if d["created"]]
    if not dates:
        return {did: 0.5 for did in docs}
    newest = max(dates)
    out = {}
    for did, d in docs.items():
        if not d["created"]:
            out[did] = 0.5
            continue
        age = (newest - d["created"]).days
        out[did] = math.exp(-math.log(2) * age / HALFLIFE_DAYS)
    return out


def search(query, docs, limit=5, stage=None):
    qt = tokens(query)
    if not qt:
        raise ValueError("query has no usable tokens")
    lex = _normalise(bm25(docs, qt))
    adj = build_edges(docs)
    gph = graph_scores(lex, adj)
    rec = recency_scores(docs)

    vec_raw, vecn, use_vec = {}, {}, False
    if stage is not None:
        try:
            import vecidx
            if vecidx.available() and _vec_enabled(len(docs)):
                loaded = vecidx.load(stage)
                if loaded is not None:
                    keys, mat = loaded
                    present = [k for k in keys if k in docs]
                    if present:
                        idx = [keys.index(k) for k in present]
                        vec_raw = vecidx.search_vectors(query, present, mat[idx])
                        vecn = _normalise({k: max(0.0, v) for k, v in vec_raw.items()})
                        use_vec = True
        except Exception:
            use_vec = False  # any failure => exact lexical fallback

    if use_vec:
        w = VEC_WEIGHTS
    else:
        w = {"lex": W_LEX, "vec": 0.0, "graph": W_GRAPH, "rec": W_REC, "sal": W_SAL}

    rows = []
    for did, d in docs.items():
        g = gph.get(did, 0.0)
        v = vecn.get(did, 0.0)
        total = (w["lex"] * lex[did] + w["vec"] * v + w["graph"] * g
                 + w["rec"] * rec[did] + w["sal"] * d["salience"])
        row = {
            "id": did, "path": d["path"], "title": d["title"],
            "score": total, "lex": lex[did], "graph": g,
            "rec": rec[did], "sal": d["salience"],
            "route": "lexical" if lex[did] > 0 else (
                "graph" if g > 0 else ("vector" if v > 0 else "-")),
            "snippet": _snippet(d["text"], qt),
        }
        if use_vec:
            row["vec"] = vec_raw.get(did, 0.0)
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    keep = [r for r in rows if r["lex"] > 0 or r["graph"] > 0
            or (use_vec and r.get("vec", 0.0) >= vecidx.VEC_FLOOR)]
    return keep[:limit]


def _snippet(text, qt, width=110):
    want = set(qt)
    for line in text.splitlines():
        low = set(tokens(line))
        if low & want:
            s = " ".join(line.split())
            return s[:width]
    return ""


def render(rows, query):
    out = ["=" * 78, f"RECALL  query: {query}", "=" * 78]
    if not rows:
        out.append("  no note matched lexically or through the link graph")
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. [{r['score']:.3f}] {r['title']}")
        out.append(f"   {r['path']}")
        out.append(f"   route={r['route']}  lex={r['lex']:.2f} "
                   f"graph={r['graph']:.2f} rec={r['rec']:.2f} sal={r['sal']:.2f}")
        if r["snippet"]:
            out.append(f"   > {r['snippet']}")
    out.append("=" * 78)
    return "\n".join(out)


def selftest():
    import shutil
    import tempfile
    work = tempfile.mkdtemp(prefix="recalltest_", dir="/tmp")
    checks = []
    try:
        def put(name, body):
            with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
                fh.write(body)

        put("alpha.md", "---\ntitle: sandbox has no network egress\n"
                        "created: 2026-08-01\n---\n\n# MEM-2026-0001 - egress\n"
                        "Socket connections to pypi fail with OSError.\n")
        put("beta.md", "---\ntitle: fetching strategy\ncreated: 2026-08-20\n---\n\n"
                       "# MEM-2026-0002 - fetching\nRoute everything through the "
                       "documented websearch tool. See MEM-2026-0001.\n")
        put("gamma.md", "---\ntitle: unrelated pdf tables\ncreated: 2026-08-02\n---\n\n"
                        "# MEM-2026-0003 - tables\nColumn alignment traps in "
                        "page-split tables.\n")
        with open(os.path.join(work, "_manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"alpha.md": "/Memory/context/alpha.md",
                       "beta.md": "/Memory/context/beta.md",
                       "gamma.md": "/Memory/lessons/gamma.md"}, fh)

        docs = build_corpus(work)
        checks.append(("corpus built", len(docs) == 3))

        hits = search("socket egress", docs)
        checks.append(("exact term ranks first",
                       hits and hits[0]["path"] == "/Memory/context/alpha.md"))

        ids = [h["path"] for h in hits]
        checks.append(("graph pulls in a link-only neighbour",
                       "/Memory/context/beta.md" in ids))
        beta = [h for h in hits if h["path"].endswith("beta.md")]
        checks.append(("neighbour surfaced without the query term",
                       bool(beta) and beta[0]["graph"] > 0))

        checks.append(("unrelated note excluded",
                       all("gamma" not in p for p in ids)))

        a = [(h["path"], round(h["score"], 9)) for h in search("socket egress", docs)]
        b = [(h["path"], round(h["score"], 9)) for h in search("socket egress", docs)]
        checks.append(("deterministic across runs", a == b))

        rare = search("pypi", docs)
        checks.append(("rare term retrieves its note",
                       rare and rare[0]["path"] == "/Memory/context/alpha.md"))

        try:
            search("   ", docs)
            empty_ok = False
        except ValueError:
            empty_ok = True
        checks.append(("empty query rejected", empty_ok))

        recent = recency_scores(docs)
        checks.append(("recency favours the newer note",
                       recent["/Memory/context/beta.md"]
                       > recent["/Memory/context/alpha.md"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    bad = [c for c in checks if not c[1]]
    print(f"RESULT: {'PASS' if not bad else 'FAIL'} - "
          f"{len(checks) - len(bad)}/{len(checks)} recall assertions")
    return 1 if bad else 0


def main():
    argv = [a for a in sys.argv[1:] if not a.endswith(".py")]
    if "--selftest" in argv or os.environ.get("RECALL_MODE") == "selftest":
        return selftest()
    words = [a for a in argv if not a.startswith("--")]
    query = " ".join(words) or os.environ.get("RECALL_QUERY", "")
    if not query.strip():
        print("usage: recall.py QUERY...   (or RECALL_QUERY=... / --selftest)",
              file=sys.stderr)
        return 2
    limit = 5
    for a in argv:
        if a.startswith("--limit="):
            limit = max(1, int(a.split("=", 1)[1]))
    docs = build_corpus()
    if not docs:
        print("no staged notes found in /tmp", file=sys.stderr)
        return 2
    try:
        rows = search(query, docs, limit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render(rows, query))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
