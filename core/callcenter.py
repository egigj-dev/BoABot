"""Call-center conversation policy and in-memory session state."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .retrieve import EMBEDDING_MODEL_NAME, model
from .text_norm import fold, restore_diacritics
from .trust import NO_EVIDENCE_MESSAGE, UNSAFE_INPUT_MESSAGE, bank_names, input_gate

if TYPE_CHECKING:
    from .comparison import RateIntent

MAX_HISTORY_MESSAGES = 12
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 1_000

CLARIFY_MESSAGE = (
    "Mund ta sqaroni pak pyetjen? Për shembull, tregoni bankën, produktin "
    "ose rregulloren për të cilën po pyesni."
)
CARD_CLARIFY_MESSAGE = (
    "Ju lutem specifikoni nëse karta është debiti apo krediti dhe nëse është "
    "për individ apo biznes."
)
LEGAL_ADVICE_MESSAGE = (
    "Kjo pyetje ka të bëjë me një çështje ligjore të situatës tuaj të veçantë, "
    "jo vetëm me informacionin rregullator që unë ndaj. Unë jap vetëm informacion "
    "nga rregulloret dhe nuk mund të jap këshillë ligjore për rastin tuaj. Për të "
    "drejtat tuaja dhe hapat që mund të ndërmerrni, ju lutem konsultohuni me një "
    "avokat ose me bankën tuaj."
)
PERSONAL_RECORD_CAPABILITY_MESSAGE = (
    "Nuk kam qasje në të dhënat tuaja në Regjistrin e Kredive dhe nuk mund ta "
    "verifikoj raportin tuaj. Për ta marrë raportin, paraqisni një kërkesë me "
    "shkrim pranë Regjistrit të Kredive sipas procedurës së përshkruar në “Norma "
    "e Regjistrit të Kredive”. Mund t'ju shpjegoj si funksionon regjistri."
)
OUT_OF_DOMAIN_MESSAGE = (
    "Kjo pyetje është jashtë fushës së shërbimit tim, që është informacioni për "
    "rregulloret bankare shqiptare dhe tarifat e bankave. Nuk mund të jap "
    "informacion mbi këtë temë. A mund t'ju ndihmoj me ndonjë pyetje tjetër për "
    "bankimin?"
)
META_FOLLOWUP_MESSAGE = (
    "Po përpiqem t'ju ndihmoj me informacion mbi rregulloret bankare ose tarifat "
    "e bankave. Nëse keni ndonjë pyetje konkrete për këto, më thuajeni dhe do të "
    "përgjigjem."
)
META_FOLLOWUP_HANDOFF_MESSAGE = (
    "Ju kaluam te një agjent njerëzor sepse kërkesa juaj e mëparshme kërkonte "
    "verifikim të llogarisë ose trajtim të një incidenti. Për sigurinë tuaj, unë "
    "nuk mund të përpunoj të dhëna personale të llogarisë këtu. Po jua kaloj "
    "përsëri një agjenti njerëzor."
)
SECURITY_HANDOFF_MESSAGE = (
    "Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. "
    "Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë."
)
ACCOUNT_HANDOFF_MESSAGE = (
    "Një agjent njerëzor mund të verifikojë llogarinë tuaj dhe ta trajtojë këtë kërkesë. "
    "Po jua kaloj bisedën një agjenti."
)
PII_HANDOFF_MESSAGE = (
    "Mos ndani të dhëna personale në këtë bisedë. "
    "Po jua kaloj kërkesën një agjenti njerëzor."
)
# Backward-compatible public name for callers that imported the security message.
HANDOFF_MESSAGE = SECURITY_HANDOFF_MESSAGE
REPEAT_MESSAGE = "Nuk kam ende një përgjigje për ta përsëritur. Si mund t’ju ndihmoj?"

class Outcome(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    HANDOFF = "handoff"
    REPEAT = "repeat"
    DEGRADED = "degraded"
    ABANDONED = "abandoned"


class ContextEffect(Enum):
    PRESERVE = "preserve"
    REPLACE = "replace"
    CLEAR = "clear"


class DecisionEvent(Enum):
    context_inherited = "context_inherited"
    query_rewritten = "query_rewritten"
    structured_lookup = "structured_lookup"
    unresolved_qualifier_detected = "unresolved_qualifier_detected"
    fidelity_sentence_drop = "fidelity_sentence_drop"


class DecisionReason(str, Enum):
    UNSAFE_INPUT = "unsafe_input"
    CREDENTIAL_DISCLOSURE = "credential_disclosure"
    PII_DETECTED = "pii_detected"
    REPEAT = "repeat"
    LEGAL_ADVICE_EXPLICIT = "legal_advice_explicit"
    LEGAL_ADVICE_POSTGEN = "legal_advice_postgen"
    NEGATION_STATEMENT = "negation_statement"
    FRAGMENT_META = "fragment_meta"
    BANK_CATALOG_LIST = "bank_catalog_list"
    CATALOG_EXACT_HIT = "catalog_exact_hit"
    CATALOG_UNKNOWN_BANK = "catalog_unknown_bank"
    CATALOG_CONFLICTING_SLOTS = "catalog_conflicting_slots"
    COMPARISON_DIMENSIONS_MISSING = "comparison_dimensions_missing"
    PERSONAL_RECORD_CAPABILITY_BOUNDARY = "personal_record_capability_boundary"
    CATALOG_MISSING_KEY = "catalog_missing_key"
    SEMANTIC_INCIDENT = "semantic_incident"
    SEMANTIC_ACCOUNT_ACTION = "semantic_account_action"
    SEMANTIC_SMALLTALK = "semantic_smalltalk"
    SEMANTIC_OUT_OF_DOMAIN = "semantic_out_of_domain"
    SEMANTIC_LEGAL_ADVICE = "semantic_legal_advice"
    SEMANTIC_CLARIFY = "semantic_clarify"
    SEMANTIC_META_FOLLOWUP = "semantic_meta_followup"
    ACCOUNT_ACTION_BACKSTOP = "account_action_backstop"
    INCIDENT_BACKSTOP = "incident_backstop"
    DENSE_RETRIEVAL = "dense_retrieval"
    REWRITE_CARD_CLARIFY = "rewrite_card_clarify"
    DENSE_ANSWER = "dense_answer"
    DENSE_NO_TRUSTED_HITS = "dense_no_trusted_hits"
    ANSWERABILITY_ABSTAIN = "answerability_abstain"
    EMPTY_ANSWER = "empty_answer"
    STRUCTURED_EMPTY_RENDER = "structured_empty_render"
    CLIENT_DISCONNECT = "client_disconnect"
    PROVIDER_ERROR = "provider_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome | None
    message: str = ""
    question: str = ""
    handoff: bool = False
    pii_redacted: bool = False
    query_embedding: np.ndarray | None = None  # Normalized caller vector for downstream retrieval reuse.
    handoff_score: float | None = None  # Frozen positive-vs-negative neighbour margin.
    reason: DecisionReason = field(kw_only=True)
    rewritten_query: str | None = None  # Step 2b: standalone query from the fused router call (when ON).
    legal_flags: dict | None = None  # Step 10 groundwork: structured flags from the fused call, if any.
    rate_intent: RateIntent | None = None  # Typed key on the no-LLM structured path.
    trace_flags: frozenset[DecisionEvent] = field(default_factory=frozenset, kw_only=True)

@dataclass
class Session:
    session_id: str
    history: list[dict[str, str]]
    last_answer: str
    updated_at: float
    last_outcome: Outcome | None = None
    last_handoff: bool = False
    last_structured_frame: RateIntent | None = None


def frame_effect(reason: DecisionReason) -> ContextEffect:
    """Return the structured-frame lifecycle effect for a terminal reason."""
    if reason is DecisionReason.CATALOG_EXACT_HIT:
        return ContextEffect.REPLACE
    if reason in {
        DecisionReason.REPEAT,
        DecisionReason.NEGATION_STATEMENT,
        DecisionReason.FRAGMENT_META,
        DecisionReason.SEMANTIC_SMALLTALK,
        DecisionReason.SEMANTIC_META_FOLLOWUP,
        DecisionReason.CATALOG_UNKNOWN_BANK,
        DecisionReason.CATALOG_CONFLICTING_SLOTS,
    }:
        return ContextEffect.PRESERVE
    return ContextEffect.CLEAR


def next_structured_frame(
        decision: Decision, previous: RateIntent | None) -> RateIntent | None:
    """Apply the centralized outcome-driven lifecycle to a structured frame."""
    effect = frame_effect(decision.reason)
    if effect is ContextEffect.REPLACE:
        return decision.rate_intent
    if effect is ContextEffect.PRESERVE:
        return previous
    return None

class SessionStore:
    """Bounded, process-local state; replace with Redis for multi-instance deployment."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def get(self, requested_id: str | None) -> Session:
        now = time.time()
        with self._lock:
            self._evict(now)
            if requested_id and requested_id in self._sessions:
                session = self._sessions[requested_id]
                session.updated_at = now
                return session
            session = Session(uuid.uuid4().hex, [], "", now)
            self._sessions[session.session_id] = session
            return session

    def record(self, session: Session, question: str, answer: str,
               outcome: Outcome | None = None, handoff: bool = False) -> None:
        with self._lock:
            session.history.extend((
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ))
            session.history = session.history[-MAX_HISTORY_MESSAGES:]
            session.last_answer = answer
            session.last_outcome = outcome
            session.last_handoff = handoff
            session.updated_at = time.time()

    def _evict(self, now: float) -> None:
        stale = [sid for sid, value in self._sessions.items()
                 if now - value.updated_at > SESSION_TTL_SECONDS]
        for sid in stale:
            del self._sessions[sid]
        overflow = len(self._sessions) - MAX_SESSIONS
        if overflow > 0:
            oldest = sorted(self._sessions.values(), key=lambda value: value.updated_at)[:overflow]
            for value in oldest:
                del self._sessions[value.session_id]

