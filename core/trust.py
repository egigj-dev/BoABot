"""Deterministic guardrails between user input, retrieval, and generation."""
from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .text_norm import fold

MIN_RELEVANCE_SCORE = 0.50
PRICE_INTENT = ("sa esht", "sa eshte", "sa kushton", "sa paguaj", "cfare tarife",
                "sa me kushton")

NO_EVIDENCE_MESSAGE = (
    "Nuk gjeta burim mjaftueshëm të lidhur për t’iu përgjigjur me "
    "besueshmëri. Nuk do të hamendësoj një përgjigje."
)
UNSAFE_INPUT_MESSAGE = (
    "Ju lutem dërgoni një pyetje të qartë, me tekst të zakonshëm, për "
    "rregulloret bankare ose tarifat. Nuk mund të përpunoj udhëzime për të "
    "anashkaluar sjelljen e asistentit ose tekst të koduar."
)

@dataclass(frozen=True)
class GateResult:
    allowed: bool
    message: str = ""
    reason: str = ""
    accepted_hits: tuple[dict[str, Any], ...] = ()
    dropped_hits: int = 0

_BANK_NAME_STOPLIST = frozenset({"sh.a"})
_BANK_NAME_STOP_PREFIXES = ("shqiper", "bank", "alban")
_NON_INSTITUTION_RATE_LABELS = frozenset({"biznes i vogel", "kredi per shtepi/prona"})
_RATE_LABEL_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*[-+]?\d")

@lru_cache(maxsize=1)
def bank_names() -> tuple[str, ...]:
    """Load commercial-bank identity tokens from the rate corpus.

    The Bank of Albania is the regulator, not a commercial provider of quotable
    tariffs; geographic qualifiers are not institution identities.
    """
    names = set()
    path = Path(__file__).resolve().parents[1] / "rate_tables.jsonl"
    with path.open(encoding="utf-8") as rate_file:
        for line in rate_file:
            text = json.loads(line)["text"]
            for data_line in text.splitlines():
                match = _RATE_LABEL_RE.match(data_line)
                if not match:
                    continue
                source_label = match.group(1).strip()
                label = fold(source_label)
                if ("banka e shqiperis" in label
                        or label in _NON_INSTITUTION_RATE_LABELS):
                    continue
                for source_token in source_label.split():
                    token = fold(source_token)
                    if ((len(token) >= 4
                            or len(token) >= 3 and source_token.isupper())
                            and token not in _BANK_NAME_STOPLIST
                            and not token.startswith(_BANK_NAME_STOP_PREFIXES)):
                        names.add(token)
    return tuple(sorted(names))


# Provenance for zero-retrieval institution facts (e.g. the bank-catalog list
# answer "Cilat jane bankat ne shqiperi?"). Identity-only: the register declares
# WHICH institutions are licensed; it is never product-availability evidence.
INSTITUTION_REGISTER_SOURCE: dict[str, object] = {
    "id": "boa-licensed-institutions",
    "doc": "Banka e Shqipërisë — Subjektet e licencuara",
    "title": "Regjistri i subjekteve të licencuara — Banka e Shqipërisë",
    "url": "https://www.bankofalbania.org/Mbikeqyrja/Subjekte_te_licencuara/",
    "as_of": None,  # no fabricated date; BoA updates the register continuously
}


@lru_cache(maxsize=1)
def _bank_name_re() -> re.Pattern[str]:
    alternatives = "|".join(re.escape(name) for name in bank_names())
    return re.compile(rf"\b(?:{alternatives})\b") if alternatives else re.compile(r"(?!)")

def _looks_like_base64(text: str) -> bool:
    candidate = text.strip()
    if len(candidate) < 16:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate):
        return False
    if len(candidate) % 4 == 1:
        return False
    try:
        decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    if not decoded:
        return False
    readable = sum(chr(byte).isalpha() or chr(byte).isspace() for byte in decoded)
    return readable / len(decoded) >= 0.85

