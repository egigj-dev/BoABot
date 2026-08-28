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
    "catalog",
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
     "pse eshte kjo", "cfare do te thote kjo", "si", "si?"),
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


# Meta/help questions: the caller asks WHAT the assistant can do, how to use it,
# or to explain itself ("cfare mund te te pyes per shembull?", "si te pyes?",
# "help", "cfare di te bej?"). These must never reach retrieval or the
# card/account scripts — they deterministically fall through to the
# meta_followup bucket (continue-helping), so a router misfire on them cannot
# start the inescapable clarify loop. NOTE: matching is done on the fold()-ed
# (diacritic-stripped) question because decide() restores diacritics
# (cfare -> çfarë) BEFORE the router sees the turn.
_META_HELP_RE = re.compile(
    r"^(?:cfare\s+(?:mund|di)\s+te\s+(?:te\s+)?(?:pyes|bej|thote|them|kerkoj)|"
    r"si\s+(?:mund|duhet)?\s*te\s+(?:te\s+)?(?:pyes|pyesni)|"
    r"si\s+te\s+(?:marr|filloj|perdor)|"
    r"cfare\s+(?:ben|bejn|mund\s+te\s+bej)"
    r"|(?:ndihme|help|help\s+me|cfare\s+ndihme)\b|"
    r"cfare\s+mund\s+t'ju\s+pyes|cfare\s+mund\s+te\s+(?:te\s+)?pyes\b)",
    re.IGNORECASE,
)


def is_meta_help(question: str) -> bool:
    """True for a turn that asks what/how the assistant can help (meta), no retrieval."""
    normed = _norm(question).strip()
    if not normed:
        return False
    if any(anchor in normed for anchor in _DOMAIN_ANCHORS):
        return False
    return _META_HELP_RE.search(normed) is not None

