"""doclite v2 — a docling-shaped PDF -> Markdown converter for an air-gapped sandbox.

Docling itself is unusable here: not installed, no torch/transformers, and no network
to fetch its layout + TableFormer weights. This reimplements the *useful subset* with
libraries that are actually present:

    pymupdf (PyMuPDF 1.28) -> text spans with font size/flags/bbox, embedded images
    pdfplumber (0.11.9)    -> ruling-based table detection and cell extraction

NOT implemented (docling has ML models for these, we have no weights and no network):
ML layout segmentation, reading-order prediction, formula recognition, OCR.
There is no tesseract binary, so scanned pages cannot be read at all — the converter
says so explicitly instead of returning a plausible blank.

v2 changes, each driven by an observed failure on a real 17-page Google-Docs export:
  1. Table suppression is per-LINE, not per-block — a multi-row text block no longer
     escapes the table bbox and reappears as prose.
  2. Truncated-table detection: lines just below a table that align to its column
     x-positions are absorbed and flagged, instead of being misread as headings.
  3. Consecutive same-level headings on adjacent baselines merge — a wrapped title
     is one heading, not four.
  4. Embedded images are extracted and indexed by page. In Google-Docs exports every
     LaTeX formula is a raster image; those are unreadable without OCR and are
     reported as a count, never silently dropped.
  5. Bullet detection tightened to real glyphs and enumerators, so a mid-sentence
     dash no longer starts a fake list.
  6. Duplicate-text guard: prose identical to a table cell on the same page is dropped.

Usage:
    from doclite import convert
    md, meta = convert("/tmp/report.pdf", with_meta=True, image_dir="/tmp/figs")

Self-test:
    python doclite.py
"""

from __future__ import annotations

import collections
import os
import re
import statistics
from dataclasses import dataclass, field

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf
import pdfplumber

# real bullet glyphs, or an enumerator like "1." / "(2)" / "a)"
BULLET_RE = re.compile(r"^\s*(?:[\u2022\u25cf\u25aa\u25e6\u2023\u2043\u00b7]|\(?\d{1,2}[.)]\s|\(?[a-z][.)]\s)\s*")
NUMERIC_ONLY = re.compile(r"^[\s~%<>=+\-\u2013.,0-9]+$")

# How far below a table bbox a column-aligned line is still treated as a cut-off row.
# DEFAULT 0 = disabled. A/B on a real 17-page export: at 40pt this heuristic caught
# ZERO genuine truncated rows and destroyed FOUR real headings that merely happened to
# start near a column x-position. The actual cause of orphaned rows was tables split
# across a page break, now handled properly in _tables_for(). Raise only if you have a
# document where stitching provably fails, and re-check your heading count.
FRAG_WINDOW = 0.0


@dataclass
class Block:
    kind: str  # heading | para | list | table | figure | tablefrag
    text: str = ""
    level: int = 0
    page: int = 0
    y: float = 0.0
    size: float = 0.0
    rows: list = field(default_factory=list)


# ----------------------------------------------------------------------- helpers


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _body_size(doc) -> float:
    hist: collections.Counter = collections.Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        hist[round(span["size"], 1)] += len(span["text"].strip())
    return hist.most_common(1)[0][0] if hist else 10.0


def _heading_scale(doc, body: float) -> dict:
    sizes = set()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        s = round(span["size"], 1)
                        if s > body * 1.12:
                            sizes.add(s)
    return {s: i + 1 for i, s in enumerate(sorted(sizes, reverse=True)[:4])}


