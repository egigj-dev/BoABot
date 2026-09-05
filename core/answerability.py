"""Answerability: decide whether retrieved evidence actually answers the question.

The deterministic evidence gate (core/trust.py trusted_hits) rejects retrieval that
is too weak or the wrong chunk family. It cannot tell whether a topically-related
passage *answers* the question, so a model can still produce a confident-sounding
answer from tangential material it was trained to ground on. This layer is the
"answer | abstain" box after RAG in the pipeline: given the question and the
accepted evidence, judge whether the response should be generated at all.

Two mechanisms, mirroring the LLM turn-router design:

1. DETERMINISTIC FLOOR (always on, offline, no model call).  Narrow
   evidence-containment checks for the two most clear-cut non-answerable cases,
   based on whether the answer material exists in the evidence -- not on growing
   regex lists of question phrasings:
     - the question pins an explicit article ("neni 7") but no accepted chunk
       carries that article in metadata (and it was not the trusted Statuti pin);
     - the question is a how-much price ask (PRICE_INTENT family) but NO accepted
       chunk text contains any numeric value, so no truthful numeric answer exists
       in the evidence.
   These fire in BOTH ON and OFF modes (fail-closed) so a weak-but-admitted
   retrieval can never reach generation.

2. LLM SEMANTIC VERDICT (OFF by default; BOABOT_LLM_ANSWERABILITY=1 enables it).
   One small classification call asking whether the materials answer the question
   completely and accurately. YES -> generate; NO / UNCLEAR -> abstain; provider
   failure or unparseable -> None -> the caller falls through to generation (fail-
   open), so an answerability outage cannot turn every answerable question into
   an abstain (the same lesson as the fidelity guard's soft-fail change).

The failure direction differs from the router by design: the router escalates to
handoff (guarded), so it fails OPEN on failure; abstain is cheap and safe, but a
provider blip must not masquerade as "the corpus lacks the answer" -- so a *real*
NO/UNCLEAR judgment abstains, whereas a transport failure falls through to the
existing generation path.
"""
from __future__ import annotations

import os
import re
from enum import Enum

from .text_norm import fold
from .trust import PRICE_INTENT

_ENABLE = ("1", "true", "yes", "on")

# Mirrors the explicit-article regex in core/rag.py so both agree on "neni N".
_ARTICLE_RE = re.compile(r"\bneni(?:n|t)?\s+(\d+(?:/\d+)?)\b", re.I)
_DIGIT_RE = re.compile(r"\d")
_FINANCIAL_VALUE_RE = re.compile(
    r"(?:\d+[.,]\d+|\d+(?:[ '.]\d+)*\s*(?:%|lek\w*|eur|euro|usd|dollar\w*))",
    re.I,
)
_FINANCIAL_FACT_RE = re.compile(
    r"\b(?:tarif|komision|kosto|interes|norm)\w*\b", re.I,
)

ABSTAIN_MESSAGE = (
    "Nuk kam një përgjigje të saktë për këtë pyetje nga të dhënat e publikuara. "
    "Riformuloni pyetjen ose specifikoni bankën, produktin ose rregulloren dhe do "
    "të provoj përsëri."
)

_VERDICT_SYSTEM = (
    "Ti je kontrolluesi i përgjigjshmërisë për një asistent bankar shqiptar që "
    "u përgjigjet rregulloreve bankare e tarifave. Pyetja e përdoruesit dhe "
    "materialet e marra do të jepen më poshtë. Vendos nëse materialet përmbajnë "
    "informacion të mjaftueshëm për t'u përgjigjur pyetjes, edhe kur përgjigja "
    "duhet ndërtuar duke bashkuar disa fragmente. Kthe VETËM një fjalë, pa asnjë "
    "shpjegim: YES vetëm nëse materialet vendosin vetë faktin e kërkuar. "
    "Lidhja tematike nuk mjafton: detyrimi për publikimin e një tarife nuk "
    "vendos shumën e tarifës; një normë nga vetëm një bankë nuk vendos renditjen; "
    "dhe kredia e përgjithshme nuk vendos disponueshmërinë e kredisë për udhëtime. "
    "NO nëse fakti i kërkuar nuk mbështetet. UNCLEAR vetëm kur një pjesë e faktit "
    "të kërkuar mbështetet, por përgjigjja mbetet realisht e paplotë."
)
_VERDICT_USER = (
    "fakti i kërkuar: {requested_fact}\n"
    "pyetja: {question}\n\nmaterialet e marra:\n{evidence}"
)
_VERDICT_YES = re.compile(r"\bYES\b", re.I)
_VERDICT_NO = re.compile(r"\bNO\b", re.I)
_VERDICT_UNCLEAR = re.compile(r"\bUNCLEAR\b", re.I)

# Per-chunk / total evidence budget sent to the LLM verdict (token control).
_EVIDENCE_PER_CHUNK = 1_200
_EVIDENCE_TOTAL = 6_000


def _enabled() -> bool:
    return os.environ.get("BOABOT_LLM_ANSWERABILITY", "").strip().lower() in _ENABLE


