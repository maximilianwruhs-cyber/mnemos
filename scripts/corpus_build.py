#!/usr/bin/env python3
"""corpus_build.py - deterministically expand authored scenario seeds into a
canonical MNEMOS certification corpus (corpus-v2).

Human-authored, bilingual, reviewable scenario seeds (one intent/incident per
group, with gold + hard-negative notes and query paraphrases in en and de) are
assembled into the six canonical split files. The builder invents nothing: it
only wires references, assigns deterministic IDs, and emits canonical bytes.

Determinism: identical seeds -> byte-identical split files -> identical
`corpus_hash`. No timestamps, no randomness, no network. All validation is
delegated to `corpus_lint.py` (the authoritative contract); this script is only
a generator and can be re-run at any time to reproduce the corpus.

Seeds (`--seeds`): a pretty JSON array (`.json`) or JSONL (`.jsonl`). Each
scenario group:

    {
      "group": "permit-badge-restore",      # unique stem; one split only
      "split": "certification",             # train | dev | certification
      "families": ["permit-vs-prohibit"],   # >=1 contrast family for the queries
      "provenance_gold": "synthetic-contrast",
      "gold": {"en": "...", "de": "..."},
      "hard": [{"family": "permit-vs-prohibit", "en": "...", "de": "..."}],
      "queries": {"en": ["...", "..."], "de": ["...", "..."]},
      "rationale": "a reviewer-disputable justification"
    }

Usage:
    python scripts/corpus_build.py [--seeds PATH] [--out PATH]
                                   [--coverage] [--lint] [--no-floors]
                                   [--emit-manifest] [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import corpus_lint  # noqa: E402  reuse SPLITS, CONTRAST_FAMILIES, canonical_bytes, main

LANGS = ("en", "de")
EASY_PER_QUERY = 2  # deterministic easy negatives drawn from other same-split groups

DEFAULT_SEEDS = HERE / "fixtures/vector-semantics/corpus-v2-seeds/scenarios.json"
DEFAULT_OUT = HERE / "fixtures/vector-semantics/corpus-v2"


def _fam_order(families) -> list:
    """De-duplicate contrast families into stable CONTRACT order."""
    rank = {f: i for i, f in enumerate(corpus_lint.CONTRAST_FAMILIES)}
    return sorted(set(families), key=lambda f: rank.get(f, len(rank)))


def load_seeds(path) -> list:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        seeds = json.loads(text)
        if not isinstance(seeds, list):
            raise SystemExit(f"{path}: expected a JSON array of scenario groups")
        return seeds
    seeds = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            seeds.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}")
    return seeds


def build(seeds) -> dict:
    """Expand scenario seeds into {'notes'|'queries': {split: [records]}}."""
    by_split = {s: [] for s in corpus_lint.SPLITS}
    seen = set()
    for seed in seeds:
        group = seed["group"]
        if group in seen:
            raise SystemExit(f"duplicate scenario group: {group}")
        seen.add(group)
        split = seed["split"]
        if split not in corpus_lint.SPLITS:
            raise SystemExit(f"group {group}: bad split {split!r}")
        by_split[split].append(seed)
    for split in corpus_lint.SPLITS:
        by_split[split].sort(key=lambda s: s["group"])

    data = {
        "notes": {s: [] for s in corpus_lint.SPLITS},
        "queries": {s: [] for s in corpus_lint.SPLITS},
    }

    # Notes: gold (en/de) + each hard negative (en/de) per group.
    for split in corpus_lint.SPLITS:
        for seed in by_split[split]:
            group = seed["group"]
            prov = seed.get("provenance_gold", "synthetic-contrast")
            for lang in LANGS:
                data["notes"][split].append({
                    "id": f"n-{group}-gold-{lang}",
                    "text": seed["gold"][lang],
                    "language": lang,
                    "scenario_group": group,
                    "provenance": prov,
                })
            for hi, hard in enumerate(seed["hard"]):
                for lang in LANGS:
                    data["notes"][split].append({
                        "id": f"n-{group}-hard{hi}-{lang}",
                        "text": hard[lang],
                        "language": lang,
                        "scenario_group": group,
                        "provenance": "synthetic-contrast",
                    })

    # Queries: every paraphrase, wired to gold, hard negatives, and easy negatives.
    for split in corpus_lint.SPLITS:
        groups = by_split[split]
        n = len(groups)
        for gi, seed in enumerate(groups):
            group = seed["group"]
            prov = seed.get("provenance_gold", "synthetic-contrast")
            fams = _fam_order(list(seed.get("families", []))
                              + [h["family"] for h in seed["hard"]])
            gold_ids = [f"n-{group}-gold-en", f"n-{group}-gold-de"]
            hard_negs = [
                {"note_id": f"n-{group}-hard{hi}-{lang}", "contrast_family": hard["family"]}
                for hi, hard in enumerate(seed["hard"]) for lang in LANGS
            ]
            easy = []
            step = 1
            while len(easy) < EASY_PER_QUERY and step < n:
                other = groups[(gi + step) % n]
                easy.append(f"n-{other['group']}-gold-en")
                step += 1
            for lang in LANGS:
                for j, text in enumerate(seed["queries"][lang]):
                    data["queries"][split].append({
                        "id": f"q-{group}-{lang}-{j}",
                        "text": text,
                        "language": lang,
                        "scenario_group": group,
                        "provenance": prov if j == 0 else "paraphrase-augmentation",
                        "contrast_families": fams,
                        "gold": gold_ids,
                        "hard_negatives": hard_negs,
                        "easy_negatives": easy,
                        "rationale": seed.get("rationale", ""),
                    })
    return data


def write_corpus(out, data) -> None:
    out = Path(out)
    for split in corpus_lint.SPLITS:
        split_dir = out / split
        split_dir.mkdir(parents=True, exist_ok=True)
        for kind in ("notes", "queries"):
            (split_dir / f"{kind}.jsonl").write_bytes(
                corpus_lint.canonical_bytes(data[kind][split]))


def coverage(data) -> str:
    lines = []
    for split in corpus_lint.SPLITS:
        lines.append(f"{split}: {len(data['queries'][split])} queries, "
                     f"{len(data['notes'][split])} notes")
    cert = data["queries"]["certification"]
    total = len(cert) or 1
    lang = {"en": 0, "de": 0}
    fam = {f: {"en": 0, "de": 0} for f in corpus_lint.CONTRAST_FAMILIES}
    for q in cert:
        if q["language"] in lang:
            lang[q["language"]] += 1
        for f in q["contrast_families"]:
            if f in fam and q["language"] in fam[f]:
                fam[f][q["language"]] += 1
    lines.append(f"cert language: en {lang['en']} ({lang['en'] / total:.0%}), "
                 f"de {lang['de']} ({lang['de'] / total:.0%})")
    for f in corpus_lint.CONTRAST_FAMILIES:
        c = fam[f]["en"] + fam[f]["de"]
        flag = "" if c >= corpus_lint.FAMILY_CERT_FLOOR else "  <15"
        lines.append(f"  {f}: {c} (en {fam[f]['en']}, de {fam[f]['de']}){flag}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build canonical corpus-v2 from scenario seeds.")
    ap.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--coverage", action="store_true", help="print split/family/language coverage")
    ap.add_argument("--lint", action="store_true", help="run corpus_lint after building")
    ap.add_argument("--no-floors", action="store_true", help="lint without coverage floors")
    ap.add_argument("--emit-manifest", action="store_true", help="lint and write manifest.json")
    ap.add_argument("--check", action="store_true", help="lint and require manifest to match")
    args = ap.parse_args(argv)

    data = build(load_seeds(args.seeds))
    write_corpus(args.out, data)
    print(f"corpus build: wrote {args.out}")
    if args.coverage:
        print(coverage(data))

    if args.lint or args.no_floors or args.emit_manifest or args.check:
        lint_argv = [str(args.out)]
        if args.no_floors:
            lint_argv.append("--no-floors")
        if args.emit_manifest:
            lint_argv.append("--emit-manifest")
        if args.check:
            lint_argv.append("--check")
        return corpus_lint.main(lint_argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
