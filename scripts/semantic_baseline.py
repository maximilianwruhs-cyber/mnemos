#!/usr/bin/env python3
"""Reproducible train/dev quality baseline for MNEMOS semantic recall."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

import recall


CANDIDATE_K = 20
UNICODE_TOKEN_RE = re.compile(r"[^\W_][\w.\-]*", re.UNICODE)
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