sessions = SessionStore()

# Fast-path only credential disclosures or active access incidents; general PIN/CVV questions use semantic routing.
_SECRET_FAST_RE = re.compile(
    r"(?:\b(?:pin|cvv|cvc|otp)\b.{0,80}\b(?:zbulu|kompromet|vjedh|dha|ndava|tregova|"
    r"derg\w*|dërg\w*|kerk|doli|nuk funksion)|\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|"
    r"tregova|derg\w*|dërg\w*|kerk|doli|nuk funksion).{0,80}\b(?:pin|cvv|cvc|otp)\b)", re.I)

# Frozen grouped-train nearest-neighbour classifier; serving needs NumPy only.
_PROBE_PATH = Path(__file__).resolve().parents[1] / "handoff_probe.json"
_PROBE_DATA = json.loads(_PROBE_PATH.read_text(encoding="utf-8"))  # Exported classifier metadata and exemplars.
_PROBE_BYTES = zlib.decompress(base64.b64decode(_PROBE_DATA["vectors_zlib_b64"]))  # Compressed frozen vectors.
_PROBE_VECTORS = np.frombuffer(_PROBE_BYTES, dtype="<f4").reshape(_PROBE_DATA["shape"])  # Train embeddings.
_PROBE_LABELS = np.asarray(_PROBE_DATA["labels"], dtype=bool)  # Positive/negative class for each exemplar.
_HANDOFF_THRESHOLD = float(_PROBE_DATA["margin"])  # Train-tuned FP<=2% class-margin threshold.
if _PROBE_DATA["k"] != 1 or _PROBE_VECTORS.shape[1] != _PROBE_DATA["dimensions"]:
    raise RuntimeError("handoff_probe.json has invalid nearest-neighbour data")
