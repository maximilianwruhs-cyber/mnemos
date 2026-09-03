"""repomap - deterministic structural outline of Python sources, via stdlib `ast`.

WHAT THIS IS NOT. It is not a port of Egonex-AI/Understand-Anything. That project
is a TypeScript multi-agent pipeline that builds an LLM-annotated knowledge graph
of a codebase; it needs Node, and its value scales with repo size. This workspace
has 4 Python files / ~1.1k lines, where such a pipeline would be pure ceremony.

WHAT IT IS. One principle from that project does transfer, and is worth stating:
**extract structure deterministically first, and only let a model add meaning on
top of it.** They use Tree-Sitter for the skeleton. Tree-Sitter is absent here, but
Python ships `ast`, which for Python sources is exact rather than approximate.

WHY IT EXISTS. Verified 2026-08-24: `fs_read_outline` reports every .py file in
this store as "binary (application/octet-stream)" and returns no structure, and
`fs_grep` on /scripts/<file>.py fails with "Not a directory". So the normal way to
see what is in a script does not work, and the fallback is staging it into the
sandbox and printing line ranges by hand. This closes that gap in one call.

USAGE
    python repomap.py                      # self-test
    from repomap import outline, render
    print(render(["/tmp/doclite.py", "/tmp/guard.py"]))
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field

__all__ = ["Symbol", "outline", "render"]


@dataclass
class Symbol:
    kind: str                     # "class" | "def" | "async def"
    qualname: str
    lineno: int
    end_lineno: int
    signature: str = ""
    doc: str = ""
    decorators: list = field(default_factory=list)
    calls: list = field(default_factory=list)   # names invoked in this body

    @property
    def span(self) -> int:
        return self.end_lineno - self.lineno + 1


def _first_doc_line(node) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _calls_in(node) -> list:
    """Names invoked directly in this body, excluding nested def bodies."""
    found, nested = [], set()
    for child in ast.walk(node):
        if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested.update(id(n) for n in ast.walk(child))
    for child in ast.walk(node):
        if id(child) in nested or not isinstance(child, ast.Call):
            continue
        f = child.func
        if isinstance(f, ast.Name):
            found.append(f.id)
        elif isinstance(f, ast.Attribute):
            found.append(f.attr)
    # order-preserving dedupe
    return list(dict.fromkeys(found))


def outline(path: str):
    """Return (symbols, imports). Never raises on a syntax error - reports it."""
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [Symbol("error", f"SyntaxError: {e.msg}", e.lineno or 0, e.lineno or 0)], []

    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or ".")

    symbols: list = []

    def visit(node, prefix: str):
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                qn = f"{prefix}{child.name}"
                symbols.append(Symbol(
                    "class", qn, child.lineno, child.end_lineno or child.lineno,
                    doc=_first_doc_line(child),
                    decorators=[ast.unparse(d) for d in child.decorator_list],
                ))
                visit(child, qn + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                qn = f"{prefix}{child.name}"
                symbols.append(Symbol(
                    kind, qn, child.lineno, child.end_lineno or child.lineno,
                    signature=f"({ast.unparse(child.args)})",
                    doc=_first_doc_line(child),
                    decorators=[ast.unparse(d) for d in child.decorator_list],
                    calls=_calls_in(child),
                ))
                visit(child, qn + ".")

    visit(tree, "")
    return symbols, list(dict.fromkeys(imports))


def render(paths, show_calls: bool = True) -> str:
    """The outline fs_read_outline should have produced."""
    out = []
    for path in paths:
        syms, imports = outline(path)
        nlines = sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
        out.append(f"## {os.path.basename(path)}  ({nlines} lines, "
                   f"{os.path.getsize(path):,} B, {len(syms)} symbols)")
        if imports:
            out.append(f"_imports:_ {', '.join(sorted(imports))}")
        local = {s.qualname.split(".")[-1] for s in syms}
        for s in syms:
            depth = s.qualname.count(".")
            pad = "  " * depth
            head = f"{pad}- `{s.qualname}{s.signature}` **{s.kind}** L{s.lineno}-{s.end_lineno}"
            if s.doc:
                head += f" — {s.doc[:80]}"
            out.append(head)
            if show_calls:
                internal = [c for c in s.calls if c in local and c != s.qualname.split(".")[-1]]
                if internal:
                    out.append(f"{pad}    ↳ calls: {', '.join(internal)}")
        out.append("")
    return "\n".join(out)


# ====================================================================== tests

_FIXTURE = '''
"""Module doc."""
import os
from pathlib import Path

TOP = 1

def helper(a, b=2):
    """Helper one-liner.

    Second paragraph ignored.
    """
    return a + b

class Widget:
    """A widget."""

    @property
    def size(self):
        return helper(1)

    async def refresh(self, *, force=False):
        inner_unreachable = lambda: helper(9)
        return self.size

def orphan():
    pass
'''


def _selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp(prefix="repomap_")
    fx = os.path.join(tmp, "fixture.py")
    open(fx, "w", encoding="utf-8").write(_FIXTURE)

    syms, imports = outline(fx)
    by = {s.qualname: s for s in syms}
    checks = {
        "module functions found": "helper" in by and "orphan" in by,
        "class found": by.get("Widget", None) is not None and by["Widget"].kind == "class",
        "methods are qualified": "Widget.size" in by and "Widget.refresh" in by,
        "async detected": by["Widget.refresh"].kind == "async def",
        "signature captured": by["helper"].signature == "(a, b=2)",
        "kwonly signature captured": "force=False" in by["Widget.refresh"].signature,
        "decorator captured": by["Widget.size"].decorators == ["property"],
        "only first doc line kept": by["helper"].doc == "Helper one-liner.",
        "imports captured": set(imports) == {"os", "pathlib"},
        "call edge found": "helper" in by["Widget.size"].calls,
        "line span sane": by["Widget"].span >= by["Widget.size"].span > 0,
    }

    # syntax errors must degrade, not explode
    bad = os.path.join(tmp, "bad.py")
    open(bad, "w", encoding="utf-8").write("def broken(:\n")
    bsyms, _ = outline(bad)
    checks["syntax error handled"] = bsyms[0].kind == "error"

    rendered = render([fx])
    checks["render mentions symbols"] = "Widget.refresh" in rendered and "L" in rendered

    print("REPOMAP SELF-TEST")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = all(checks.values())
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
