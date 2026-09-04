#!/usr/bin/env python3
"""Reproducible train/dev quality baseline for MNEMOS semantic recall."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
import math
from fractions import Fraction

import recall
import vecidx


CANDIDATE_K = 20
UNICODE_TOKEN_RE = re.compile(r"[^\W_][\w.\-]*", re.UNICODE)
LEXICAL_RESERVES = (20, 16, 12, 10, 8, 4, 0)
TOKENIZER_NAMES = ("current_ascii", "unicode_nfc")
SAFETY_FAMILIES = frozenset({
    "success-vs-failure",
    "permit-vs-prohibit",
    "apply-vs-rollback",
    "online-vs-offline",
    "current-vs-superseded",
    "mutate-vs-inspect",
    "cause-vs-coincidence",
})
IDENTIFIER_LEXEME_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/-])(?:--)?[A-Za-z0-9][A-Za-z0-9_.:/-]*(?![A-Za-z0-9_.:/-])"
)


class BaselineError(ValueError):
    """The baseline input or policy violates a declared invariant."""


class SetupError(BaselineError):
    """A required runtime artifact or environment value is unavailable."""


def unicode_tokens(text: str) -> list[str]:
    """Tokenize NFC text without splitting non-ASCII letters."""
    out = []
    normalized = unicodedata.normalize("NFC", text).casefold()
    for match in UNICODE_TOKEN_RE.finditer(normalized):
        token = match.group(0).strip(".-_")
        if len(token) >= 2 and token not in recall.STOP:
            out.append(token)
    return out


def adapt_notes(
    notes: list[dict], tokenizer: Callable[[str], list[str]]
) -> dict[str, dict]:
    """Adapt corpus notes without leaking IDs or invented metadata into ranking."""
    return {
        note["id"]: {
            "path": note["id"],
            "title": "",
            "text": note["text"],
            "created": None,
            "salience": recall.DEFAULT_SALIENCE,
            "mentions": set(),
            "self_ids": set(),
            "tokens": tokenizer(note["text"]),
        }
        for note in notes
    }


def rank_bm25(
    query_text: str,
    notes: list[dict],
    tokenizer: Callable[[str], list[str]],
) -> list[tuple[str, float]]:
    """Rank positive lexical matches by normalized production BM25 and note ID."""
    docs = adapt_notes(notes, tokenizer)
    query_tokens = tokenizer(query_text)
    if not query_tokens:
        return []
    scores = recall._normalise(recall.bm25(docs, query_tokens))
    return sorted(
        ((note_id, score) for note_id, score in scores.items() if score > 0.0),
        key=lambda item: (-item[1], item[0]),
    )


def _is_identifier(token: str) -> bool:
    return (
        len(token) >= 2
        and (
            any(char.isdigit() for char in token)
            or token.startswith("--")
            or any(char in token for char in "._/:")
        )
    )


def identifier_tokens(text: str) -> tuple[str, ...]:
    """Return sorted, exact identifier-like lexemes from text."""
    normalized = unicodedata.normalize("NFC", text).casefold()
    return tuple(
        sorted(
            {
                match.group(0)
                for match in IDENTIFIER_LEXEME_RE.finditer(normalized)
                if _is_identifier(match.group(0))
            }
        )
    )


def protected_note_ids(
    query_text: str,
    notes: list[dict],
    deterministic: list[tuple[str, float]],
) -> tuple[str, ...]:
    """Return exact-identifier note hits in deterministic order, then note ID."""
    wanted = set(identifier_tokens(query_text))
    matched = {
        note["id"]
        for note in notes
        if wanted.intersection(identifier_tokens(note["text"]))
    }
    positions = {
        note_id: index for index, (note_id, _score) in enumerate(deterministic)
    }
    ordered = tuple(
        sorted(matched, key=lambda note_id: (positions.get(note_id, len(positions)), note_id))
    )
    if len(ordered) > CANDIDATE_K:
        raise BaselineError("identifier_budget_overflow")
    return ordered


def policy_grid() -> tuple[tuple[str, int], ...]:
    return tuple(
        (tokenizer_name, reserve)
        for tokenizer_name in TOKENIZER_NAMES
        for reserve in LEXICAL_RESERVES
    )


def rank_potion(
    query_text: str, note_ids: list[str], matrix
) -> list[tuple[str, float]]:
    """Rank every indexed note by cosine score, including zero/negative scores."""
    if len(note_ids) != len(matrix):
        raise BaselineError("Potion note IDs and matrix rows differ")
    query_vector = vecidx.embed_query(query_text)
    rows = [
        (note_id, float(matrix[index] @ query_vector))
        for index, note_id in enumerate(note_ids)
    ]
    rows.sort(key=lambda item: (-item[1], item[0]))
    return rows


def union_candidates(
    deterministic,
    semantic,
    protected,
    lexical_reserve: int,
    k: int = CANDIDATE_K,
) -> list[str]:
    """Build one protected, deduplicated L/V candidate union."""
    protected = tuple(dict.fromkeys(protected))
    if len(protected) > k:
        raise BaselineError("identifier_budget_overflow")
    if not 0 <= lexical_reserve <= k:
        raise BaselineError(f"invalid lexical reserve: {lexical_reserve}")

    deterministic = list(deterministic)
    deterministic_ids = {note_id for note_id, _score in deterministic}
    out = list(protected)
    seen = set(out)
    lexical_count = sum(note_id in deterministic_ids for note_id in out)

    for note_id, _score in deterministic:
        if len(out) >= k or lexical_count >= lexical_reserve:
            break
        if note_id not in seen:
            out.append(note_id)
            seen.add(note_id)
            lexical_count += 1

    if len(out) < k:
        for note_id, _score in semantic:
            if note_id in seen:
                continue
            out.append(note_id)
            seen.add(note_id)
            if len(out) >= k:
                break

    if len(out) < k:
        for note_id, _score in deterministic:
            if note_id in seen:
                continue
            out.append(note_id)
            seen.add(note_id)
            if len(out) >= k:
                break
    return out


def candidate_gate(report: dict, expected_queries: int | None = None) -> list[str]:
    errors = []
    overall = report["overall"]
    if expected_queries is not None:
        if overall["queries"] != expected_queries:
            errors.append(
                f"query count {overall['queries']} != expected {expected_queries}"
            )
        required = math.ceil(0.99 * expected_queries)
        if overall["hit_at_20"] < required:
            errors.append(
                f"overall Recall@20 {overall['hit_at_20']}/{expected_queries} "
                f"< {required}/{expected_queries}"
            )
    for name in ("safety", "identifier"):
        counts = report[name]
        if counts["hit_at_20"] != counts["queries"]:
            errors.append(
                f"{name} Recall@20 {counts['hit_at_20']}/{counts['queries']} "
                f"!= {counts['queries']}/{counts['queries']}"
            )
    if report["protected_dropped"]:
        errors.append(f"protected hits dropped: {report['protected_dropped']}")
    return errors


def select_policy(policy_reports: list[dict]) -> dict | None:
    eligible = [report for report in policy_reports if not report["gate_errors"]]
    if not eligible:
        return None
    grid_order = {
        f"{tokenizer}-{reserve}-{CANDIDATE_K - reserve}": index
        for index, (tokenizer, reserve) in enumerate(policy_grid())
    }

    def key(report):
        overall = report["overall"]
        recall_at_20 = Fraction(overall["hit_at_20"], overall["queries"] or 1)
        current = report["tokenizer"] == "current_ascii"
        return (
            recall_at_20,
            current,
            report["lexical_reserve"],
            -grid_order[report["policy_id"]],
        )

    return max(eligible, key=key)




def _count_hits(rows: list[dict]) -> dict:
    return {
        "queries": len(rows),
        "hit_at_20": sum(bool(row["hit_at_20"]) for row in rows),
    }


def evaluate_candidate_policy(
    split_data: dict,
    tokenizer_name: str,
    lexical_reserve: int,
    semantic_rankings: dict[str, list[tuple[str, float]]],
) -> dict:
    tokenizers = {"current_ascii": recall.tokens, "unicode_nfc": unicode_tokens}
    try:
        tokenizer = tokenizers[tokenizer_name]
    except KeyError as exc:
        raise BaselineError(f"unknown tokenizer: {tokenizer_name}") from exc

    notes = split_data["notes"]
    rows = []
    protected_dropped = 0
    for query in split_data["queries"]:
        deterministic = rank_bm25(query["text"], notes, tokenizer)
        protected = protected_note_ids(query["text"], notes, deterministic)
        if "identifier-tokens" in query["contrast_families"]:
            if not identifier_tokens(query["text"]):
                raise BaselineError(f"{query['id']}: identifier query has no identifier")
            if not set(query["gold"]).intersection(protected):
                raise BaselineError(f"{query['id']}: no exact identifier gold")
        candidates = union_candidates(
            deterministic,
            semantic_rankings[query["id"]],
            protected,
            lexical_reserve,
        )
        protected_dropped += len(set(protected).difference(candidates))
        rows.append({
            "query_id": query["id"],
            "candidates": candidates,
            "protected": list(protected),
            "hit_at_20": bool(set(query["gold"]).intersection(candidates)),
            "safety": bool(SAFETY_FAMILIES.intersection(query["contrast_families"])),
            "identifier": "identifier-tokens" in query["contrast_families"],
        })

    report = {
        "policy_id": f"{tokenizer_name}-{lexical_reserve}-{CANDIDATE_K - lexical_reserve}",
        "tokenizer": tokenizer_name,
        "lexical_reserve": lexical_reserve,
        "semantic_reserve": CANDIDATE_K - lexical_reserve,
        "overall": _count_hits(rows),
        "safety": _count_hits([row for row in rows if row["safety"]]),
        "identifier": _count_hits([row for row in rows if row["identifier"]]),
        "protected_dropped": protected_dropped,
        "queries": rows,
    }
    report["gate_errors"] = candidate_gate(report)
    return report


def evaluate_all_candidate_policies(
    split_data: dict,
    semantic_rankings: dict[str, list[tuple[str, float]]],
) -> list[dict]:
    return [
        evaluate_candidate_policy(
            split_data, tokenizer_name, lexical_reserve, semantic_rankings
        )
        for tokenizer_name, lexical_reserve in policy_grid()
    ]