if _PROBE_DATA.get("model") != EMBEDDING_MODEL_NAME or not _PROBE_DATA.get("source_sha256"):
    raise RuntimeError("handoff_probe.json is not bound to the active model/source corpus")

_ACCOUNT_ACTION_RE = re.compile(
    r"\b(?:gjendj\w*\s+(?:e\s+)?llogar\w*\s+sim\w*|"
    r"limit\w*\s+(?:i\s+|e\s+)?kart\w*\s+sim\w*|"
    r"mbyll\w*\s+(?:llogar|kart)\w*|bllok\w*\s+(?:llogar|kart)\w*)",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?355\s*)?(?:6[789]|0)\d(?:[\s-]?\d){6,8}(?!\d)")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)(?:\d[ -]?){8,18}\d(?!\d)")

def _redact_pii(text: str) -> tuple[str, bool]:
    redacted = _EMAIL_RE.sub("[email i fshehur]", text)
    redacted = _PHONE_RE.sub("[numër telefoni i fshehur]", redacted)
    redacted = _LONG_NUMBER_RE.sub("[numër i fshehur]", redacted)
    return redacted, redacted != text

def _is_repeat(text: str) -> bool:
    lowered = fold(text)
    albanian_repeat = any(term in lowered for term in (
        "perserite", "ma perserit", "perserit pergjigjen",
        "ma thuaj edhe nje here", "nuk degjova", "thuaje prap",
    ))
    return albanian_repeat or re.search(r"\brepeat\b", lowered) is not None


# ---- Small-talk / greeting handling -----------------------------------------
# Only fire when the whole message is small talk (greeting, how-are-you, thanks,
# farewell). If any substantive banking query words are present, we let the
# message through to normal handling. fold() strips e->e and ë->e and lowercases.
_SMALLTALK_PATTERNS = (
    # Pure greeting / farewell.
    r"^(?:pershendetje|përshëndetje|miredita|miremengjes|mirembrema|"
    r"tung(?:jatjeta)?|hello|hi|hej|alo|ckemi|cfare ka|lamtumire|"
    r"mirupafshim|naten e mire)(?:[.,!? ]*)$",
    # How are you.
    r"^(?:si je|si jeni|si po shkon|si jane gjerat|cfare ben|cfare po ben)(?:[.,!? ]*)$",
    # Thanks / you're welcome.
    r"^(?:faleminderit(?: shume)?|te falenderoj|thanks|thank you)(?:[.,!? ]*)$",
)
_SMALLTALK_WORDS = (
    "pershendetje", "miredita", "miremengjes", "mirembrema", "tung", "hello",
    "hi", "hej", "alo", "lamtumire", "mirupafshim", "faleminderit",
)
# Must NOT intercept even if small-talk word appears alongside real intent.
_SMALLTALK_QUERY_BLOCKLIST = (
    "komision", "norma", "interes", "tarif", "kredi", "depozit", "llogari",
    "rregullore", "rregullorja", "bank", "karte", "shlyerje", "neni",
)


def _is_smalltalk(text: str) -> bool:
    folded = fold(text).strip()
    if not folded:
        return False
    if any(word in folded for word in _SMALLTALK_QUERY_BLOCKLIST):
        return False
    return any(re.match(pattern, folded) for pattern in _SMALLTALK_PATTERNS)


# ---- Informational-query fast path ------------------------------------------
# The frozen incident probe (k=1 nearest-neighbour, margin threshold ~0.047)
# false-positives on short factual questions whose embedding lands near fraud
# exemplars (e.g. "Me thuaj bankat ne shqiperi" ~ "Po më ikin lekët nga banka",
# sim 0.68). These are lexically unambiguous informational banking questions,
# so they deterministically skip the probe. Any query carrying incident
# vocabulary still goes through the classifier unchanged.
_QUESTION_MARKER_RE = re.compile(
    r"\b(?:cilat|cila|cili|cilin|cfare|sa|kush|ku|kur|si|listo|lista|trego|thuaj|"
    r"rendit|pershkruaj)\b|me\s+thuaj|ma\s+thuaj|me\s+trego|a\s+ka\b",
    re.I,
)
_DOMAIN_MARKER_RE = re.compile(
    r"\b(?:bank|rregullore|komision|norm|tarif|kredi|debit|depozit|kart|neni|shlyerje|"
    r"interes|licenc|llogari|individ|biznes|mbikeqyr)\w*",
    re.I,
)
_INCIDENT_MARKER_RE = re.compile(
    r"\b(?:humb\w*|vjedh\w*|vidh\w*|bllok|pin|cvv|cvc|otp|kod|ikin|iken|dikush|mashtr|"
    r"raportoj|kartel|ime|time|mua)\b",
    re.I,
)


