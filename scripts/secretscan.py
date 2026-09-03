#!/usr/bin/env python3
"""High-confidence secret detection for the MNEMOS audit gate.

The scanner is deterministic and side-effect free. It reports masked matches;
it never mutates or returns the original credential value.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretMatch:
    """A detected credential with a log-safe preview."""

    kind: str
    preview: str
    fingerprint: bytes
    start: int
    end: int


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(
        r"-----BEGIN (?:(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----")),
    ("AWS access key", re.compile(
        r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b")),
    ("GitHub PAT", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{22,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
    ("Stripe webhook secret", re.compile(r"\bwhsec_[0-9A-Za-z]{16,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[0-9A-Za-z_-]{20,}\b")),
    ("OpenAI key", re.compile(
        r"\bsk-(?:proj-[0-9A-Za-z_-]{20,}|(?!ant-|proj-)[0-9A-Za-z]{20,})\b")),
    ("HuggingFace token", re.compile(r"\bhf_[0-9A-Za-z]{34,}\b")),
    ("npm token", re.compile(r"\bnpm_[0-9A-Za-z]{36}\b")),
    ("PyPI token", re.compile(
        r"\bpypi-AgEIcHlwaS5vcmcBA[0-9A-Za-z_-]{50,}\b")),
    ("SendGrid key", re.compile(
        r"\bSG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43}\b")),
    ("Twilio API key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("Square token", re.compile(r"\bsq0(?:atp|csp)-[0-9A-Za-z_-]{20,}\b")),
    ("Shopify token", re.compile(r"\bshp(?:at|ca|pa|ss)_[0-9a-fA-F]{32}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[0-9A-Za-z_-]{35}\b")),
    ("DigitalOcean token", re.compile(r"\bdop_v1_[0-9a-fA-F]{64}\b")),
    ("Databricks token", re.compile(r"\bdapi[0-9A-Za-z]{32}\b")),
    ("New Relic key", re.compile(r"\bNRAK-[0-9A-Z]{27}\b")),
    ("Pulumi token", re.compile(r"\bpul-[0-9A-Za-z_-]{40,}\b")),
    ("Sentry token", re.compile(r"\bsntrys_[0-9A-Za-z_-]{20,}\b")),
    ("Linear key", re.compile(r"\blin_api_[0-9A-Za-z]{40}\b")),
    ("Postman key", re.compile(
        r"\bPMAK-[0-9a-fA-F]{24}-[0-9a-fA-F]{34}\b")),
    ("Vault token", re.compile(r"\bhv[bsr]\.[0-9A-Za-z_-]{24,}\b")),
    ("age secret key", re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{20,}\b")),
    ("credentials in URL", re.compile(
        r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s:@/]+@")),
    ("JWT", re.compile(
        r"\beyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\b")),
)


def _mask(value: str) -> str:
    if value.startswith("-----BEGIN"):
        return "-----BEGIN ... PRIVATE KEY-----"
    return value[:6] + "..."


def scan(text: str) -> list[SecretMatch]:
    """Return high-confidence secret occurrences found in ``text``."""
    matches: list[SecretMatch] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            matches.append(SecretMatch(
                kind=kind,
                preview=_mask(value),
                fingerprint=hashlib.sha256(value.encode("utf-8")).digest(),
                start=match.start(),
                end=match.end(),
            ))
    return matches