def _inside(bbox, boxes, pad=3.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(
        x0 - pad <= cx <= x1 + pad and top - pad <= cy <= bottom + pad
        for x0, top, x1, bottom in boxes
    )


def _md_table(rows) -> str:
    rows = [[(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    if not any(head):
        head = [f"col{i+1}" for i in range(width)]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _tables_for(pdf_path):
    """page -> list of table dicts. Handles tables that straddle a page break.

    A table continued onto the next page yields a fragment that may be a single row.
    v2.0 discarded those (len(rows) >= 2), which dumped their cells into the prose
    stream as fake headings. We now keep 1-row fragments *only* when they stitch onto
    a table that ran to the bottom of the previous page with matching columns.
    """
    found = collections.defaultdict(list)
    err = None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            raw = {}
            heights = {}
            for pno, page in enumerate(pdf.pages):
                heights[pno] = page.height
                items = []
                for tbl in page.find_tables():
                    rows = tbl.extract()
                    if not rows:
                        continue
                    cols = sorted({round(c[0], 1) for c in tbl.cells}) if tbl.cells else []
                    items.append(
                        {"bbox": tbl.bbox, "rows": rows, "cols": cols, "emit": True, "keep": True}
                    )
                raw[pno] = items

            for pno in sorted(raw):
                for t in raw[pno]:
                    prev = raw.get(pno - 1, [])
                    cont = False
                    if prev:
                        p = prev[-1]
                        same_cols = (
                            len(p["cols"]) == len(t["cols"])
                            and all(abs(a - b) < 3 for a, b in zip(p["cols"], t["cols"]))
                        )
                        if (
                            same_cols
                            and t["bbox"][1] < 0.15 * heights[pno]
                            and p["bbox"][3] > 0.80 * heights[pno - 1]
                        ):
                            cont = True
                            p["rows"].extend(t["rows"])  # stitch onto the parent table
                            t["emit"] = False             # bbox still suppresses text
                    if not cont and len(t["rows"]) < 2:
                        t["keep"] = False                 # lone bordered box, not a table
                for t in raw[pno]:
                    if t["keep"]:
                        found[pno].append(t)
    except Exception as exc:  # noqa: BLE001
        err = repr(exc)
    return found, err


# ---------------------------------------------------------------------- pipeline


def parse(pdf_path: str, image_dir: str | None = None, images_out: dict | None = None):
    doc = pymupdf.open(pdf_path)
    body = _body_size(doc)
    scale = _heading_scale(doc, body)
    tables, terr = _tables_for(pdf_path)

    blocks: list[Block] = []
    chars = 0
    figures = 0
    frags = 0
    if image_dir and images_out is None:
        os.makedirs(image_dir, exist_ok=True)

    for pno, page in enumerate(doc):
        tbls = tables.get(pno, [])
        tboxes = [t["bbox"] for t in tbls]
        cellvals = {_norm(c) for t in tbls for r in t["rows"] for c in r if _norm(c)}

        for t in tbls:
            if t.get("emit", True):
                blocks.append(Block("table", page=pno, y=t["bbox"][1], rows=t["rows"]))

        for blk in page.get_text("dict")["blocks"]:
            # ---- raster image (in Google-Docs exports, usually a rendered formula)
            if blk.get("type") == 1:
                figures += 1
                ref = f"p{pno+1}-img{figures}"
                if blk.get("image") and (image_dir or images_out is not None):
                    fn = f"{ref}.{blk.get('ext', 'png')}"
                    if images_out is not None:
                        # Guarded mode: collect for ONE bundled, policy-checked
                        # emission instead of spraying N loose files. See guard.py.
                        images_out[fn] = blk["image"]
                    else:
                        with open(os.path.join(image_dir, fn), "wb") as fh:
                            fh.write(blk["image"])
                    ref = f"{ref}]({fn}"
                blocks.append(Block("figure", ref, page=pno, y=blk["bbox"][1]))
                continue

            for line in blk.get("lines", []):
                spans = [s for s in line.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue
                lb = line["bbox"]
                if _inside(lb, tboxes):  # per-LINE suppression (v1 bug: per-block)
                    continue

                text = re.sub(r"\s+", " ", "".join(s["text"] for s in spans).strip())
                if _norm(text) in cellvals:  # duplicate of a table cell
                    continue

                # ---- suspected row of a table whose bbox was cut short
                frag = False
                for t in tbls:
                    x0, top, x1, bot = t["bbox"]
                    if bot < lb[1] < bot + FRAG_WINDOW and t["cols"] and any(
                        abs(lb[0] - c) < 6 for c in t["cols"]
                    ):
                        frag = True
                        break

                chars += len(text)
                size = round(statistics.median([s["size"] for s in spans]), 1)
                bold = all(s["flags"] & 2 ** 4 for s in spans)
                y = lb[1]

                if frag:
                    frags += 1
                    blocks.append(Block("tablefrag", text, 0, pno, y, size))
                elif size in scale:
                    blocks.append(Block("heading", text, scale[size], pno, y, size))
                elif BULLET_RE.match(text):
                    blocks.append(Block("list", BULLET_RE.sub("", text), 0, pno, y, size))
                elif bold and len(text) < 90 and not NUMERIC_ONLY.match(text):
                    blocks.append(Block("heading", text, 4, pno, y, size))
                else:
                    blocks.append(Block("para", text, 0, pno, y, size))

    blocks.sort(key=lambda b: (b.page, b.y))
    blocks = _merge_headings(blocks)

    meta = {
        "pages": doc.page_count,
        "body_font_pt": body,
        "heading_sizes": scale,
        "tables": sum(len(v) for v in tables.values()),
        "figures": figures,
        "table_fragments_flagged": frags,
        "text_chars": chars,
        "table_error": terr,
        "likely_scanned": chars < 40 * doc.page_count,
    }
    doc.close()
    return blocks, meta


def _merge_headings(blocks: list[Block]) -> list[Block]:
    """A wrapped title is one heading, not one per line."""
    out: list[Block] = []
    for b in blocks:
        if (
            out
            and b.kind == "heading"
            and out[-1].kind == "heading"
            and out[-1].level == b.level
            and out[-1].page == b.page
            and 0 <= b.y - out[-1].y <= 2.0 * max(b.size, 1.0)
        ):
            out[-1].text = out[-1].text.rstrip() + " " + b.text.lstrip()
            out[-1].y = b.y
        else:
            out.append(b)
    return out


def render(blocks: list[Block]) -> str:
    out: list[str] = []
    buf: list[str] = []
    frag: list[str] = []

    def flush():
        if buf:
            out.append(" ".join(buf))
            buf.clear()

    def flush_frag():
        if frag:
            out.append(
                "> [doclite] Suspected truncated table row — column alignment detected "
                "below a table boundary, raw text preserved:\n> " + " / ".join(frag)
            )
            frag.clear()

    for b in blocks:
        if b.kind == "tablefrag":
            flush()
            frag.append(b.text)
            continue
        flush_frag()
        if b.kind == "para":
            buf.append(b.text)
            continue
        flush()
        if b.kind == "heading":
            out.append("#" * b.level + " " + b.text)
        elif b.kind == "list":
            out.append("- " + b.text)
        elif b.kind == "table":
            out.append(_md_table(b.rows))
        elif b.kind == "figure":
            out.append(f"![{b.text}](#)" if "](" not in b.text else f"![{b.text})")
    flush()
    flush_frag()

    md = "\n\n".join(x for x in out if x.strip())
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def convert(pdf_path: str, with_meta: bool = False, image_dir: str | None = None,
            guard=None, bundle_name: str = "figures.zip"):
    """guard: an optional guard.Guard instance. When supplied, embedded images are
    emitted as a SINGLE policy-checked archive rather than N loose files, and every
    member is recorded in the audit log. image_dir is ignored in guarded mode."""
    images_out: dict | None = {} if guard is not None else None
    blocks, meta = parse(pdf_path, image_dir=image_dir, images_out=images_out)
    if guard is not None and images_out:
        guard.write_bundle(bundle_name, images_out)
        meta["images_bundled_to"] = bundle_name
    md = render(blocks)
    if meta["likely_scanned"]:
        md = (
            "> [doclite] Almost no extractable text — this PDF is probably scanned.\n"
            "> No OCR is available in this sandbox (tesseract binary absent), so the\n"
            "> body could not be recovered.\n\n" + md
        )
    return (md, meta) if with_meta else md


# --------------------------------------------------------------------- self-test

def _fixture(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 22)
    c.drawString(60, h - 70, "Quarterly Platform Review:")      # wrapped title, line 1
    c.drawString(60, h - 96, "Regional Performance")            # wrapped title, line 2
    c.setFont("Helvetica-Bold", 15); c.drawString(60, h - 135, "1. Executive Summary")
    c.setFont("Helvetica", 10)
    for i, ln in enumerate([
        "Throughput improved across all three regions during the quarter, while unit",
        "cost fell for the second consecutive period. Latency remains the open risk.",
    ]):
        c.drawString(60, h - 158 - i * 14, ln)
    c.setFont("Helvetica-Bold", 15); c.drawString(60, h - 205, "2. Key Findings")
    c.setFont("Helvetica", 10)
    for i, ln in enumerate([
        "\u2022 Ingest throughput rose 34 percent quarter over quarter.",
        "\u2022 Unit cost fell to 0.42 EUR per thousand documents.",
        "\u2022 p99 latency regressed in the EMEA region.",
    ]):
        c.drawString(70, h - 228 - i * 15, ln)
    c.drawString(60, h - 285, "Costs are tracked per region - see the table below for detail.")

    c.setFont("Helvetica-Bold", 15); c.drawString(60, h - 320, "3. Regional Detail")
    data = [["Region", "Docs (M)", "Cost EUR", "p99 ms"],
            ["EMEA", "12.4", "5210", "880"],
            ["AMER", "9.1", "3980", "410"],
            ["APAC", "6.7", "2640", "455"]]
    x0, y0, cw, rh = 60, h - 360, 110, 20
    c.setFont("Helvetica", 10)
    for r, row in enumerate(data):
        for col, val in enumerate(row):
            c.rect(x0 + col * cw, y0 - r * rh, cw, rh)
            c.drawString(x0 + col * cw + 5, y0 - r * rh + 6, val)
    c.showPage()
    c.setFont("Helvetica-Bold", 15); c.drawString(60, h - 70, "4. Recommendation")
    c.setFont("Helvetica", 10)
    c.drawString(60, h - 95, "Fund the EMEA cache tier in the next planning cycle.")
    c.save()


if __name__ == "__main__":
    fx = "/tmp/_doclite_fixture.pdf"
    _fixture(fx)
    md, meta = convert(fx, with_meta=True)
    print("META:", meta)
    print("-" * 68); print(md); print("-" * 68)

    checks = {
        "wrapped title merged to ONE h1":
            md.count("# Quarterly Platform Review: Regional Performance") == 1
            and sum(1 for l in md.splitlines() if re.match(r"^# ", l)) == 1,
        "section headings": md.count("## ") >= 3,
        "bullets kept": md.count("\n- ") >= 2,
        # the sentence legitimately contains " - see the table"; what must not happen
        # is a LINE beginning with it, i.e. the dash promoted to a list item
        "no fake bullet from mid-sentence dash": not any(
            l.startswith("- ") and "see the table" in l for l in md.splitlines()
        ),
        "table rendered": "| Region |" in md and "| --- |" in md,
        "table rows": "| EMEA |" in md and "| APAC |" in md,
        "cells not duplicated as prose": all(
            md.count(v) == 1 and all(v not in ln for ln in md.splitlines() if not ln.startswith("|"))
            for v in ("12.4", "5210", "2640", "455")
        ),
        "page 2 present": "Recommendation" in md,
        "wrapped lines merged": "unit cost fell for the second" in md.lower(),
        "no spurious frag warning": "truncated table row" not in md,
        "no scan warning": "probably scanned" not in md,
    }
    print("SELF-TEST")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("RESULT:", "ALL PASS" if all(checks.values()) else "FAILURES PRESENT")
    os.remove(fx)
    # Audit finding 2026-08-24: these checks previously printed FAIL but still
    # exited 0, making them diagnostics rather than an enforcing test suite.
    raise SystemExit(0 if all(checks.values()) else 1)