def _is_informational_banking_query(
        text: str, last_outcome: Outcome | None = None) -> bool:
    folded = fold(text)
    has_domain_marker = bool(_DOMAIN_MARKER_RE.search(folded))
    has_incident_marker = bool(_INCIDENT_MARKER_RE.search(folded))
    # [SUPERSEDED] The original informational check required both a question
    # marker and a domain marker, so a bare answer to a CLARIFY prompt reached
    # the incident probe:
    # return (
    #     bool(_QUESTION_MARKER_RE.search(folded))
    #     and bool(_DOMAIN_MARKER_RE.search(folded))
    #     and not _INCIDENT_MARKER_RE.search(folded)
    # )
    return (
        bool(_QUESTION_MARKER_RE.search(folded))
        and has_domain_marker
        and not has_incident_marker
    ) or (
        # A domain-bearing fragment immediately after CLARIFY answers the
        # disambiguation prompt; it is informational even without a question
        # word. Incident/credential vocabulary retains precedence and still
        # reaches the frozen incident probe below.
        last_outcome == Outcome.CLARIFY
        and has_domain_marker
        and not has_incident_marker
    )


# ---- Legal-advice fast path (explicit personal-application floor) ----------
# The frozen incident probe and retrieval have no notion of legal advice.  A
# caller who asks whether THEY should pay, whether THEY are liable, whether a
# specific penalty imposed on them is lawful, or what remedy they can pursue in
# their own case is asking for personalized legal advice — not a regulatory
# fact.  Anti-pro-drop note: Albanian carries person in the verb inflection
# (a duhet ta paguaj = "should I pay"), so this lexical floor is deliberately
# narrow and explicit.  It is the deterministic FAST PATH only; semantic
# personal-application deflection lives in the model prompt + the post-
# generation all-or-nothing scanner (api.py).  It must NOT fire on pure
# legal-INFORMATION (rights/obligations/deadlines stated generally, e.g. "a
# garanton BSH qe banka ime nuk mund te me mbyll llogarine?" or "a kam te drejte
# te marr nje kopje te kontrates sime?").
_LEGAL_ADVICE_EXPLICIT_RE = re.compile(
    r"\b(?:"
    r"me\s+keshill\w*|keshel|keshillim\w*|rekomand\w*|"
    r"ligjerisht|hapa?\w*\s+ligjor\w*|"
    r"pasojat\s+ligjor\w*|"
    r"(?:a\s+)?duhet\s+(?:te|ta)\s+paguaj\b|"
    r"(?:a\s+)?jam\s+pergjegjes\b|"
    r"(?:a\s+)?mund\s+(?:te|ta)\s+padis\w*|"
    r"mos\s+me\s+padis\w*|"
    r"(?:a\s+)?eshte\s+e\s+ligjshme\b|"
    r"demshperblim\w*"
    r")\b",
    re.I,
)


def _is_legal_advice_explicit(text: str) -> bool:
    return _LEGAL_ADVICE_EXPLICIT_RE.search(fold(text)) is not None


# ---- Negation-statement floor ----------------------------------------------
# "nuk kam karte" / "nuk kam llogari" / "nuk kam pyetje" are responses to a
# prior card/account/question, NOT an instruction to act on an account. They
# must never be escalated to a human (the router can misfire on "karte"/account
# words), so they deterministically fall through to a continue-helping
# response before the router or the incident probe can see them.
_NEGATION_STATEMENT_RE = re.compile(
    r"\bnuk\s+kam\s+(?:asnjë\s+)?(?:kart\w*|llogari\w*|pyetje(?:s)?)\b",
    re.I,
)


def _is_negation_statement(text: str) -> bool:
    return _NEGATION_STATEMENT_RE.search(fold(text)) is not None


# ---- Personal Credit Registry record capability boundary ------------------
_PERSONAL_RECORD_STRONG_RE = re.compile(
    r"\b(?:"
    r"ne\s+emrin\s+tim|"
    r"te\s+dhenat\s+e\s+mia|"
    r"raporti\s+im\s+i\s+kredimarresit|"
    r"a\s+figuroj|"
    r"a\s+kam\s+kredi\s+aktive|"
    r"a\s+kam\s+kredi\b.{0,80}\bne\s+emrin\s+tim|"
    r"a\s+nuk\s+kam\s+kredi|"
    r"nuk\s+kam\s+kredi\b[^?!.]{0,40}\bapo\s+jo"
    r")\b",
    re.I,
)
_PERSONAL_RECORD_CONTEXT_RE = re.compile(
    r"\b(?:per\s+mua|rreth\s+meje|informacionin\s+tim)\b",
    re.I,
)
_PERSONAL_RECORD_REGISTRY_RE = re.compile(
    r"\b(?:regjistri\s+i\s+kredive|kredi\s+aktive|kredi\s+problematike|"
    r"raport\s+kredimarresi|te\s+dhena\s+personale)\b",
    re.I,
)


