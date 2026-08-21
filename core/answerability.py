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

from .text_norm import fold
from .trust import PRICE_INTENT

_ENABLE = ("1", "true", "yes", "on")

# Mirrors the explicit-article regex in core/rag.py so both agree on "neni N".
_ARTICLE_RE = re.compile(r"\bneni(?:n|t)?\s+(\d+(?:/\d+)?)\b", re.I)
_DIGIT_RE = re.compile(r"\d")

ABSTAIN_MESSAGE = (
    "Materialet që gjeta në korpus nuk përmbajnë përgjigjen e kësaj pyetjeje. "
    "Nuk do të hamendësoj një përgjigje. Mund ta riformuloni pyetjen ose të "
    "specifikoni bankën, produktin ose rregulloren konkrete dhe do të provoj përsëri."
)

_VERDICT_SYSTEM = (
    "Ti je kontrolluesi i përgjigjshmërisë për një asistent bankar shqiptar që "
    "u përgjigjet rregulloreve bankare e tarifave. Pyetja e përdoruesit dhe "
    "materialet e marra nga korpusi do të jepen më poshtë. Vendos nëse materialet "
    "përmbajnë PËRGJIGJEN E PLOTË DHE TË SAKTË për pyetjen. Kthe VETËM një fjalë, "
    "pa asnjë shpjegim: YES nëse materialet përmbajnë informacionin e nevojshëm për "
    "t'u përgjigjur pyetjes; NO nëse materialet zënë me temë tjetër të lidhur por "
    "nuk e përmbajnë përgjigjen, ose vetëm një pjesë të saj; UNCLEAR nëse nuk je i "
    "sigurt nëse materialet janë të mjaftueshëm."
)
_VERDICT_USER = "pyetja: {question}\n\nmaterialet e marra nga korpusi:\n{evidence}"
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
    return any(term in folded for term in PRICE_INTENT)


def lexical_verdict(question: str, hits) -> tuple[bool, str]:
    """Deterministic evidence-containment floor. Returns (answerable, reason)."""
    folded = fold(question)
    article = _ARTICLE_RE.search(folded)
    if article and not _hits_have_article(hits, article.group(1)):
        return False, "abstain_no_article_in_evidence"
    if _price_ask(question) and not _hits_contain_digit(hits):
        return False, "abstain_price_without_value"
    return True, ""


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
    user_content = _VERDICT_USER.format(question=question, evidence=material)
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


def answerable(question: str, hits) -> tuple[bool, str]:
    """Return (can_generate, abstain_reason). Fail-closed on the lexical floor;
    the LLM verdict abstains only on a real NO/UNCLEAR judgment.
    """
    if not hits:
        return False, "abstain_no_hits"
    answerable_, reason = lexical_verdict(question, hits)
    if not answerable_:
        return False, reason
    verdict = _answerability_verdict(question, hits)
    if verdict and str(verdict).strip().upper() in ("NO", "UNCLEAR"):
        return False, "abstain_llm_judgment"
    return True, ""