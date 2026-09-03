#!/usr/bin/env python3
"""crush.py - local context compression for staged files (MNEMOS toolbelt).

Rationale
---------
Inspired by headroomlabs-ai/headroom, which cannot run here: the sandbox has no
network egress, no pip, and there is no proxy or MCP path into this runtime.
The *ideas* port fine, so this is a stdlib-only reimplementation of the parts
that matter for a file-staging agent:

  * JSON / JSONL  -> schema + field profiles + N samples   (cf. SmartCrusher)
  * logs          -> template dedupe + frequency           (cf. SmartCrusher)
  * source code   -> structural outline, no bodies         (cf. CodeCompressor)
  * CSV           -> header + per-column profile
  * prose / other -> head/tail window

Every compression is *reversible*: the original text of each collapsed region,
plus the whole original file, is written to a keyed cache. `--expand ID`
returns it byte-exact (cf. Headroom's CCR).

Deliberate non-goals
--------------------
  * No semantic prose compression. Kompress-style rewriting needs a model;
    this file is deterministic and dependency-free by design.
  * Token counts are APPROXIMATE (chars/4). No tokenizer is available here.
    Never quote these as measured token savings.

CLI
---
    python crush.py FILE [FILE ...] [--out digest.md] [--cache cache.json]
    python crush.py --expand J3 --cache cache.json
    python crush.py --selftest

Note: this sandbox pre-populates sys.argv with harness junk. When invoking via
runpy with run_name="__main__", set sys.argv explicitly first. See MEM-2026-0007.
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

__version__ = "1.0.0"

CHARS_PER_TOKEN = 4.0  # crude; see module docstring


def approx_tokens(s: str) -> int:
    return max(1, int(round(len(s) / CHARS_PER_TOKEN)))


# --------------------------------------------------------------------------- #
# config + cache
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    samples: int = 2           # sample records kept per collapsed array
    array_threshold: int = 5   # arrays >= this many items get collapsed
    max_str: int = 240         # strings longer than this are truncated + cached
    max_depth: int = 12
    enum_max: int = 12         # <= this many distinct values -> list them all
    log_examples: int = 1
    max_templates: int = 40
    text_head: int = 40
    text_tail: int = 10
    csv_scan: int = 5000


class CrushCache:
    """Keyed store of every original region that was collapsed."""

    def __init__(self) -> None:
        self.entries: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}

    def put(self, kind: str, payload: str) -> str:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        cid = f"{kind}{n}"
        self.entries[cid] = payload
        return cid

    def get(self, cid: str) -> Optional[str]:
        return self.entries.get(cid)

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": __version__, "entries": self.entries}, fh,
                      ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str) -> "CrushCache":
        obj = cls()
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        obj.entries = dict(data.get("entries", {}))
        for cid in obj.entries:
            m = re.match(r"([A-Z]+)(\d+)$", cid)
            if m:
                k, n = m.group(1), int(m.group(2))
                obj._counters[k] = max(obj._counters.get(k, 0), n)
        return obj

    @property
    def bytes(self) -> int:
        return sum(len(v) for v in self.entries.values())


@dataclass
class Digest:
    source: str
    kind: str
    original_chars: int
    text: str
    notes: List[str] = field(default_factory=list)
    full_ref: str = ""

    @property
    def digest_chars(self) -> int:
        return len(self.text)

    @property
    def ratio(self) -> float:
        if self.original_chars == 0:
            return 1.0
        return self.digest_chars / self.original_chars

    def header(self) -> str:
        return (f"# crush: {os.path.basename(self.source)}  [{self.kind}]\n"
                f"- original ~{approx_tokens('x' * self.original_chars)} tok "
                f"({self.original_chars} chars) -> digest ~"
                f"{approx_tokens(self.text)} tok ({self.digest_chars} chars) "
                f"= {self.ratio * 100:.1f}%\n"
                f"- full original recoverable: ref={self.full_ref}\n")

    def render(self) -> str:
        out = [self.header()]
        for n in self.notes:
            out.append(f"- note: {n}\n")
        out.append("\n")
        out.append(self.text)
        return "".join(out)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #

def _tname(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _profile_field(values: List[Any], cfg: Config) -> Dict[str, Any]:
    present = [v for v in values if v is not None]
    types = sorted({_tname(v) for v in present})
    prof: Dict[str, Any] = {"type": "|".join(types) if types else "null"}
    nulls = len(values) - len(present)
    if nulls:
        prof["nulls"] = nulls
    scalars = [v for v in present if isinstance(v, (str, int, float, bool))]
    if not present or len(scalars) != len(present):
        return prof
    uniq = set(scalars)
    prof["unique"] = len(uniq)
    if len(uniq) <= cfg.enum_max:
        prof["values"] = sorted(uniq, key=lambda x: (str(type(x)), str(x)))
    elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in scalars):
        prof["min"] = min(scalars)
        prof["max"] = max(scalars)
    elif all(isinstance(v, str) for v in scalars):
        lens = [len(v) for v in scalars]
        prof["len"] = f"{min(lens)}..{max(lens)}"
        prof["sample"] = scalars[0][:60]
    return prof


def _crush_array(arr: List[Any], cache: CrushCache, cfg: Config, depth: int) -> Any:
    ref = cache.put("J", json.dumps(arr, ensure_ascii=False, default=str))
    n = len(arr)
    if all(isinstance(x, dict) for x in arr):
        keys: List[str] = []
        for x in arr:
            for k in x:
                if k not in keys:
                    keys.append(k)
        schema: Dict[str, Any] = {}
        for k in keys:
            vals = [x.get(k) for x in arr]
            prof = _profile_field(vals, cfg)
            missing = sum(1 for x in arr if k not in x)
            if missing:
                prof["missing"] = missing
            schema[k] = prof
        return {
            "__crushed": f"array<object>[{n}]",
            "ref": ref,
            "schema": schema,
            "samples": [_crush_value(x, cache, cfg, depth + 1)
                        for x in arr[:cfg.samples]],
        }
    if all(isinstance(x, (str, int, float, bool)) or x is None for x in arr):
        return {
            "__crushed": f"array<scalar>[{n}]",
            "ref": ref,
            "profile": _profile_field(arr, cfg),
            "samples": arr[:cfg.samples],
        }
    return {
        "__crushed": f"array<mixed>[{n}]",
        "ref": ref,
        "types": sorted({_tname(x) for x in arr}),
        "samples": [_crush_value(x, cache, cfg, depth + 1) for x in arr[:cfg.samples]],
    }


def _crush_value(v: Any, cache: CrushCache, cfg: Config, depth: int = 0) -> Any:
    if depth > cfg.max_depth:
        return "<depth-limit>"
    if isinstance(v, str) and len(v) > cfg.max_str:
        ref = cache.put("S", v)
        return f"{v[:cfg.max_str]}... <+{len(v) - cfg.max_str} chars ref={ref}>"
    if isinstance(v, list):
        if len(v) >= cfg.array_threshold:
            return _crush_array(v, cache, cfg, depth)
        return [_crush_value(x, cache, cfg, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: _crush_value(x, cache, cfg, depth + 1) for k, x in v.items()}
    return v


def crush_json(text: str, cache: CrushCache, cfg: Config,
               source: str = "<json>") -> Digest:
    notes: List[str] = []
    try:
        obj = json.loads(text)
        kind = "json"
    except json.JSONDecodeError as exc:
        rows = []
        try:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        except json.JSONDecodeError:
            raise ValueError(f"not JSON or JSONL: {exc.msg}") from exc
        if not rows:
            raise ValueError("not JSON or JSONL: empty")
        obj = rows
        kind = "jsonl"
        notes.append(f"parsed as JSONL ({len(rows)} records)")
    crushed = _crush_value(obj, cache, cfg)
    body = json.dumps(crushed, ensure_ascii=False, indent=1, default=str)
    return Digest(source=source, kind=kind, original_chars=len(text),
                  text=body, notes=notes)


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #

_SUBS: List[Tuple[Any, str]] = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"), "<TIME>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HASH>"),
    (re.compile(r"(?<=[=:\s])/[\w./-]{4,}"), "<PATH>"),
    # NB: \b\d+\b is WRONG here - "37ms" has no boundary between 7 and m, so
    # unit-suffixed numbers escape templating and explode the template count.
    (re.compile(r"(?<![\w.])\d+\.\d+"), "<F>"),
    (re.compile(r"(?<!\w)\d+"), "<N>"),
]


def templatize(line: str) -> str:
    for rx, tok in _SUBS:
        line = rx.sub(tok, line)
    return line


def crush_log(text: str, cache: CrushCache, cfg: Config,
              source: str = "<log>") -> Digest:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    groups: Dict[str, Dict[str, Any]] = {}
    for i, ln in enumerate(lines):
        t = templatize(ln)
        g = groups.setdefault(t, {"count": 0, "first": i, "last": i, "lines": []})
        g["count"] += 1
        g["last"] = i
        g["lines"].append(ln)

    ordered = sorted(groups.items(), key=lambda kv: -kv[1]["count"])
    out = io.StringIO()
    out.write(f"{len(lines)} lines -> {len(groups)} templates\n\n")
    for t, g in ordered[:cfg.max_templates]:
        ref = cache.put("L", "\n".join(g["lines"]))
        out.write(f"{g['count']:>7}x  {t}\n")
        for ex in g["lines"][:cfg.log_examples]:
            out.write(f"          e.g. {ex[:300]}\n")
        out.write(f"          lines {g['first']}-{g['last']}  ref={ref}\n")
    hidden = len(ordered) - cfg.max_templates
    notes = []
    if hidden > 0:
        rest = "\n".join("\n".join(g["lines"]) for _, g in ordered[cfg.max_templates:])
        ref = cache.put("L", rest)
        out.write(f"\n... {hidden} rarer templates omitted, ref={ref}\n")
        notes.append(f"{hidden} low-frequency templates collapsed into {ref}")
    return Digest(source=source, kind="log", original_chars=len(text),
                  text=out.getvalue(), notes=notes)


# --------------------------------------------------------------------------- #
# code
# --------------------------------------------------------------------------- #

_DECL_RX = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?"
    r"(?:function|class|interface|type|struct|impl|fn|func|def|const\s+\w+\s*=\s*(?:async\s*)?\()"
    r".*$")


def _py_outline(text: str) -> str:
    tree = ast.parse(text)
    out = io.StringIO()

    def sig(node: ast.AST) -> str:
        args = getattr(node, "args", None)
        if args is None:
            return "()"
        names = [a.arg for a in list(args.posonlyargs) + list(args.args)]
        if args.vararg:
            names.append("*" + args.vararg.arg)
        names += [a.arg for a in args.kwonlyargs]
        if args.kwarg:
            names.append("**" + args.kwarg.arg)
        return "(" + ", ".join(names) + ")"

    def walk(node: ast.AST, indent: int) -> None:
        for child in getattr(node, "body", []):
            pad = "  " * indent
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = (ast.get_docstring(child) or "").strip().splitlines()
                first = f"  # {doc[0][:70]}" if doc else ""
                out.write(f"{pad}L{child.lineno}: def {child.name}{sig(child)}{first}\n")
                walk(child, indent + 1)
            elif isinstance(child, ast.ClassDef):
                doc = (ast.get_docstring(child) or "").strip().splitlines()
                first = f"  # {doc[0][:70]}" if doc else ""
                out.write(f"{pad}L{child.lineno}: class {child.name}{first}\n")
                walk(child, indent + 1)
            elif isinstance(child, (ast.Import, ast.ImportFrom)) and indent == 0:
                pass
    walk(tree, 0)
    imports = sorted({
        (a.name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0])
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n.names[0]])
    })
    return f"imports: {', '.join(i for i in imports if i)}\n\n" + out.getvalue()


def crush_code(text: str, cache: CrushCache, cfg: Config,
               source: str = "<code>") -> Digest:
    notes: List[str] = []
    if source.endswith(".py"):
        try:
            body = _py_outline(text)
        except SyntaxError as exc:
            body = ""
            notes.append(f"python parse failed ({exc.msg}); fell back to regex scan")
    else:
        body = ""
    if not body.strip():
        hits = []
        for i, ln in enumerate(text.splitlines(), 1):
            if _DECL_RX.match(ln) and len(ln) < 200:
                hits.append(f"L{i}: {ln.strip()}")
        body = "\n".join(hits) + "\n"
        notes.append("regex declaration scan (no AST for this language)")
    return Digest(source=source, kind="code", original_chars=len(text),
                  text=body, notes=notes)


# --------------------------------------------------------------------------- #
# CSV + plain text
# --------------------------------------------------------------------------- #

def crush_csv(text: str, cache: CrushCache, cfg: Config,
              source: str = "<csv>") -> Digest:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return Digest(source, "csv", len(text), "(empty)\n")
    header, data = rows[0], rows[1:]
    scan = data[:cfg.csv_scan]
    out = io.StringIO()
    out.write(f"{len(data)} rows x {len(header)} cols "
              f"(profiled on first {len(scan)})\n\n")
    for ci, name in enumerate(header):
        col = [r[ci] for r in scan if ci < len(r)]
        prof = _profile_field(col, cfg)
        out.write(f"  {name}: {json.dumps(prof, ensure_ascii=False, default=str)}\n")
    out.write("\nsamples:\n")
    for r in data[:cfg.samples]:
        out.write("  " + ",".join(x[:40] for x in r) + "\n")
    return Digest(source=source, kind="csv", original_chars=len(text),
                  text=out.getvalue())


def crush_text(text: str, cache: CrushCache, cfg: Config,
               source: str = "<text>") -> Digest:
    lines = text.splitlines()
    if len(lines) <= cfg.text_head + cfg.text_tail:
        return Digest(source=source, kind="text", original_chars=len(text),
                      text=text, notes=["short enough; passed through verbatim"])
    mid = "\n".join(lines[cfg.text_head:len(lines) - cfg.text_tail])
    ref = cache.put("T", mid)
    body = ("\n".join(lines[:cfg.text_head])
            + f"\n\n... [{len(lines) - cfg.text_head - cfg.text_tail} lines elided,"
              f" ref={ref}] ...\n\n"
            + "\n".join(lines[len(lines) - cfg.text_tail:]) + "\n")
    return Digest(source=source, kind="text", original_chars=len(text), text=body,
                  notes=["no semantic compression applied - head/tail window only"])


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
            ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt"}


def _looks_like_log(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()][:400]
    if len(lines) < 20:
        return False
    templates = {templatize(ln) for ln in lines}
    return len(templates) <= 0.6 * len(lines)


def crush_text_blob(text: str, cache: CrushCache, cfg: Config,
                    source: str) -> Digest:
    ext = os.path.splitext(source)[1].lower()
    if ext in (".json", ".jsonl", ".ndjson"):
        try:
            return crush_json(text, cache, cfg, source)
        except ValueError:
            pass  # mislabelled or corrupt - fall through to sniffing
    if ext in CODE_EXT:
        return crush_code(text, cache, cfg, source)
    if ext in (".csv", ".tsv"):
        return crush_csv(text, cache, cfg, source)
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            return crush_json(text, cache, cfg, source)
        except (json.JSONDecodeError, ValueError):
            pass
    if _looks_like_log(text):
        return crush_log(text, cache, cfg, source)
    return crush_text(text, cache, cfg, source)


def crush_file(path: str, cache: CrushCache, cfg: Config) -> Digest:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    full_ref = cache.put("F", text)
    dg = crush_text_blob(text, cache, cfg, path)
    dg.full_ref = full_ref
    return dg


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    import random
    import shutil
    import tempfile
    random.seed(7)
    tmp = tempfile.mkdtemp(prefix="crushtest_")
    failures: List[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    cfg = Config()

    # 1. big JSON
    recs = [{"id": i,
             "status": random.choice(["ok", "fail", "pending"]),
             "user": f"user{random.randint(1, 400)}@example.com",
             "latency_ms": round(random.uniform(1, 900), 2),
             "meta": {"region": random.choice(["eu", "us", "apac"]), "retry": i % 4}}
            for i in range(1200)]
    jpath = os.path.join(tmp, "events.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump({"generated": "2026-08-24", "events": recs}, fh)
    cache = CrushCache()
    dg = crush_file(jpath, cache, cfg)
    print(dg.header())
    check("json: digest under 10% of original", dg.ratio < 0.10,
          f"ratio={dg.ratio * 100:.2f}%")
    check("json: schema lists status enum", '"pending"' in dg.text)
    check("json: array ref emitted", '"ref": "J1"' in dg.text)
    back = json.loads(cache.get("J1"))
    check("json: collapsed array round-trips exactly", back == recs)
    with open(jpath, "r", encoding="utf-8") as fh:
        check("json: whole file round-trips byte-exact",
              cache.get(dg.full_ref) == fh.read())

    # 2. logs
    lpath = os.path.join(tmp, "app.log")
    with open(lpath, "w", encoding="utf-8") as fh:
        for i in range(4000):
            ts = f"2026-08-24T10:{i // 60 % 60:02d}:{i % 60:02d}Z"
            r = random.random()
            if r < 0.7:
                fh.write(f"{ts} INFO  request served in {random.randint(1, 90)}ms\n")
            elif r < 0.9:
                fh.write(f"{ts} WARN  cache miss for key 0x{random.getrandbits(32):08x}\n")
            else:
                fh.write(f"{ts} ERROR db timeout after {random.randint(1, 5) * 1000}ms "
                         f"from 10.0.{random.randint(0, 9)}.{random.randint(1, 250)}\n")
    cache2 = CrushCache()
    dg2 = crush_file(lpath, cache2, cfg)
    print(dg2.header())
    check("log: routed to log crusher", dg2.kind == "log")
    check("log: digest under 5% of original", dg2.ratio < 0.05,
          f"ratio={dg2.ratio * 100:.2f}%")
    check("log: collapsed to few templates", "-> 3 templates" in dg2.text, dg2.text.splitlines()[0])
    grp = cache2.get("L1")
    check("log: template group expands to real lines",
          grp is not None and all(ln in open(lpath).read() for ln in grp.splitlines()[:5]))

    # 3. small input must pass through untouched
    spath = os.path.join(tmp, "small.json")
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump({"a": 1, "b": "two", "c": [1, 2]}, fh)
    cache3 = CrushCache()
    dg3 = crush_file(spath, cache3, cfg)
    check("small json: values preserved verbatim",
          json.loads(dg3.text) == {"a": 1, "b": "two", "c": [1, 2]})

    # 4. code outline
    cpath = os.path.join(tmp, "mod.py")
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write('import os\n\n\nclass Widget:\n    """A widget."""\n\n'
                 '    def render(self, ctx, *, deep=False):\n'
                 '        """Draw it."""\n        ' + "x = 1\n        " * 60 +
                 '\n\ndef helper(a, b=2):\n    return a + b\n')
    cache4 = CrushCache()
    dg4 = crush_file(cpath, cache4, cfg)
    check("code: class captured", "class Widget" in dg4.text)
    check("code: signature captured", "def render(self, deep)" in dg4.text
          or "def render(self" in dg4.text)
    check("code: bodies dropped", "x = 1" not in dg4.text)
    check("code: smaller than source", dg4.ratio < 0.5, f"ratio={dg4.ratio * 100:.1f}%")

    # 5. malformed JSON must not crash
    bpath = os.path.join(tmp, "broken.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        fh.write("{not really json at all, just text\n" * 3)
    cache5 = CrushCache()
    try:
        dg5 = crush_file(bpath, cache5, cfg)
        check("malformed json: falls back without crashing", dg5.kind in ("text", "log"))
    except Exception as exc:  # noqa: BLE001
        check("malformed json: falls back without crashing", False, repr(exc))

    # 6. cache persistence
    cp = os.path.join(tmp, "cache.json")
    cache.save(cp)
    reloaded = CrushCache.load(cp)
    check("cache: survives save/load", reloaded.get("J1") == cache.get("J1"))
    check("cache: counters restored", reloaded.put("J", "x") == f"J{cache._counters['J'] + 1}")

    # 7. CSV
    vpath = os.path.join(tmp, "t.csv")
    with open(vpath, "w", encoding="utf-8") as fh:
        fh.write("id,region,amount\n")
        for i in range(800):
            fh.write(f"{i},{random.choice(['eu', 'us'])},{random.randint(1, 999)}\n")
    cache6 = CrushCache()
    dg6 = crush_file(vpath, cache6, cfg)
    check("csv: row count reported", "800 rows" in dg6.text)
    check("csv: low-cardinality column enumerated", "'eu'" in dg6.text or '"eu"' in dg6.text)
    check("csv: digest under 15% of original", dg6.ratio < 0.15, f"ratio={dg6.ratio * 100:.1f}%")

    shutil.rmtree(tmp, ignore_errors=True)  # keep /tmp clean: the harness
    # publishes anything left there into /code-interpreter-output
    print(f"\n{len(failures)} failure(s)" + (": " + ", ".join(failures) if failures else ""))
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="crush.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="files to compress")
    ap.add_argument("--cache", default="/tmp/crush_cache.json")
    ap.add_argument("--out", help="write digest here (default: stdout)")
    ap.add_argument("--samples", type=int, default=Config.samples)
    ap.add_argument("--max-templates", type=int, default=Config.max_templates)
    ap.add_argument("--expand", metavar="ID", help="print a cached original and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.expand:
        if not os.path.exists(args.cache):
            print(f"no cache at {args.cache}", file=sys.stderr)
            return 2
        val = CrushCache.load(args.cache).get(args.expand)
        if val is None:
            print(f"no such ref: {args.expand}", file=sys.stderr)
            return 2
        sys.stdout.write(val)
        return 0

    if not args.files:
        ap.print_help()
        return 2

    cfg = Config(samples=args.samples, max_templates=args.max_templates)
    cache = CrushCache()
    parts, before, after = [], 0, 0
    for path in args.files:
        dg = crush_file(path, cache, cfg)
        before += dg.original_chars
        after += dg.digest_chars
        parts.append(dg.render())
    cache.save(args.cache)
    parts.append(f"\n---\ncache: {args.cache} ({len(cache.entries)} refs, "
                 f"{cache.bytes} chars)\n"
                 f"total ~{approx_tokens('x' * before)} -> ~{approx_tokens('x' * after)} tok "
                 f"({after / max(before, 1) * 100:.1f}%)  [approximate: chars/4]\n")
    doc = "\n".join(parts)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(doc)
        print(f"wrote {args.out} ({len(doc)} chars); cache {args.cache}")
    else:
        sys.stdout.write(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
