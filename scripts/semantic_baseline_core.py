#!/usr/bin/env python3
"""Pure retrieval, policy, metric, and artifact primitives for baseline-v1."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import tempfile
import unicodedata
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

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


def score_query(
    query: dict,
    ranking: list[str],
    protected: set[str],
    notes_by_id: dict[str, dict],
) -> dict:
    if len(ranking) != len(set(ranking)):
        raise BaselineError(f"{query['id']}: duplicate note in ranking")
    positions = {note_id: index for index, note_id in enumerate(ranking, 1)}
    gold = tuple(query["gold"])
    gold_ranks = {note_id: positions.get(note_id) for note_id in gold}
    present_ranks = [rank for rank in gold_ranks.values() if rank is not None]
    first_gold_rank = min(present_ranks) if present_ranks else None
    hard_ids = [item["note_id"] for item in query["hard_negatives"]]
    hard_ranks = {note_id: positions.get(note_id) for note_id in hard_ids}

    result = {
        "query_id": query["id"],
        "scenario_group": query["scenario_group"],
        "language": query["language"],
        "contrast_families": list(query["contrast_families"]),
        "provenance": query["provenance"],
        "ranking": list(ranking),
        "ranking_size": len(ranking),
        "protected": sorted(protected),
        "gold_ranks": gold_ranks,
        "hard_negative_ranks": hard_ranks,
        "first_gold_rank": first_gold_rank,
        "reciprocal_rank": 1.0 / first_gold_rank if first_gold_rank else 0.0,
        "zero_candidates": not ranking,
        "top1_hard_negative": (
            ranking[0] if ranking and ranking[0] in set(hard_ids) else None
        ),
        "eligible": (
            len([note_id for note_id in ranking if note_id not in protected]) >= 2
            and not set(gold).intersection(protected)
        ),
    }
    for k in (1, 3, 20):
        top = set(ranking[:k])
        result[f"hit_at_{k}"] = bool(top.intersection(gold))
        same = {
            note_id for note_id in gold
            if notes_by_id[note_id]["language"] == query["language"]
        }
        cross = set(gold).difference(same)
        result[f"same_language_gold_at_{k}"] = bool(top.intersection(same))
        result[f"cross_language_gold_at_{k}"] = bool(top.intersection(cross))
        result[f"both_golds_at_{k}"] = bool(gold) and set(gold).issubset(top)
    return result


def score_rerank(
    query: dict,
    ranked_scores: list[tuple[str, float | None]],
    protected: set[str],
    notes_by_id: dict[str, dict],
) -> dict:
    ranking = [note_id for note_id, _score in ranked_scores]
    result = score_query(query, ranking, protected, notes_by_id)
    result["ranked_scores"] = [
        {"note_id": note_id, "score": score} for note_id, score in ranked_scores
    ]
    scored = [(note_id, score) for note_id, score in ranked_scores if score is not None]
    result["semantic_top1_top2_margin"] = (
        scored[0][1] - scored[1][1] if len(scored) >= 2 else None
    )
    hard_ids = {item["note_id"] for item in query["hard_negatives"]}
    hard_scores = [score for note_id, score in scored if note_id in hard_ids]
    result["semantic_top1_best_hard_margin"] = (
        scored[0][1] - max(hard_scores) if scored and hard_scores else None
    )
    return result


def _summary(rows: list[dict]) -> dict:
    count = len(rows)
    return {
        "queries": count,
        "hit_at_1": sum(row["hit_at_1"] for row in rows),
        "hit_at_3": sum(row["hit_at_3"] for row in rows),
        "hit_at_20": sum(row["hit_at_20"] for row in rows),
        "mean_reciprocal_rank": (
            sum(row["reciprocal_rank"] for row in rows) / count if count else 0.0
        ),
        "zero_candidates": sum(row["zero_candidates"] for row in rows),
        "same_language_gold_at_20": sum(
            row["same_language_gold_at_20"] for row in rows
        ),
        "cross_language_gold_at_20": sum(
            row["cross_language_gold_at_20"] for row in rows
        ),
        "both_golds_at_20": sum(row["both_golds_at_20"] for row in rows),
        "top1_hard_negative": sum(
            row["top1_hard_negative"] is not None for row in rows
        ),
        "eligible": sum(row["eligible"] for row in rows),
    }


def aggregate_results(queries: list[dict], results: dict[str, dict]) -> dict:
    expected = [query["id"] for query in queries]
    if len(expected) != len(set(expected)) or set(expected) != set(results):
        raise SetupError("query results do not exactly cover the input query IDs")

    def rows_for(predicate):
        return [results[query["id"]] for query in queries if predicate(query)]

    languages = sorted({query["language"] for query in queries})
    families = sorted({family for query in queries for family in query["contrast_families"]})
    provenances = sorted({query["provenance"] for query in queries})
    return {
        "overall": _summary(rows_for(lambda _query: True)),
        "languages": {
            language: _summary(rows_for(lambda query, lang=language: query["language"] == lang))
            for language in languages
        },
        "families": {
            family: _summary(rows_for(
                lambda query, fam=family: fam in query["contrast_families"]
            ))
            for family in families
        },
        "provenance": {
            provenance: _summary(rows_for(
                lambda query, prov=provenance: query["provenance"] == prov
            ))
            for provenance in provenances
        },
    }


def compare_results(base: dict[str, dict], reranked: dict[str, dict]) -> dict:
    if set(base) != set(reranked):
        raise BaselineError("paired query IDs differ")
    corrections = sorted(
        query_id for query_id in base
        if not base[query_id]["hit_at_1"]
        and reranked[query_id]["hit_at_20"]
        and reranked[query_id]["hit_at_1"]
    )
    regressions = sorted(
        query_id for query_id in base
        if base[query_id]["hit_at_1"] and not reranked[query_id]["hit_at_1"]
    )
    return {
        "corrections": len(corrections),
        "correction_query_ids": corrections,
        "regressions": len(regressions),
        "regression_query_ids": regressions,
    }


def _group_rows(rows: list[dict]) -> dict[str, list[dict]]:
    groups = {}
    for row in rows:
        groups.setdefault(row["scenario_group"], []).append(row)
    return groups


def _percentile_interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    lower = math.floor(0.025 * (len(ordered) - 1))
    upper = math.ceil(0.975 * (len(ordered) - 1))
    return ordered[lower], ordered[upper]


def cluster_interval(
    rows: list[dict], metric: str, seed: int, samples: int
) -> dict:
    if not rows or samples < 1:
        raise BaselineError("cluster interval requires rows and positive samples")
    groups = _group_rows(rows)
    group_ids = sorted(groups)
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        sampled_groups = rng.choices(group_ids, k=len(group_ids))
        values = [
            row[metric]
            for group_id in sampled_groups
            for row in groups[group_id]
        ]
        estimates.append(sum(values) / len(values))
    lower, upper = _percentile_interval(estimates)
    return {
        "observed": sum(row[metric] for row in rows) / len(rows),
        "lower": lower,
        "upper": upper,
        "groups": len(groups),
        "samples": samples,
        "seed": seed,
    }



def paired_cluster_interval(
    left: list[dict],
    right: list[dict],
    metric: str,
    seed: int,
    samples: int,
) -> dict:
    left_by_id = {row["query_id"]: row for row in left}
    right_by_id = {row["query_id"]: row for row in right}
    if set(left_by_id) != set(right_by_id):
        raise BaselineError("paired query IDs differ")
    deltas = []
    for query_id in sorted(left_by_id):
        left_row, right_row = left_by_id[query_id], right_by_id[query_id]
        if left_row["scenario_group"] != right_row["scenario_group"]:
            raise BaselineError(f"{query_id}: paired scenario groups differ")
        deltas.append({
            "query_id": query_id,
            "scenario_group": left_row["scenario_group"],
            metric: right_row[metric] - left_row[metric],
        })
    return cluster_interval(deltas, metric, seed, samples)


def canonical_json_bytes(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_json(path: Path, value) -> str:
    payload = canonical_json_bytes(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(payload).hexdigest()


