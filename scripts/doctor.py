"""doctor - one call that answers "what can this environment do, and is my toolbelt still green?"

WHY THIS EXISTS. Across one session the documented library list was wrong five times
(cv2, pymupdf, markdown_it, networkx, manim), and three toolbelt scripts each had a
self-test reachable only by a separate call. Belief was cheaper than measurement, so
belief drifted. This replaces the written inventory with an executable one.

SCOPE HONESTY. doctor sees the *sandbox*. It cannot exercise the agent-layer fs tools,
so their known defects (fs_read_file returning no text, fs_read_outline calling .py
"binary", fs_grep failing on file paths, fs_glob reporting sizeBytes 0) live as
directives in MEMORY.md and are NOT checked here. Do not read a green doctor as
"everything works".

USAGE
    python doctor.py            # full report, exit 0 only if all checks pass
Stage alongside: doclite.py, guard.py, repomap.py, mnemos.py, test_mnemos.py,
MEMORY.md, AGENTS.md - whatever is present gets checked, the rest is skipped.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True

# (module, expected) - expected=True means the tool docs claim it exists
LIBS = [
    ("pandas", True), ("numpy", True), ("scipy", True), ("duckdb", True),
    ("sklearn", True), ("statsmodels", True), ("sympy", True),
    ("matplotlib", True), ("seaborn", True), ("plotly", True),
    ("openpyxl", True), ("xlsxwriter", True), ("pptx", True), ("docx", True),
    ("pdfplumber", True), ("PyPDF2", True), ("reportlab", True), ("markitdown", True),
    ("PIL", True), ("pytesseract", True),
    # undocumented but present - each found by accident, each worth keeping visible
    ("pymupdf", False), ("markdown_it", False), ("jinja2", False),
    ("networkx", False), ("requests", False),
    # documented but broken / heavy
    ("cv2", True), ("manim", True), ("moviepy", True),
]

HEAVY = {"manim", "moviepy"}          # presence only - importing manim costs ~12s cold

BINARIES = ["ffmpeg", "tesseract", "latex", "dvisvgm", "git", "node", "java",
            "docker", "curl", "nvidia-smi"]

SELFTESTS = ["guard.py", "repomap.py", "doclite.py", "test_mnemos.py"]

CAPS = {"MEMORY.md": (200, 12 * 1024), "AGENTS.md": (120, None)}


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check_env():
    section("1. ENVIRONMENT")
    print(f"  python      {sys.version.split()[0]}")
    print(f"  runtime     {os.environ.get('AWS_EXECUTION_ENV', 'unknown')}")
    print(f"  function    {os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'n/a')}")
    try:
        cpus = sum(1 for l in open("/proc/cpuinfo") if l.startswith("processor"))
        mem = int(open("/proc/meminfo").readline().split()[1]) // 1024
        print(f"  cpus={cpus}  mem={mem} MB")
    except Exception:
        print("  /proc unavailable")


def check_libs():
    section("2. LIBRARIES  (surprise = docs and reality disagree)")
    surprises = []
    presence_only = os.environ.get("DOCTOR_PRESENCE_ONLY") == "1"
    for name, documented in LIBS:
        if presence_only or name in HEAVY:
            ok = importlib.util.find_spec(name) is not None
            status, detail = ("OK" if ok else "FAIL"), "(presence only, not imported)"
        else:
            try:
                m = importlib.import_module(name)
                status, detail = "OK", str(getattr(m, "__version__", ""))
            except Exception as e:
                status, detail = "FAIL", f"{type(e).__name__}: {str(e).splitlines()[0][:52]}"
        present = status == "OK"
        flag = ""
        if present and not documented:
            flag, _ = "  <- UNDOCUMENTED", surprises.append(f"{name} present but undocumented")
        elif not present and documented:
            flag, _ = "  <- DOCUMENTED BUT BROKEN", surprises.append(f"{name} documented but {detail}")
        print(f"  [{status:<4}] {name:<14} {detail[:56]}{flag}")
    return surprises


def check_binaries():
    section("3. BINARIES")
    for b in BINARIES:
        print(f"  {'OK  ' if shutil.which(b) else 'MISS'}  {b:<12} {shutil.which(b) or ''}")


def check_selftests(root="/tmp"):
    section("4. TOOLBELT SELF-TESTS  (non-zero exit = real failure)")
    results = {}
    for script in SELFTESTS:
        path = os.path.join(root, script)
        if not os.path.exists(path):
            print(f"  [SKIP] {script:<16} not staged")
            continue
        t = time.time()
        try:
            r = subprocess.run([sys.executable, path], capture_output=True,
                               text=True, timeout=120)
            tail = [l for l in r.stdout.splitlines() if l.startswith("RESULT")]
            verdict = tail[-1] if tail else f"(no RESULT line, exit {r.returncode})"
            results[script] = r.returncode
            print(f"  [{'PASS' if r.returncode == 0 else 'FAIL'}] {script:<16} "
                  f"exit={r.returncode}  {time.time() - t:4.1f}s  {verdict}")
            if r.returncode != 0:
                print(f"         stderr: {r.stderr.strip().splitlines()[-1][:70]}"
                      if r.stderr.strip() else "         (no stderr)")
        except subprocess.TimeoutExpired:
            results[script] = 124
            print(f"  [FAIL] {script:<16} TIMEOUT after 120s")
    return results


def check_caps(root="/tmp"):
    section("5. SUBSTRATE CAPS")
    breaches = []
    for name, (maxlines, maxbytes) in CAPS.items():
        path = os.path.join(root, name)
        if not os.path.exists(path):
            print(f"  [SKIP] {name} not staged")
            continue
        data = open(path, encoding="utf-8").read()
        lines, nbytes = len(data.splitlines()), len(data.encode())
        lpct = 100 * lines / maxlines
        bad = lines > maxlines or (maxbytes and nbytes > maxbytes)
        warn = lpct > 85 or (maxbytes and nbytes > 0.85 * maxbytes)
        tag = "OVER" if bad else ("WARN" if warn else "OK  ")
        cap_b = f" / {maxbytes:,} B" if maxbytes else ""
        print(f"  [{tag}] {name:<12} {lines:>4}/{maxlines} lines ({lpct:.0f}%)  "
              f"{nbytes:,}{cap_b}")
        if bad:
            breaches.append(name)
    return breaches


def main() -> int:
    check_env()
    surprises = check_libs()
    check_binaries()
    results = check_selftests()
    breaches = check_caps()

    section("VERDICT")
    failed = [k for k, v in results.items() if v != 0]
    for s in surprises:
        print(f"  ! doc/reality mismatch: {s}")
    print(f"  self-tests: {len(results) - len(failed)}/{len(results)} passing"
          + (f"  FAILED: {', '.join(failed)}" if failed else ""))
    print(f"  cap breaches: {breaches or 'none'}")
    ok = not failed and not breaches
    print(f"\n  DOCTOR: {'GREEN' if ok else 'RED'}")
    print("  (agent-layer fs tool defects are NOT covered - see MEMORY.md)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