def _is_personal_record_request(text: str) -> bool:
    folded = fold(text)
    return (
        _PERSONAL_RECORD_STRONG_RE.search(folded) is not None
        or (
            _PERSONAL_RECORD_CONTEXT_RE.search(folded) is not None
            and _PERSONAL_RECORD_REGISTRY_RE.search(folded) is not None
        )
    )


# ---- LLM turn-router seam ---------------------------------------------------
# Replaces the lexical smalltalk/account_action/clarify decision blocks with a
# single semantic intent classification (core/router.py). The router is OFF by
# default (env BOABOT_LLM_ROUTER=1 enables it) and falls back to the old lexical
# routing when disabled or unavailable, so offline behavior and tests are
# unchanged until explicitly enabled. Security gates (input_gate / secret / PII /
# repeat) and the deterministic legal-advice floor stay OUTSIDE this seam.
#
# Hypothetical/rights framing: an account-word in a question like "a garanton BSH
# qe banka ime nuk mund te me mbyll llogarine?" is a question about whether a rule
# exists, NOT a request to act on an account. The router labels such turns "answer".
# As a fail-closed backstop, the lexical account-action regex is re-checked on the
# answer path, but exempted (carved out) for this hypothetical/rights framing so
# genuine "close my account" requests still escalate and rights questions do not.
_HYPOTHETICAL_RIGHTS_RE = re.compile(
    r"\b(?:a\s+garanton|a\s+lejohet|a\s+ndalohet|a\s+ka\s+te\s+drejte|"
    r"a\s+mundet\s+banka|a\s+mund\s+banka|eshte\s+e\s+lejuar|"
    r"eshte\s+e\s+ndaluar|nuk\s+mund\s+te\s+me\b)\b",
    re.I,
)


def _is_hypothetical_rights(text: str) -> bool:
    return _HYPOTHETICAL_RIGHTS_RE.search(fold(text)) is not None


_CATALOG_SELECTOR_RE = re.compile(
    r"\b(?:cilat?|kush|listo|lista|rendit)\b|\b(?:me|ma)\s+(?:thuaj|trego)\b",
    re.I,
)
_CATALOG_PRESENCE_RE = re.compile(
    r"\b(?:opero(?:n|j)|vepro(?:n|j)|ekzist|gjend)\w*\b|"
    r"\b(?:ka|kane)\b|\bne\s+shqiperi\b",
    re.I,
)
_CATALOG_PRICE_RE = re.compile(r"\b(?:tarif|komision|interes|norm|penalitet)\w*\b", re.I)


def _is_catalog_speech(text: str) -> bool:
    """Identify Albanian requests for the commercial-bank catalog."""
    folded = fold(text)
    return (
        re.search(r"\bbank\w*\b", folded) is not None
        and re.search(r"\bshqiperi\w*\b", folded) is not None
        and _CATALOG_SELECTOR_RE.search(folded) is not None
        and _CATALOG_PRESENCE_RE.search(folded) is not None
        and _CATALOG_PRICE_RE.search(folded) is None
    )


def _catalog_message() -> str | None:
    names = bank_names()
    if not names:
        return None
    readable = [name.upper() if len(name) <= 3 else name.title() for name in names]
    return "Bankat tregtare në Shqipëri janë: " + ", ".join(readable) + "."


def _fallback_label(question: str) -> str:
    """Old lexical intent used when the router is off/unavailable."""
    if _is_catalog_speech(question):
        return "catalog"
    if _is_smalltalk(question):
        return "smalltalk"
    if _is_account_action(question):
        return "account_action"
    if is_ambiguous_card_maintenance(question):
        return "clarify"
    return "answer"


def _route_label(label: str, question: str, last_handoff: bool) -> Decision | None:
    """Map a router label to a terminal Decision; return None to fall through."""
    if label == "catalog":
        # Fail-closed: an LLM "catalog" label only short-circuits to the canned
        # bank list when the deterministic catalog vocabulary is actually
        # present. The router prompt gives "catalog" only one exemplar (the
        # bank-list ask), so a flash model over-generalizes it onto fee/tariff/
        # role questions that merely mention "bankë". Re-checking keeps a wrong
        # canned list from silently replacing a grounded RAG answer — the same
        # fail-closed pattern as the account_action branch below ("false
        # handoffs are the worst UX failure").
        if not _is_catalog_speech(question):
            return None
        message = _catalog_message()
        if message:
            return Decision(Outcome.ANSWER, message, question=question,
                            handoff=False, reason=DecisionReason.BANK_CATALOG_LIST)
        return None
    if label == "smalltalk":
        return Decision(Outcome.ANSWER, _smalltalk_reply(question),
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_SMALLTALK)
    if label == "out_of_domain":
        return Decision(Outcome.UNSUPPORTED, OUT_OF_DOMAIN_MESSAGE,
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_OUT_OF_DOMAIN)
    if label == "meta_followup":
        msg = META_FOLLOWUP_HANDOFF_MESSAGE if last_handoff else META_FOLLOWUP_MESSAGE
        return Decision(Outcome.ANSWER, msg, question=question,
                        handoff=last_handoff,
                        reason=DecisionReason.SEMANTIC_META_FOLLOWUP)
    if label == "legal_advice":
        return Decision(Outcome.UNSUPPORTED, LEGAL_ADVICE_MESSAGE,
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_LEGAL_ADVICE)
    if label == "account_action":
        # Fail-closed: an LLM "account_action" only escalates when the
        # deterministic account-action vocabulary is actually present. Vague or
        # negation turns ("nuk kam karte") must never be escalated to a human
        # on the router's word alone — false handoffs are the worst UX failure.
        if not _is_account_action(question):
            return None
        return Decision(Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE,
                        handoff=True, reason=DecisionReason.SEMANTIC_ACCOUNT_ACTION)
    if label in ("incident", "incident_handoff"):
        return Decision(Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE,
                        handoff=True, reason=DecisionReason.SEMANTIC_INCIDENT)
    if label == "clarify":
        # A generic "clarify" (confused / needs-disambiguation turn) asks the
        # user to restate — NOT the card-specific text. The card-debit/credit
        # disambiguation stays deterministic: it fires ONLY when
        # is_ambiguous_card_maintenance (kart + mirembajt, no debit/credit or
        # segment given) is actually true, so a router "clarify" misfire on
        # unrelated turns can no longer start an inescapable card script.
        msg = CARD_CLARIFY_MESSAGE if is_ambiguous_card_maintenance(question) \
            else CLARIFY_MESSAGE
        return Decision(Outcome.CLARIFY, msg, question=question,
                        reason=DecisionReason.SEMANTIC_CLARIFY)
    return None  # "answer" / unknown -> fall through to retrieval