def _hits_contain_digit(hits) -> bool:
    return any(_DIGIT_RE.search(str(hit.get("text") or "")) for hit in hits)


class RequestedFact(str, Enum):
    FEE_AMOUNT = "FEE_AMOUNT"
    INTEREST_RATE = "INTEREST_RATE"
    COMPARISON_RANKING = "COMPARISON_RANKING"
    PRODUCT_AVAILABILITY = "PRODUCT_AVAILABILITY"
    REGULATORY_RULE = "REGULATORY_RULE"
    DEFINITION = "DEFINITION"
    PROCEDURE = "PROCEDURE"
    INSTITUTION_IDENTITY = "INSTITUTION_IDENTITY"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"


def requested_fact(question: str) -> RequestedFact:
    folded = fold(question)
    if re.search(r"\b(?:cfare eshte|perkufiz)\w*\b", folded):
        return RequestedFact.DEFINITION
    if re.search(r"\b(?:me e ulet|me te ulet|me e lire|me te mire|krahas)\w*\b", folded):
        return RequestedFact.COMPARISON_RANKING
    if _price_ask(question):
        if re.search(r"\b(?:tarif|komision|kosto)\w*\b", folded):
            return RequestedFact.FEE_AMOUNT
        if re.search(r"\b(?:interes|norm)\w*\b", folded):
            return RequestedFact.INTEREST_RATE
    if re.search(
            r"\b(?:publik|detyrim|transparenc)\w*\b|"
            r"\b(?:cfar|cil)\w*\s+rregull\w*", folded):
        return RequestedFact.REGULATORY_RULE
    if re.search(r"\b(?:ofron|ofrojne|ka)\b", folded):
        return RequestedFact.PRODUCT_AVAILABILITY
    if re.search(r"\b(?:si mund|si behet|procedure)\w*\b", folded):
        return RequestedFact.PROCEDURE
    if re.search(r"\bcilat?\s+banka\b", folded):
        return RequestedFact.INSTITUTION_IDENTITY
    return RequestedFact.GENERAL_INFORMATION

def _hits_contain_requested_financial_value(hits) -> bool:
    for hit in hits:
        text = str(hit.get("text") or "")
        for sentence in re.split(r"[\n!?]+|(?<!\d)\.(?!\d)", text):
            if _FINANCIAL_FACT_RE.search(sentence) and _FINANCIAL_VALUE_RE.search(sentence):
                return True
    return False


def _hits_have_article(hits, article: str) -> bool:
    """True if any accepted chunk carries the article, or is an explicitly pinned hit."""
    for hit in hits:
        if hit.get("retrieval_source") == "metadata_pin":
            return True
        if str(hit.get("article") or "") == str(article):
            return True
    return False


def _price_ask(question: str) -> bool:
    folded = fold(question)
    return (
        any(term in folded for term in PRICE_INTENT)
        or re.search(r"\bcilat?\s+jane\s+(?:tarif|komision|kosto)\w*", folded)
        is not None
    )


def lexical_verdict(question: str, hits) -> tuple[bool, str]:
    """Deterministic evidence-containment floor. Returns (answerable, reason)."""
    folded = fold(question)
    article = _ARTICLE_RE.search(folded)
    if article and not _hits_have_article(hits, article.group(1)):
        return False, "abstain_no_article_in_evidence"
    fact = requested_fact(question)
    if (fact in {RequestedFact.FEE_AMOUNT, RequestedFact.INTEREST_RATE}
            and not _hits_contain_requested_financial_value(hits)):
        return False, "abstain_price_without_value"
    return True, ""


def _coerce_rate_intent(value):
    from .comparison import RateIntent

    if isinstance(value, RateIntent):
        return value
    if not isinstance(value, dict):
        return None
    try:
        payload = dict(value)
        payload["banks"] = tuple(payload.get("banks") or ())
        return RateIntent(**payload)
    except (KeyError, TypeError, ValueError):
        return None