def _looks_like_instruction_override(text: str) -> bool:
    folded = fold(text)
    patterns = (
        r"ignore\s+(all\s+)?(previous|prior|system)\s+(instructions?|prompts?)",
        r"disregard\s+(all\s+)?(previous|prior|system)",
        r"system\s+prompt",
        r"jailbreak|developer\s+message|reveal\s+(your|the)\s+(prompt|instructions)",
        r"injoro\s+(te\s+gjitha\s+)?(udhezimet|instruksionet|rregullat)",
        r"shfaq\s+(promptin|udhezimet|instruksionet)\s+(e\s+)?(sistemit|brendshme)",
        r"anashkalo\s+(rregullat|udhezimet|kufizimet)",
    )
    return any(re.search(pattern, folded) for pattern in patterns)

def input_gate(text: str) -> GateResult:
    """Reject control characters, encoded text, and prompt-injection attempts."""
    normalized = unicodedata.normalize("NFKC", text)
    if any(unicodedata.category(ch).startswith("C") and ch not in "\n\t" for ch in normalized):
        return GateResult(False, UNSAFE_INPUT_MESSAGE, "control_characters")
    decoded = unquote(normalized)
    if decoded != normalized and normalized.count("%") >= 3:
        return GateResult(False, UNSAFE_INPUT_MESSAGE, "encoded_text")
    if _looks_like_base64(normalized) or _looks_like_instruction_override(normalized) or _looks_like_instruction_override(decoded):
        return GateResult(False, UNSAFE_INPUT_MESSAGE, "encoded_or_instruction_override")
    return GateResult(True)

def issuer_of(hit_id: str, text: str = "") -> str:
    """Return the issuing institution for one retrieved chunk (Step 8).

    ``rate_*`` ids are commercial-bank fee tables: the issuer is the bank named
    in the table row(s). Anything else is a Bank-of-Albania regulation. The
    mapping is in-code (no DB migration) and mandatory: a commercial fee must
    never be presented as the Bank of Albania's own rate.
    """
    if str(hit_id).startswith("rate_"):
        if not isinstance(text, str) or not text:
            return "tabela e tarifave te bankave"
        folded_text = fold(text)
        matches = [name for name in bank_names() if name in folded_text]
        if len(matches) == 1:
            return matches[0]
        return "bankat e tarifat komerciale" if matches else "tabelat e tarifave"
    return "Banka e Shqipërisë"


def trusted_hits(query: str, hits: list[dict[str, Any]]) -> GateResult:
    """Accept only retrieval results strong enough to be used as evidence.

    The rate-family gate stops a regulation passage being quoted as a commercial
    tariff. That risk only exists when the caller asks for a specific institution's
    price.
    """
    if not hits:
        return GateResult(False, NO_EVIDENCE_MESSAGE, "no_hits")
    accepted: list[dict[str, Any]] = []
    invalid = 0
    for hit in hits:
        if hit.get("retrieval_source") == "metadata_pin":
            accepted.append(hit)
            continue
        try:
            dense_score = float(hit["dense_score"])
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        if dense_score >= MIN_RELEVANCE_SCORE:
            accepted.append(hit)
    if not accepted:
        reason = "invalid_hits" if invalid == len(hits) else "weak_retrieval"
        return GateResult(False, NO_EVIDENCE_MESSAGE, reason, dropped_hits=len(hits))

    folded = fold(query)
    names_institution = bool(_bank_name_re().search(folded))
    asks_price = any(term in folded for term in (
        *PRICE_INTENT, "komision", "tarif", "norme interesi", "norma e interesit",
    ))
    if names_institution and asks_price and not any(
        str(hit.get("id", "")).startswith("rate_") for hit in accepted
    ):
        return GateResult(
            False, NO_EVIDENCE_MESSAGE, "wrong_chunk_family",
            dropped_hits=len(hits) - len(accepted),
        )
    return GateResult(
        True,
        reason="metadata_pin" if any(
            hit.get("retrieval_source") == "metadata_pin" for hit in accepted
        ) else "dense_relevance",
        accepted_hits=tuple(accepted),
        dropped_hits=len(hits) - len(accepted),
    )