def _classify_turn(question: str, last_outcome=None, last_handoff: bool = False):
    """Injectable seam: the router call. Tests monkeypatch this directly."""
    try:
        from .router import classify_turn as impl
        return impl(question, last_outcome, last_handoff)
    except Exception:
        return None


def _analyze_turn(question: str, history: list[dict[str, str]],
                  last_outcome=None, last_handoff: bool = False):
    """Injectable seam: the FUSED router call (Step 2b).

    Returns a router.TurnAnalysis (label + standalone rewritten query + legal
    flags) from a single model call, or None when disabled/off/unparseable so
    the caller falls back to the separate classify+rewrite pair. This is the
    latency fix: with the router ON, intent + rewrite + legal flags are decided
    in ONE call instead of classify_turn then rewrite().
    """
    try:
        from .router import analyze_turn as impl
        return impl(question, history, last_outcome, last_handoff)
    except Exception:
        return None


GREETING_MESSAGE = (
    "Përshëndetje! Si mund t'ju ndihmoj me rregulloret bankare shqiptare "
    "ose tarifat e bankave sot?"
)
HOW_ARE_YOU_MESSAGE = (
    "Jam mirë, faleminderit! Si mund t'ju ndihmoj me rregulloret ose "
    "tarifat bankare?"
)
THANKS_MESSAGE = (
    "Me kënaqësi! Nëse keni pyetje të tjera për rregulloret ose tarifat "
    "bankare, mos ngurroni të pyesni."
)
FAREWELL_MESSAGE = (
    "Mirupafshim! Ju lutem kthehuni nëse keni nevojë për më shumë informacion."
)


def _smalltalk_reply(text: str) -> str:
    folded = fold(text).strip()
    if any(pattern in folded for pattern in ("si je", "si jeni", "si po shkon",
                                             "si jane gjerat", "cfare ben")):
        return HOW_ARE_YOU_MESSAGE
    if any(word in folded for word in ("faleminderit", "te falenderoj",
                                       "thanks", "thank")):
        return THANKS_MESSAGE
    if any(word in folded for word in ("lamtumire", "mirupafshim", "naten")):
        return FAREWELL_MESSAGE
    return GREETING_MESSAGE


def is_ambiguous_card_maintenance(text: str) -> bool:
    folded = fold(text)
    if "kart" not in folded or "mirembajt" not in folded:
        return False
    card_types = sum(term in folded for term in ("debit", "kredit"))
    customer_segments = sum(term in folded for term in ("individ", "biznes"))
    return card_types != 1 or customer_segments != 1

def _encode_question(question: str) -> np.ndarray:
    """Sole callcenter embedding entry point: exactly one normalized encode per routed turn."""
    return np.asarray(model().encode([question], normalize_embeddings=True)[0], dtype=np.float32)


def _probe_score(query_embedding: np.ndarray) -> float | None:
    """Return the positive-minus-negative margin for a positive nearest neighbour."""
    similarities = _PROBE_VECTORS @ query_embedding
    if not _PROBE_LABELS[int(np.argmax(similarities))]:
        return None
    positive_similarity = float(np.max(similarities[_PROBE_LABELS]))
    negative_similarity = float(np.max(similarities[~_PROBE_LABELS]))
    return positive_similarity - negative_similarity


def _account_action_score(query_embedding: np.ndarray) -> float | None:
    """Compatibility hook for account-action policy telemetry.

    Account-action routing is deliberately lexical and fail-closed so serving
    has no hidden dependency on an exported training artifact.
    """
    del query_embedding
    return None


def _is_account_action(question: str) -> bool:
    """Identify explicit requests about a caller's own account or card."""
    return _ACCOUNT_ACTION_RE.search(question) is not None


_ENABLE = ("1", "true", "yes", "on")
_ACTIVE_INCIDENT_FOR_RATE_RE = re.compile(
    r"\b(?:humb\w*|vjedh\w*|vidh\w*|mashtr\w*|raportoj\w*|"
    r"(?:me|mua)\s+ikin\w*|dikush)\b",
    re.I,
)


