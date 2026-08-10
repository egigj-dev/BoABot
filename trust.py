"""Deterministic guardrails between user input, retrieval, and generation."""
from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

MIN_RELEVANCE_SCORE = 0.50
PRICE_INTENT = ("sa esht", "sa eshte", "sa kushton", "sa paguaj", "cfare tarife",
                "sa me kushton")

BUSINESS_DEPOSIT_MESSAGE = (
    "Nuk mund të jap normë për depozita të bizneseve, sepse kjo kategori nuk "
    "gjendet në korpusin aktual. Tabelat përmbajnë norma depozitash për "
    "individë, por jo për biznese; nuk do të hamendësoj shifra."
)
NO_EVIDENCE_MESSAGE = (
    "Nuk gjeta burim mjaftueshëm të lidhur në korpus për t’iu përgjigjur me "
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

def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

_BANK_NAME_STOPLIST = frozenset({"sh.a"})
_BANK_NAME_STOP_PREFIXES = ("shqiper", "bank", "alban")
_NON_INSTITUTION_RATE_LABELS = frozenset({"biznes i vogel", "kredi per shtepi/prona"})
_RATE_LABEL_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*[-+]?\d")

def _load_bank_names() -> list[str]:
    """Load commercial-bank identity tokens from the rate corpus.

    The Bank of Albania is the regulator, not a commercial provider of quotable
    tariffs; geographic qualifiers are not institution identities.
    """
    names = set()
    path = Path(__file__).with_name("rate_tables.jsonl")
    with path.open(encoding="utf-8") as rate_file:
        for line in rate_file:
            text = json.loads(line)["text"]
            for data_line in text.splitlines():
                match = _RATE_LABEL_RE.match(data_line)
                if not match:
                    continue
                source_label = match.group(1).strip()
                label = _fold(source_label)
                if ("banka e shqiperis" in label
                        or label in _NON_INSTITUTION_RATE_LABELS):
                    continue
                for source_token in source_label.split():
                    token = _fold(source_token)
                    if ((len(token) >= 4
                            or len(token) >= 3 and source_token.isupper())
                            and token not in _BANK_NAME_STOPLIST
                            and not token.startswith(_BANK_NAME_STOP_PREFIXES)):
                        names.add(token)
    return sorted(names)

BANK_NAMES = _load_bank_names()

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
    printable = sum(chr(byte).isprintable() or chr(byte).isspace() for byte in decoded)
    return printable / len(decoded) >= 0.9

def _looks_like_instruction_override(text: str) -> bool:
    folded = _fold(text)
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

def is_business_deposit_question(question: str, history: Iterable[dict[str, str]] = ()) -> bool:
    """Identify the explicitly unsupported business-deposit rate category."""
    current = _fold(question)
    context = " ".join(_fold(message.get("content", "")) for message in history)
    has_business = any(term in current for term in (
        "biznes", "kompani", "shoqeri", "ndermarr", "tregtar", "korporat",
    ))
    has_deposit = "depozit" in current or (has_business and "depozit" in context)
    return has_business and has_deposit

def trusted_hits(query: str, hits: list[dict[str, Any]]) -> GateResult:
    """Accept only retrieval results strong enough to be used as evidence.

    The rate-family gate stops a regulation passage being quoted as a commercial
    tariff. That risk only exists when the caller asks for a specific institution's
    price.
    """
    if not hits:
        return GateResult(False, NO_EVIDENCE_MESSAGE, "no_hits")
    try:
        best_score = float(hits[0]["score"])
    except (KeyError, TypeError, ValueError):
        return GateResult(False, NO_EVIDENCE_MESSAGE, "invalid_hits")
    if best_score < MIN_RELEVANCE_SCORE:
        return GateResult(False, NO_EVIDENCE_MESSAGE, "weak_retrieval")
    folded = _fold(query)
    rate_terms = ("komision", "tarif", "norm", "interes", "depozit", "karte")
    has_price_intent = any(term in folded for term in BANK_NAMES + list(PRICE_INTENT))
    if (any(term in folded for term in rate_terms) and has_price_intent and not any(
        str(hit.get("id", "")).startswith("rate_") for hit in hits
    )):
        return GateResult(False, NO_EVIDENCE_MESSAGE, "wrong_chunk_family")
    return GateResult(True)