def structured_verdict(intent, hits) -> tuple[str, str]:
    """Validate a typed exact resolution without trusting its source tag alone."""
    from .comparison import _row_slots, published_product_terms, resolve_rate_rows

    typed_intent = _coerce_rate_intent(intent)
    if typed_intent is None and hits:
        typed_intent = _coerce_rate_intent(hits[0].get("rate_resolution"))
    if typed_intent is None:
        return "UNSUPPORTED", "structured_rate_untrusted_metadata"
    if any(hit.get("retrieval_source") != "structured_rate" for hit in hits):
        return "UNSUPPORTED", "structured_rate_mixed_evidence"

    # A caller may ask about availability, but these rows establish only that
    # published terms exist; the renderer preserves that evidence scope.
    if typed_intent.availability:
        offers = published_product_terms(typed_intent)
        if not offers or not hits:
            return "UNSUPPORTED", "structured_rate_missing_key"
        if len(hits) != len(offers):
            return "UNSUPPORTED", "structured_rate_incomplete_resolution"
        for hit in hits:
            if hit.get("rate_resolution") != typed_intent._asdict():
                return "UNSUPPORTED", "structured_rate_untrusted_metadata"
        return "SUPPORTED", "structured_published_product_terms"

    expected_rows = resolve_rate_rows(typed_intent)
    if not expected_rows or not hits:
        return "UNSUPPORTED", "structured_rate_missing_key"

    expected = {str(row.get("_id")): _row_slots(row)._asdict() for row in expected_rows}
    actual_ids = [str(hit.get("id") or "") for hit in hits]
    if actual_ids != list(expected):
        return "UNSUPPORTED", "structured_rate_incomplete_resolution"
    resolution = typed_intent._asdict()
    for hit in hits:
        if hit.get("rate_resolution") != resolution:
            return "UNSUPPORTED", "structured_rate_untrusted_metadata"
        if hit.get("rate_row_slots") != expected[str(hit.get("id") or "")]:
            return "UNSUPPORTED", "structured_rate_untrusted_metadata"
    return "SUPPORTED", "structured_rate"


def _evidence_text(hits) -> str:
    """Concatenate chunk texts (doc-labeled, truncated) for the verdict prompt."""
    parts: list[str] = []
    budget = 0
    for hit in hits:
        doc = str(hit.get("doc") or "")
        text = str(hit.get("text") or "")
        if len(text) > _EVIDENCE_PER_CHUNK:
            text = text[:_EVIDENCE_PER_CHUNK] + "…"
        piece = f"[{doc}] {text}" if doc else text
        if budget + len(piece) > _EVIDENCE_TOTAL:
            remaining = _EVIDENCE_TOTAL - budget
            if remaining > 0:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        budget += len(piece)
    return "\n".join(parts)


def _answerability_verdict(question: str, hits):
    """Injectable seam: the LLM answerability judgment. Mirrors the router seam.

    Returns one of YES / NO / UNCLEAR, or None when disabled/off or the provider
    call or parse fails. The caller treats None as "not decided" (fall through to
    generation), NOT as abstain.
    """
    if not _enabled():
        return None
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return None
    material = _evidence_text(hits)
    user_content = _VERDICT_USER.format(
        requested_fact=requested_fact(question).value,
        question=question, evidence=material,
    )
    try:
        from . import rag
        out = rag._post({
            "model": rag.MODEL,
            "messages": [
                {"role": "system", "content": _VERDICT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
        })
        text = (rag.completion_message(out).get("content") or "").strip()
    except Exception:
        return None
    if _VERDICT_UNCLEAR.search(text):
        return "UNCLEAR"
    if _VERDICT_NO.search(text):
        return "NO"
    if _VERDICT_YES.search(text):
        return "YES"
    return None


def _level(question: str, hits, rate_intent=None) -> tuple[str, str]:
    """Three-way classification of how completely the evidence answers.

    Returns (level, reason) where level is one of:
      SUPPORTED          -> the evidence answers completely -> generate.
      PARTIALLY_SUPPORTED-> the evidence answers only partly (LLM UNCLEAR) ->
                           still generate, but may lead with a partial hedge.
      UNSUPPORTED        -> the evidence does not answer -> abstain.
    Fail-closed on the lexical floor; the LLM verdict abstains only on a real
    NO and downgrades to PARTIALLY on UNCLEAR (Step 6). No hits, or a parse/
    transport failure where judgement is inconclusive, never fabricates an
    answer.
    """
    if rate_intent is not None or any(
            hit.get("retrieval_source") == "structured_rate" for hit in hits):
        return structured_verdict(rate_intent, hits)
    if not hits:
        return "UNSUPPORTED", "abstain_no_hits"
    answerable_, reason = lexical_verdict(question, hits)
    if not answerable_:
        return "UNSUPPORTED", reason
    verdict = _answerability_verdict(question, hits)
    if verdict and str(verdict).strip().upper() == "NO":
        return "UNSUPPORTED", "abstain_llm_judgment"
    if verdict and str(verdict).strip().upper() == "UNCLEAR":
        return "PARTIALLY_SUPPORTED", "partial_llm_judgment"
    return "SUPPORTED", ""


def judge(question: str, hits, rate_intent=None) -> tuple[str, str]:
    """Step 6: three-way SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED gate.

    Returns (level, reason). Backward-compatible with the old binary contract:
    SUPPORTED and PARTIALLY_SUPPORTED both mean \"can generate\"; only
    UNSUPPORTED abstains.
    """
    return _level(question, hits, rate_intent=rate_intent)


def answerable(question: str, hits, rate_intent=None) -> tuple[bool, str]:
    """Return (can_generate, abstain_reason). Kept for backward compatibility.

    UNSUPPORTED abstains; SUPPORTED and PARTIALLY_SUPPORTED both allow
    generation (the caller may use the level to decide whether to lead with a
    hedge).
    """
    level, reason = _level(question, hits, rate_intent=rate_intent)
    return (level != "UNSUPPORTED", reason)