def _structured_rate_enabled() -> bool:
    return os.environ.get("BOABOT_COMPARISON_STRUCTURED", "").strip().lower() in _ENABLE


def _fragment_meta_preflight(
        question: str, last_handoff: bool = False) -> Decision | None:
    """Expose the deterministic never-retrieve fragment/meta floor."""
    from .router import is_conversational_fragment, is_meta_help

    if not (is_conversational_fragment(question) or is_meta_help(question)):
        return None
    message = META_FOLLOWUP_HANDOFF_MESSAGE if last_handoff else META_FOLLOWUP_MESSAGE
    return Decision(
        Outcome.ANSWER, message, question=question,
        handoff=last_handoff, reason=DecisionReason.FRAGMENT_META,
    )


def _structured_rate_eligible(question: str) -> bool:
    """Cede account, incident, and ambiguous-card turns to higher policy floors."""
    return not (
        _is_account_action(question)
        or _ACTIVE_INCIDENT_FOR_RATE_RE.search(fold(question))
        or is_ambiguous_card_maintenance(question)
    )


def _structured_rate_decision(
        question: str, *, frame: RateIntent | None = None) -> Decision | None:
    """Injectable pre-LLM seam for exact closed-catalog rate requests."""
    if not _structured_rate_enabled() or not _structured_rate_eligible(question):
        return None
    from .comparison import (CATALOG_DECLINE_REASONS, _source_bank_labels,
                             merge_elliptical, parse_rate_intent_hybrid,
                             resolve_rate_rows)

    parsed = parse_rate_intent_hybrid(question)
    if parsed.status == "not_rate":
        if frame is not None:
            merged = merge_elliptical(question, frame)
            if merged is not None and resolve_rate_rows(merged):
                return Decision(
                    None, question=question,
                    reason=DecisionReason.CATALOG_EXACT_HIT,
                    rate_intent=merged,
                )
        return None
    if parsed.status == "unsupported":
        if parsed.reason not in CATALOG_DECLINE_REASONS:
            return None
        message = NO_EVIDENCE_MESSAGE
        reason = DecisionReason.CATALOG_CONFLICTING_SLOTS
        if parsed.reason == "unknown_bank":
            labels = ", ".join(_source_bank_labels())
            message = (
                f"Nuk e njoh këtë bankë. Kam të dhëna për: {labels}. "
                "Për cilën dëshironi të dini?"
            )
            reason = DecisionReason.CATALOG_UNKNOWN_BANK
        elif parsed.reason == "comparison_dimensions_missing":
            labels = {
                "currency": "monedha",
                "term_months": "afati",
                "amount_band": "shuma",
                "customer_segment": "segmenti (individë apo biznese)",
                "fee_event": "lloji i komisionit",
            }
            dimensions = [
                labels[item] for item in (
                    parsed.coverage.unresolved_qualifiers
                    if parsed.coverage is not None else ()
                ) if item in labels
            ]
            if len(dimensions) > 1:
                requested = ", ".join(dimensions[:-1]) + f" dhe {dimensions[-1]}"
            else:
                requested = dimensions[0] if dimensions else "dimensionet e krahasimit"
            message = f"Për ta krahasuar saktë, më duhet {requested}."
            reason = DecisionReason.COMPARISON_DIMENSIONS_MISSING
        return Decision(
            Outcome.CLARIFY, message, question=question,
            reason=reason,
            rate_intent=parsed.intent,
        )
    return Decision(
        None, question=question, reason=DecisionReason.CATALOG_EXACT_HIT,
        rate_intent=parsed.intent,
    )