ROUTER_SYSTEM = (
    "Ti je ruteri i kategorimit për një asistent bankar shqiptar që u përgjigjet "
    "rregulloreve bankare e tarifave të bankave. Klasifiko qëllimin e kërkesës së "
    "fundit të përdoruesit në PIKËRISHT NJË nga etiketat e mëposhtme dhe kthe VETËM "
    "atë fjalë, pa asnjë shpjegim: answer, catalog, smalltalk, out_of_domain, account_action, "
    "incident, legal_advice, clarify, meta_followup. Shembuj: 'cilat banka operojnë "
    "në Shqipëri?' -> catalog; 'cilat jane tarifat e kartave te debitit?' -> "
    "answer; 'cili eshte roli i Bankes se Shqiperise?' -> answer; 'a duhet ta paguaj?' -> "
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
    # (and meta/help questions) never touch retrieval (intent-first routing
    # invariant).
    if is_conversational_fragment(question):
        return "meta_followup"
    if is_meta_help(question):
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


# ---- Step 2b: fused single-call intent + rewrite + legal flags -------------
# classify_turn() below decides ONLY the routing label (one model call), and
# api.py then makes a SEPARATE rewrite() call for elliptical follow-ups. That
# is two model calls per substantive turn. analyze_turn() fuses intent +
# standalone-rewrite + legal flags into ONE structured (JSON) call, halving
# the LLM latency on the answer path when the router is ON. It returns None
# when disabled/unavailable/unparseable so the caller falls back to the old
# classify_turn + rewrite() pair unchanged.
from typing import NamedTuple


class TurnAnalysis(NamedTuple):
    label: str | None
    rewritten_query: str | None
    legal_flags: dict | None


_FUSED_SYSTEM = (
    "Ti je ruteri për një asistent bankar shqiptar që u përgjigjet rregulloreve "
    "bankare e tarifave. Për pyetjen e fundit të përdoruesit, kthe VETËM një "
    "objekt JSON (pa markdown, pa shpjegim) me këto fusha: "
    "\"intent\" një nga: answer, catalog, smalltalk, out_of_domain, account_action, "
    "incident, legal_advice, clarify, meta_followup; "
    "\"rewritten_query\" pyetja e plotë e pavarur (zhvilloj referencat bisedore; "
    "kthe vetëm një varg bosh nëse pyetja është tashmë e plotë); "
    "\"legal_flags\" një objekt me \"is_legal_advice\" (bollean: pyetja kërkon "
    "këshillë ligjore për rastin personal, jo vetëm informacion) dhe "
    "\"is_personal_application\" (bollean: aplikohet ligji në situatën e tij "
    "specifike). 'cilat banka operojnë në Shqipëri?' ka intent catalog. "
    "'cilat jane tarifat e kartave te debitit?' ka intent answer, jo catalog. "
    "'cili eshte roli i Bankes se Shqiperise?' ka intent answer. "
    "'a duhet ta paguaj?' ka intent legal_advice (aplikim ligjor për situatën "
    "personale të përdoruesit). 'a jam pergjegjes?' ka intent legal_advice. "
    "'a eshte e ligjshme kjo gjobë?' ka intent legal_advice. 'si je?' ose 'si ke "
    "qene?' ka intent smalltalk. 'si eshte koha sot?' ka intent out_of_domain. "
    "'mbyll llogarinë time' ka intent account_action. 'sa është gjendja e "
    "llogarisë sime' ka intent account_action. 'kam humbur kartën, çfarë të bëj?' "
    "ka intent incident. 'më vidhën paranë nga llogaria' ka intent incident. "
    "'a garanton BSH qe banka ime nuk mund te me mbyll llogarine?' ka intent answer "
    "(pyetje për ekzistencën e një rregulle, jo kërkesë veprimi). 'a kam te drejte "
    "te marr kopje te kontrates sime?' ka intent answer. 'cilat jane detyrimet e "
    "bankes?' ka intent answer. 'cili eshte afati per ankimimin?' ka intent answer. "
    "'pse duhet te trajtohet nga nje agjent njerezor?' ka intent meta_followup. "
    "'cila eshte norma e interesit per depozita?' ka intent answer. "
    "Incident kërkon që telefonuesi të raportojë diçka që i ka ndodhur llogarisë "
    "së vet, jo një pyetje të përgjithshme për produktet e bankave. "
    "'a ofrojne bkt, credins dhe otp kredi?' ka intent answer. 'jo dua te di thjesht "
    "nese ofrojne kredi' ka intent answer. 'cfare tarifash ka nje kredi "
    "konsumatore?' ka intent catalog. "
    "Shembull output: {\"intent\":\"answer\",\"rewritten_query\":\"Sa është komisioni "
    "për shlyerje të parakohshme të kredisë për shtëpi?\",\"legal_flags\":"
    "{\"is_legal_advice\":false,\"is_personal_application\":false}}"
)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _parse_fused(text: str) -> TurnAnalysis | None:
    """Best-effort parse of the fused JSON response; never raises."""
    if not text:
        return None
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    try:
        import json
        payload = json.loads(text.strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    label = payload.get("intent")
    if not isinstance(label, str) or label.strip().casefold() not in LABELS:
        label = "answer"
    raw = payload.get("rewritten_query")
    rewritten = None
    if isinstance(raw, str):
        candidate = raw.strip()
        if candidate and not ("\n" in candidate or "\r" in candidate):
            rewritten = candidate
    legal = payload.get("legal_flags")
    flags = None
    if isinstance(legal, dict):
        flags = {
            "is_legal_advice": bool(legal.get("is_legal_advice")),
            "is_personal_application": bool(legal.get("is_personal_application")),
        }
    return TurnAnalysis(label, rewritten, flags)


def analyze_turn(question: str, history: list[dict[str, str]] | None = None,
                 last_outcome=None, last_handoff: bool = False) -> TurnAnalysis | None:
    """Fused route: one call decides intent label + standalone query + flags.

    Returns a TurnAnalysis, or None when disabled / no key / unparseable so the
    caller falls back to classify_turn() + rewrite(). Never escalates on
    failure: an unreadable intent defaults to \"answer\" (fall through to
    retrieval), exactly like classify_turn.
    """
    if is_conversational_fragment(question):
        return TurnAnalysis("meta_followup", None, None)
    if is_meta_help(question):
        return TurnAnalysis("meta_followup", None, None)
    if not _enabled():
        return None
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return None
    try:
        from . import rag
        turns = [m for m in (history or []) if m.get("role") in ("user", "assistant")][-4:]
        ctx = "\n".join(f"{m['role']}: {m.get('content', '')}" for m in turns)
        context = ""
        if last_outcome or last_handoff:
            context = (
                f"outcome-e-mëparshme: {last_outcome}; "
                f"handoff-e-mëparshme: {int(bool(last_handoff))}\n"
            )
        user = f"{ctx}\nuser: {question}" if ctx else question
        if context:
            user = f"{context}pyetja e fundit: {user}"
        out = rag._post({
            "model": rag.MODEL,
            "messages": [
                {"role": "system", "content": _FUSED_SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        })
        text = (rag.completion_message(out).get("content") or "").strip()
    except Exception:
        return None
    return _parse_fused(text)
