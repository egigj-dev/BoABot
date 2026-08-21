"""LLM turn-router: classify one Albanian turn into a routing label.

BoABot's routing has historically been lexical regexes (smalltalk, account_action,
incident vocabulary, legal-advice floor). Those are unbounded against colloquial
Albanian (the live run showed "si ke qene?" -> handoff, "si eshte koha sot?" ->
handoff, "a garanton BSH qe banka ime nuk mund te me mbyll llogarine?" -> account
handoff). This module replaces the SEMANTIC routing decisions with one LLM intent
call, while the security-critical gates (input_gate / secret / PII / repeat) and
the deterministic legal-advice floor + post-gen scanner remain lexical fail-closed.

AVAILABILITY: intentionally OFF by default. Flip `BOABOT_LLM_ROUTER=1` (or true/on)
to enable. When disabled, or when the provider call fails, classify_turn returns
None and the caller falls back to the previous lexical routing — so behavior and
offline tests are unchanged until the router is explicitly turned on.
"""

from __future__ import annotations

import os
import re

LABELS = (
    "answer",
    "smalltalk",
    "out_of_domain",
    "account_action",
    "incident",
    "legal_advice",
    "clarify",
    "meta_followup",
)

_ENABLE = ("1", "true", "yes", "on")

# Degenerate conversational fragments: follow-ups, confusion, or cross-turn
# corrections ("pse?", "nuk te kuptoj", "kjo nuk ishte pyetja ime"). These carry
# no banking subject matter and must NEVER touch retrieval — the whole point of
# intent-first routing is to not retrieve BOA documents before deciding the
# query is a BOA knowledge query. This is a deterministic FLOOR (no model call)
# so a tiny set of observed fragments cannot degrade.
_FRAGMENTS = frozenset(
    ("pse", "perse", "pse jo", "cbehet", "cbehet ne pergjithesi",
     "nuk te kuptoj", "nuk kuptoj", "kjo nuk ishte pyetja ime",
     "pse eshte kjo", "cfare do te thote kjo")
)
_DOMAIN_ANCHORS = (
    "bank", "kredi", "komision", "norm", "tarif", "interes", "llogari",
    "kart", "depozit", "rregullore", "neni", "licenc",
)


def _norm(question: str) -> str:
    import unicodedata
    stripped = question.strip().lower().replace("'", "")
    return unicodedata.normalize("NFKD", stripped).encode("ascii", "ignore").decode()


def is_conversational_fragment(question: str) -> bool:
    """True for tiny follow-up/confusion fragments with no banking subject."""
    canon = _norm(question).strip(" ?!.,;:'\"")
    if not canon or len(canon) > 40:
        return False
    if any(anchor in canon for anchor in _DOMAIN_ANCHORS):
        return False
    return canon in _FRAGMENTS

ROUTER_SYSTEM = (
    "Ti je ruteri i kategorimit për një asistent bankar shqiptar që u përgjigjet "
    "rregulloreve bankare e tarifave të bankave. Klasifiko qëllimin e kërkesës së "
    "fundit të përdoruesit në PIKËRISHT NJË nga etiketat e mëposhtme dhe kthe VETËM "
    "atë fjalë, pa asnjë shpjegim: answer, smalltalk, out_of_domain, account_action, "
    "incident, legal_advice, clarify, meta_followup. Shembuj: 'a duhet ta paguaj?' -> "
    "legal_advice (aplikim ligjor për situatën personale të përdoruesit); 'a jam "
    "pergjegjes?' -> legal_advice; 'a eshte e ligjshme kjo gjobë?' -> legal_advice; "
    "'si je?' ose 'si ke qene?' -> smalltalk; 'si eshte koha sot?' -> out_of_domain; "
    "'mbyll llogarinë time' -> account_action; 'sa është gjendja e llogarisë sime' -> "
    "account_action; 'kam humbur kartën, çfarë të bëj?' -> incident; 'më vidhën paranë "
    "nga llogaria' -> incident; 'a garanton BSH qe banka ime nuk mund te me mbyll "
    "llogarine?' -> answer (pyetje për ekzistencën e një rregulle, jo kërkesë veprimi); "
    "'a kam te drejte te marr kopje te kontrates sime?' -> answer; 'cilat jane detyrimet "
    "e bankes?' -> answer; 'cili eshte afati per ankimimin?' -> answer; 'pse duhet te "
    "trajtohet nga nje agjent njerezor?' -> meta_followup; 'cila eshte norma e interesit "
    "per depozita?' -> answer."
)

_ANY_LABEL_RE = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(l) for l in LABELS), re.I)


def _enabled() -> bool:
    return os.environ.get("BOABOT_LLM_ROUTER", "").strip().lower() in _ENABLE


def classify_turn(question: str, last_outcome=None, last_handoff: bool = False):
    """Return a routing label, or None when off/unavailable/parse-failed.

    Never escalates on failure: an unparseable or erroring response defaults to
    "answer" (fall through to retrieval), so a router hiccup cannot cause a
    spurious handoff. Returns None (not "answer") only when disabled/no-key, so
    the caller uses its offline lexical fallback for exactly the old behavior.
    """
    # Deterministic floor shared by ON and OFF modes: conversational fragments
    # never touch retrieval (intent-first routing invariant).
    if is_conversational_fragment(question):
        return "meta_followup"
    if not _enabled():
        return None
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return None
    try:
        from . import rag
        context = ""
        if last_outcome or last_handoff:
            context = (
                f"outcome-e-mëparshme: {last_outcome}; "
                f"handoff-e-mëparshme: {int(bool(last_handoff))}\n"
            )
        out = rag._post({
            "model": rag.MODEL,
            "messages": [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": f"{context}pyetja: {question}"},
            ],
            "temperature": 0,
        })
        text = (rag.completion_message(out).get("content") or "").strip().lower()
    except Exception:
        return None
    match = _ANY_LABEL_RE.search(text)
    if not match:
        # Conservative: an unreadable label routes to retrieval, never handoff.
        return "answer"
    return match.group(0)