def decide(question: str, last_answer: str, history: list[dict[str, str]],
           last_outcome: Outcome | None = None, last_handoff: bool = False,
           last_structured_frame: RateIntent | None = None) -> Decision:
    """Route a caller turn before it can reach retrieval or the model."""
    gate = input_gate(question)
    if not gate.allowed:
        return Decision(
            Outcome.UNSUPPORTED, UNSAFE_INPUT_MESSAGE,
            reason=DecisionReason.UNSAFE_INPUT,
        )

    clean_question, pii_redacted = _redact_pii(question)
    # Step 2a: restore known ç/ë diacritics on lossily-typed tokens so the
    # embedding + retrieval + generation all see the canonical Albanian form.
    # Lexicon-bounded (no guessing); folding is rotation-invariant so the
    # deterministic lexical gates below are unaffected by the restoration.
    clean_question = restore_diacritics(clean_question)
    if _SECRET_FAST_RE.search(clean_question):
        # The raw credential-bearing text is deliberately not copied into the
        # decision/session history; expose that redaction happened in telemetry.
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            pii_redacted=True, reason=DecisionReason.CREDENTIAL_DISCLOSURE,
        )
    if pii_redacted:
        return Decision(
            Outcome.HANDOFF, PII_HANDOFF_MESSAGE, question=clean_question,
            handoff=True, pii_redacted=True, reason=DecisionReason.PII_DETECTED,
        )

    if _is_repeat(clean_question):
        return Decision(
            Outcome.REPEAT, last_answer or REPEAT_MESSAGE,
            handoff=last_handoff, reason=DecisionReason.REPEAT,
        )

    # ---- Legal-advice floor (deterministic, kept BEFORE the router) ----
    if _is_legal_advice_explicit(clean_question):
        return Decision(
            Outcome.UNSUPPORTED, LEGAL_ADVICE_MESSAGE,
            question=clean_question, handoff=False,
            reason=DecisionReason.LEGAL_ADVICE_EXPLICIT,
        )

    # ---- Negation-statement floor (deterministic, BEFORE the router) ----
    # "nuk kam karte" / "nuk kam llogari" answer a prior card/account question
    # without any action request; never escalate them to a human.
    if _is_negation_statement(clean_question):
        return Decision(
            Outcome.ANSWER, META_FOLLOWUP_MESSAGE,
            question=clean_question, handoff=last_handoff,
            reason=DecisionReason.NEGATION_STATEMENT,
        )

    # ---- Fragment/meta floor (deterministic, NEVER retrieves) ----
    # [SUPERSEDED] This floor previously lived only inside router.classify_turn /
    # analyze_turn, which made its pre-retrieval position implicit. The router
    # checks remain for compatibility, but this exposed preflight is authoritative.
    fragment_meta = _fragment_meta_preflight(clean_question, last_handoff)
    if fragment_meta is not None:
        return fragment_meta

    # ---- Personal-record capability boundary (deterministic, BEFORE router) ----
    # Account actions and incidents retain their higher-priority handling.
    if (_structured_rate_eligible(clean_question)
            and _is_personal_record_request(clean_question)):
        return Decision(
            Outcome.ANSWER, PERSONAL_RECORD_CAPABILITY_MESSAGE,
            question=clean_question, handoff=False,
            reason=DecisionReason.PERSONAL_RECORD_CAPABILITY_BOUNDARY,
        )

    # ---- Typed structured-rate seam (opt-in, BEFORE every LLM/vector call) ----
    # Account actions, active incidents, and ambiguous-card turns explicitly
    # cede to their existing higher-priority routing/backstop paths.
    if _structured_rate_enabled() and _structured_rate_eligible(clean_question):
        structured = _structured_rate_decision(
            clean_question, frame=last_structured_frame,
        )
        if structured is not None:
            return structured

    # ---- LLM turn-router (semantic intent, fused when ON) ----
    # Step 2b: when the router is enabled, ONE fused call returns the intent
    # label AND the standalone rewritten query (AND legal flags) together.
    # That replaces the old separate classify_turn(...) then rewrite() pair on
    # the happy path — a single model call for intent+rewrite+legal. When
    # disabled/unavailable we fall back to classify_turn then, downstream, to
    # needs_rewrite()/rewrite() in api.py. The security gates above and the
    # legal-advice floor stay deterministic and OUTSIDE this seam.
    # [SUPERSEDED] Small-talk handled semantically by the router label
    #              "smalltalk". Old lexical form:
    #   if _is_smalltalk(clean_question):
    #       return Decision(Outcome.ANSWER, _smalltalk_reply(clean_question),
    #                       question=clean_question, handoff=False, reason="smalltalk")
    # [SUPERSEDED] Account action handled semantically by the router label
    #              "account_action". Old lexical form:
    #   if _is_account_action(clean_question):
    #       return Decision(Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE, handoff=True,
    #                       reason="account_action")
    fused_rewrite = None
    fused_legal = None
    analysis = _analyze_turn(clean_question, history, last_outcome, last_handoff)
    if analysis is not None and getattr(analysis, "label", None):
        label = analysis.label
        fused_rewrite = getattr(analysis, "rewritten_query", None)
        fused_legal = getattr(analysis, "legal_flags", None)
    else:
        label = _classify_turn(clean_question, last_outcome, last_handoff)
        if label is None:
            label = _fallback_label(clean_question)
    routed = _route_label(label, clean_question, last_handoff)
    if routed is not None:
        return routed

    # ---- Answer path: fall through to retrieval with fail-closed backstops ----
    # Lexical account-action backstop, carved out for hypothetical/rights framing
    # (e.g. "a garanton BSH qe banka ime nuk mund te me mbyll llogarine?" is a
    # question about whether a rule exists, not a request to act on the account).
    if _is_account_action(clean_question) and not _is_hypothetical_rights(clean_question):
        return Decision(
            Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE, handoff=True,
            reason=DecisionReason.ACCOUNT_ACTION_BACKSTOP,
        )

    query_embedding = _encode_question(clean_question)
    incident_score = None
    if not _is_informational_banking_query(clean_question, last_outcome):
        # Deterministic backstop: the frozen incident classifier still runs on
        # non-informational turns so an LLM-missed incident escalates. Incident
        # vocabulary is routed through the classifier unchanged.
        incident_score = _probe_score(query_embedding)
    if incident_score is not None and incident_score >= _HANDOFF_THRESHOLD:
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            query_embedding=query_embedding, handoff_score=incident_score,
            reason=DecisionReason.INCIDENT_BACKSTOP,
        )
    account_score = _account_action_score(query_embedding)
    # [SUPERSEDED] Ambiguous card-maintenance handled by the router label
    #              "clarify". Old lexical form:
    #   if is_ambiguous_card_maintenance(clean_question):
    #       return Decision(Outcome.CLARIFY, CARD_CLARIFY_MESSAGE,
    #                       question=clean_question, query_embedding=query_embedding,
    #                       handoff_score=account_score, reason="disambiguation")
    return Decision(None, question=clean_question, query_embedding=query_embedding,
                    handoff_score=account_score,
                    reason=DecisionReason.DENSE_RETRIEVAL,
                    rewritten_query=fused_rewrite, legal_flags=fused_legal)
