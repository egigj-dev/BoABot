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
from .institutions import institution_catalog_message
from .text_norm import fold, restore_diacritics
from .trust import (INSTITUTION_REGISTER_SOURCE, NO_EVIDENCE_MESSAGE,
                    UNSAFE_INPUT_MESSAGE, bank_names, input_gate)

if TYPE_CHECKING:
    from .comparison import RateIntent, ResponsePlan

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
TRANSFER_FEE_CLARIFY_MESSAGE = (
    "Për individë apo biznese? Dhe bëhet fjalë për transfertë brenda "
    "Shqipërisë apo jashtë vendit?"
)
TRANSFER_FEE_SEGMENT_CLARIFY_MESSAGE = "Për individë apo biznese?"
TRANSFER_FEE_SCOPE_CLARIFY_MESSAGE = (
    "Bëhet fjalë për transfertë brenda Shqipërisë apo jashtë vendit?"
)
TRANSFER_CONTEXT_MESSAGE = "Në rregull. Çfarë dëshironi të dini për transfertën?"
TRANSFER_FEE_BANK_CLARIFY_MESSAGE = (
    "Dëshironi një bankë specifike apo krahasim mes bankave?"
)
TRANSFER_FEE_UNAVAILABLE_MESSAGE = (
    "Nuk kam të dhëna të publikuara që konfirmojnë shumën konkrete të kësaj "
    "tarife. Materialet që kam për këtë temë trajtojnë transparencën e "
    "tarifave, jo shumën konkrete të tyre."
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
    # Wired by the plan-P2 fidelity-drop work when rag.ask surfaces drops.
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
    MATURITY_BAND_REQUIRED = "maturity_band_required"
    TRANSFER_FEE_DIMENSIONS_MISSING = "transfer_fee_dimensions_missing"
    TRANSFER_CONTEXT_ESTABLISHED = "transfer_context_established"
    TRANSFER_FEE_PRICE_UNAVAILABLE = "transfer_fee_price_unavailable"
    STRUCTURED_PLANNER_CLARIFY = "structured_planner_clarify"
    STRUCTURED_ANSWER_AND_FOLLOW_UP = "structured_answer_and_follow_up"
    PRODUCT_CAPABILITY = "product_capability"
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
    response_plan: ResponsePlan | None = None

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
    if reason in {
        DecisionReason.CATALOG_EXACT_HIT,
        DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
        DecisionReason.TRANSFER_CONTEXT_ESTABLISHED,
        DecisionReason.STRUCTURED_PLANNER_CLARIFY,
    }:
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


def _frame_resolves(intent: RateIntent | None) -> bool:
    """True when the intent can actually resolve evidence rows.

    Step 18-BX (b): a carried frame must be one the next turn can bind onto
    without dying. An unresolvable clarify intent (missing_key product with no
    rows) would, if adopted, turn every subsequent slot reply into a terminal
    refusal. Fail-open on any resolution error (keep the frame) rather than
    dropping context over a transient failure.
    """
    if intent is None:
        return False
    from .comparison import structured_rate_hits
    try:
        return bool(structured_rate_hits(intent, k=1))
    except Exception:
        return True


def next_structured_frame(
        decision: Decision, previous: RateIntent | None) -> RateIntent | None:
    """Apply the centralized outcome-driven lifecycle to a structured frame."""
    effect = frame_effect(decision.reason)
    if effect is ContextEffect.REPLACE:
        candidate = decision.rate_intent
        if candidate is None:
            return None
        # Step 18-BX (b): adopt the REPLACE frame only when it can resolve
        # rows, so an unresolvable clarify intent never becomes a carried
        # frame that later slot replies bind onto (the AE dead end). TRANSFER
        # frames resolve through the transfer-fee engine (not the rate tables)
        # and are exempt by reason — their lifecycle is unchanged. A reject
        # keeps the PREVIOUS frame on a concrete answer (real context) and
        # drops it on a clarify (the clarify supersedes).
        if (decision.reason in {
                DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
                DecisionReason.TRANSFER_CONTEXT_ESTABLISHED,
        } or _frame_resolves(candidate)):
            return candidate
        if decision.reason is DecisionReason.CATALOG_EXACT_HIT:
            return previous
        return None
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
            # Honor an explicit client session id (the web UI adopts the
            # server-sent id; API callers that pre-generate an id must be able
            # to reuse it across turns — otherwise last_answer / the structured
            # frame reset on EVERY turn). Fall back to a fresh uuid for None.
            session = Session(
                requested_id if requested_id else uuid.uuid4().hex, [], "", now,
            )
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
    r"tregova|derg\w*|dërg\w*|kerk|doli|nuk funksion).{0,80}\b(?:pin|cvv|cvc|otp)\b|"
    r"\b(?:pin|cvv|cvc|otp|password|fjalëkalim\w*)\b.{0,30}(?::|=|\b(?:eshte|është)\b)\s*[A-Za-z0-9._-]{3,64}\b)", re.I)

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
_TRANSACTION_ACTION_RE = re.compile(
    r"\b(?:anulo\w*|ndrysho\w*|ndalo\w*)\s+(?:te\s+)?"
    r"(?:transfert\w*|pages\w*)\b", re.IGNORECASE,
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
    "hi", "hej", "alo", "lamtumire", "mirupafshim", "faleminderit", "thanks",
    "ckemi",
)
# Multi-word small-talk phrases (folded). Kept apart from _SMALLTALK_WORDS so a
# single bare word is never matched mid-phrase; the sequence matcher below
# prefers the longer phrase first (operation order is by length).
_SMALLTALK_PHRASES = (
    "si je", "si jeni", "si po shkon", "si jane gjerat",
    "cfare ka", "cfare ben", "cfare po ben",
    "naten e mire", "te falenderoj", "faleminderit shume",
    "thank you", "tungjatjeta",
)
_SMALLTALK_UNITS = tuple(sorted(_SMALLTALK_WORDS + _SMALLTALK_PHRASES,
                                key=len, reverse=True))
_SMALLTALK_SEQ_RE = re.compile(
    r"^(?:" + "|".join(re.escape(u) for u in _SMALLTALK_UNITS) + r")"
    r"(?:[\s,;.!?–]+(?:" + "|".join(re.escape(u) for u in _SMALLTALK_UNITS) + r"))*"
    r"[\s,;.!?–]*$",
    re.I,
)
# Must NOT intercept if small-talk appears alongside real intent.
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
    # Pure single-clause forms are still matched by the anchored patterns.
    if any(re.match(pattern, folded) for pattern in _SMALLTALK_PATTERNS):
        return True
    # Combined/elliptical greetings ("pershendetje si je", "pershendetje,
    # si po shkon?") are a sequence of small-talk units separated by
    # punctuation or whitespace. The low-risk default start (no LLM router)
    # answered ONLY single-clause forms; everything else fell through to
    # retrieval and refused with NO_EVIDENCE_MESSAGE. Accepting any run of
    # pure small-talk units makes the flag-less default behave like the
    # router-on mode for social turns.
    return _SMALLTALK_SEQ_RE.fullmatch(folded) is not None


# ---- Bare courtesy fragments ("te lutem", "ju lutem", "lutem" alone) --------
# Distinct from the smalltalk set: these are polite standalone courtesies that
# carry no content (not thanks, not a question). MUST be anchored to the bare
# form ONLY — "ju lutem" is also a common polite OPENER for real banking turns
# ("ju lutem, sa kushton transferta brenda vendit?"), so a bare courtesy must
# never swallow a follow-up clause.
_COURTESY_RE = re.compile(
    r"^(?:te\s+lutem|ju\s+lutem|ju\s+we|lutem|prit)[.,!? ]*$",
    re.I,
)
COURTESY_MESSAGE = (
    "Me kënaqësi! Nëse keni ndonjë pyetje tjetër për rregulloret ose tarifat "
    "bankare, më thuajeni."
)


def _is_bare_courtesy(text: str) -> bool:
    folded = fold(text).strip()
    if not folded:
        return False
    return _COURTESY_RE.fullmatch(folded) is not None


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
_RATE_FEE_TOPIC_RE = re.compile(
    r"\b(?:komision|tarif|norm|interes|penalitet|mirembajt)\w*",
    re.I,
)
_BARE_NP_SLOT_RE = re.compile(
    r"\b(?:kart|debit|kredi|depozit|llogari|individ|person\w*\s+fizik|"
    r"biznes|person\w*\s+juridik)\w*",
    re.I,
)
_BARE_NP_ACTION_RE = re.compile(
    r"\b(?:humb|vjedh|mashtr|bll?oko|mbyll)\w*",
    re.I,
)


def _last_user_turn(history: list[dict[str, str]]) -> str:
    for item in reversed(history):
        if item.get("role") == "user":
            return item.get("content", "")
    return ""


def _is_bare_np_rate_continuation(
        text: str, last_outcome: Outcome | None,
        history: list[dict[str, str]], frame: RateIntent | None) -> bool:
    """Recognize slot-only answers to a preceding rate/fee turn."""
    if last_outcome not in {Outcome.CLARIFY, Outcome.ANSWER}:
        return False
    folded = fold(text)
    if (_QUESTION_MARKER_RE.search(folded)
            or _BARE_NP_ACTION_RE.search(folded)):
        return False
    has_slot = _BARE_NP_SLOT_RE.search(folded) is not None
    if not has_slot:
        has_slot = any(
            re.search(rf"\b{re.escape(name)}\b", folded)
            for name in bank_names()
        )
    if not has_slot:
        return False
    # Direct policy callers historically supplied only last_outcome. Preserve
    # that seam; real sessions always carry the preceding user turn below.
    if last_outcome is Outcome.CLARIFY and not history:
        return True
    if (frame is not None
            and frame.metric in {"interest_rate", "fee", "penalty"}):
        return True
    return _RATE_FEE_TOPIC_RE.search(fold(_last_user_turn(history))) is not None


def _is_informational_banking_query(
        text: str, last_outcome: Outcome | None = None,
        history: list[dict[str, str]] | None = None,
        frame: RateIntent | None = None) -> bool:
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
    benign_transfer = (
        bool(_TRANSFER_SERVICE_RE.search(folded) or _TRANSFER_SEND_RE.search(folded))
        and not has_incident_marker
        and not _ACTIVE_INCIDENT_FOR_RATE_RE.search(folded)
    )
    return (
        bool(_QUESTION_MARKER_RE.search(folded))
        and has_domain_marker
        and not has_incident_marker
    ) or benign_transfer or (
        _is_bare_np_rate_continuation(
            text, last_outcome, history or [], frame,
        )
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
    r"\bnuk\s+kam\s+(?:asnjë\s+)?(?:kart\w*|llogari\w*|pyetje(?:s)?|kredi\w*)\b",
    re.I,
)
_CREDIT_NEGATION_INTERROGATIVE_RE = re.compile(
    r"(?:^|[?!.]\s*)a\s+nuk\s+kam\s+(?:asnje\s+)?kredi\w*\b|"
    r"\bnuk\s+kam\s+(?:asnje\s+)?kredi\w*\b.{0,40}\bapo\s+jo\b",
    re.I,
)


def _is_negation_statement(text: str) -> bool:
    folded = fold(text)
    if _CREDIT_NEGATION_INTERROGATIVE_RE.search(folded):
        return False
    return _NEGATION_STATEMENT_RE.search(folded) is not None


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
    r"\b(?:a\s+garanton|a\s+lejohet|a\s+ndalohet|a\s+(?:ka|kam|keni)\s+te\s+drejte|"
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


# ---- Product-capability statement (fix 2026-08-30) --------------------------
# "cfare produktesh ofron secila prej bankave?" was answered with a regulatory
# wall of text. The capability statement is the concise deterministic reply:
# banks offer product categories; the caller picks a category/bank for real
# figures. Fires ONLY for a bare capability ask — a price/rate term or a
# concrete product word (kredi/depozite/karte) routes to the normal path.
_PRODUCT_CAPABILITY_RE = re.compile(
    r"\b(?:cfar\w*\s+(?:produkt\w*|sh(?:ë|e)rbim\w*)|"
    r"cilat?\s+produkt\w*|cilet\s+produkt\w*)\b",
    re.I,
)
_PRODUCT_CAPABILITY_OFFER_RE = re.compile(r"\b(?:ofron|ofrojne|ofrojn|japin)\b", re.I)
# The subject of a capability ask is the banks themselves — literally
# ("bankat", "secila prej bankave") or a deictic plural pronoun referring to
# the banks just listed in the catalog turn ("ato", "këto", "tyre").
_PRODUCT_CAPABILITY_SUBJECT_RE = re.compile(
    r"\bbank\w*\b|\b(?:ato|tyre|këto|keto)\b", re.I,
)
_PRODUCT_CAPABILITY_EXCLUDE_RE = re.compile(
    r"\b(?:tarif\w*|komision\w*|interes\w*|norm\w*|penalitet\w*|"
    r"kredi\w*|depozit\w*|kart\w*|llogari\w*)\b",
    re.I,
)


def _is_product_capability_speech(text: str) -> bool:
    folded = fold(text)
    # [SUPERSEDED] The subject used to require a literal "bank*" word, which
    # missed the deictic follow-up "cfare produktesh ofrojne ato?" (ato = the
    # banks just listed, no bank noun in the turn). The subject is now a bank
    # word OR a deictic plural pronoun; the product+offer+exclusion gates
    # below still bind the intent tight.
    return (
        _PRODUCT_CAPABILITY_SUBJECT_RE.search(folded) is not None
        and _PRODUCT_CAPABILITY_RE.search(folded) is not None
        and _PRODUCT_CAPABILITY_OFFER_RE.search(folded) is not None
        and _PRODUCT_CAPABILITY_EXCLUDE_RE.search(folded) is None
    )


def _product_capability_message() -> str:
    return (
        "Bankat tregtare ofrojnë një gamë produktesh e shërbimesh: kredi "
        "(konsumatore dhe për shtëpi), depozita, karta debiti e krediti, "
        "komisione dhe shërbime pagesash. Për të dhëna konkrete, më tregoni "
        "kategorinë (p.sh. \"kredi konsumatore\" ose \"karta debiti\") ose "
        "bankën, dhe do t'ju jap tarifat ose normat përkatëse."
    )


def _catalog_message() -> str:
    return institution_catalog_message()


def _fallback_label(question: str) -> str:
    """Old lexical intent used when the router is off/unavailable."""
    if _is_catalog_speech(question):
        return "catalog"
    if _is_smalltalk(question):
        return "smalltalk"
    if _is_bare_courtesy(question):
        return "meta_followup"
    if _is_account_action(question):
        return "account_action"
    if is_ambiguous_card_maintenance(question):
        return "clarify"
    return "answer"


_GENERAL_UNKNOWN_TRANSFER_RE = re.compile(
    r"(?:\bk[eë]t[eë]\s+lloj\s+(?:transfer\w*|pages\w*)\b|"
    r"\bklient\w*\b.{0,50}\bnuk\s+(?:e\s+)?njeh\b|"
    r"\bsi\s+trajtohen?\b.{0,60}\b(?:transfer\w*|pages\w*)\b|"
    r"\b(?:transfer\w*|pages\w*)\b.{0,50}\b(?:t[eë])\s+panjohur\w*\b|"
    r"\b[çc]far[eë]\s+duhet\s+t[eë]\s+b[eë]j[eë]\s+nj[eë]\s+klient\w*\b.{0,60}\b(?:nuk\s+njeh|panjohur)\b)",
    re.I,
)

def _is_general_unknown_transfer(question: str) -> bool:
    return _GENERAL_UNKNOWN_TRANSFER_RE.search(fold(question)) is not None


def _route_label(
        label: str, question: str, last_handoff: bool,
        trace_flags: frozenset[DecisionEvent] = frozenset(),
        ) -> Decision | None:
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
                            handoff=False, reason=DecisionReason.BANK_CATALOG_LIST,
                            trace_flags=trace_flags)
        return None
    if label == "smalltalk":
        return Decision(Outcome.ANSWER, _smalltalk_reply(question),
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_SMALLTALK,
                        trace_flags=trace_flags)
    if label == "out_of_domain":
        return Decision(Outcome.UNSUPPORTED, OUT_OF_DOMAIN_MESSAGE,
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_OUT_OF_DOMAIN,
                        trace_flags=trace_flags)
    if label == "meta_followup":
        msg = META_FOLLOWUP_HANDOFF_MESSAGE if last_handoff else META_FOLLOWUP_MESSAGE
        return Decision(Outcome.ANSWER, msg, question=question,
                        handoff=last_handoff,
                        reason=DecisionReason.SEMANTIC_META_FOLLOWUP,
                        trace_flags=trace_flags)
    if label == "legal_advice":
        return Decision(Outcome.UNSUPPORTED, LEGAL_ADVICE_MESSAGE,
                        question=question, handoff=False,
                        reason=DecisionReason.SEMANTIC_LEGAL_ADVICE,
                        trace_flags=trace_flags)
    if label == "account_action":
        # Fail-closed: an LLM "account_action" only escalates when the
        # deterministic account-action vocabulary is actually present. Vague or
        # negation turns ("nuk kam karte") must never be escalated to a human
        # on the router's word alone — false handoffs are the worst UX failure.
        if not _is_account_action(question):
            return None
        return Decision(Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE,
                        handoff=True, reason=DecisionReason.SEMANTIC_ACCOUNT_ACTION,
                        trace_flags=trace_flags)
    if label in ("incident", "incident_handoff"):
        if (_is_general_unknown_transfer(question)
                or not _has_positive_incident_evidence(question)):
            return None
        return Decision(Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE,
                        handoff=True, reason=DecisionReason.SEMANTIC_INCIDENT,
                        trace_flags=trace_flags)
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
                        reason=DecisionReason.SEMANTIC_CLARIFY,
                        trace_flags=trace_flags)
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
    r"(?:me|mua)\s+ikin\w*|dikush)\b|"
    r"\b(?:transfert\w*|pages\w*)\b.{0,40}\bnuk\s+(?:e\s+)?njoh\b|"
    r"\bnuk\s+(?:e\s+)?njoh(?:\s+(?:kete|këtë))?\s+(?:transfert\w*|pages\w*)\b|"
    r"\bnuk\s+e\s+kam\s+ber\w*\s+(?:une\s+)?(?:kete|këtë)?\s*(?:transfert\w*|pages\w*)\b|"
    r"\bkush\s+e\s+b[eë]ri\w*\b.{0,40}\b(?:transfert\w*|pages\w*)\b",
    re.I,
)



_POSITIVE_INCIDENT_EVIDENCE_RE = re.compile(
    _ACTIVE_INCIDENT_FOR_RATE_RE.pattern +
    r"|\b(?:me|mua)\s+(?:jane\s+)?marr\w*\s+(?:para|lek\w*)\b|"
    r"\b(?:para|lek\w*)\s+(?:me|mua)\s+(?:jane\s+)?marr\w*\b|"
    r"\bkush\s+e\s+beri\w*\b.{0,50}\b(?:pages\w*|transfert\w*)\b",
    re.I,
)


def _has_positive_incident_evidence(text: str) -> bool:
    return _POSITIVE_INCIDENT_EVIDENCE_RE.search(fold(text)) is not None


def _incident_context_has_positive_evidence(
        text: str, history: list[dict[str, str]],
        last_outcome: Outcome | None, last_handoff: bool) -> bool:
    if _has_positive_incident_evidence(text):
        return True
    if not last_handoff or last_outcome is not Outcome.HANDOFF:
        return False
    return any(
        item.get("role") == "user"
        and _has_positive_incident_evidence(item.get("content", ""))
        for item in reversed(history[-4:])
    )


_TRANSFER_SERVICE_RE = re.compile(r"\b(?:transfert\w*|transfer\w*)\b", re.I)
_TRANSFER_SEND_RE = re.compile(r"\b(?:dergoj|derguar|dergim\w*|cu|coj)\b", re.I)
_TRANSFER_PRICE_RE = re.compile(
    r"\b(?:sa\s+(?:me\s+)?(?:kushton|eshte|mban)|cilat?\s+jane|cfar\w*)\b|"
    r"\b(?:tarif|komision|kosto)\w*\b", re.I,
)
_TRANSFER_REGULATORY_RE = re.compile(
    r"\b(?:rregull\w*|publik\w*|transparenc\w*|detyrim\w*)\b", re.I,
)
_TRANSFER_PROCEDURE_RE = re.compile(
    r"\b(?:si\s+(?:mund|behet)|cfare\s+duhet|procedur\w*|proces\w*|dokument\w*)\b",
    re.I,
)
_TRANSFER_OTHER_PRODUCT_RE = re.compile(
    r"\b(?:kart\w*|kredi\w*|depozit\w*|llogari\w*)\b", re.I,
)
_TRANSFER_DOMESTIC_RE = re.compile(
    r"\bbrenda(?:\s+(?:vendit|shqiperise|shqipnise))?\b", re.I,
)
_TRANSFER_INTERNATIONAL_RE = re.compile(
    r"\b(?:jashte(?:\s+(?:vendit|shqiperise|shqipnise))?|nderkombetar\w*)\b", re.I,
)
_TRANSFER_COMPARISON_RE = re.compile(
    r"\b(?:krahasim\w*\s+mes\s+bank\w*|krahaso\w*)\b", re.I,
)
_TRANSFER_GEOGRAPHY_COMPARISON_RE = re.compile(
    r"\b(?:krahas\w*|ndryshon\w*|me\s+e\s+lire)\b", re.I,
)
_TRANSFER_DETAIL_FRAGMENT_RE = re.compile(
    r"\b\d[\d .,'’]*\s*(?:euro|eur|usd|dollar\w*|lek\w*)\b", re.I,
)


def _explicit_transfer_scope(folded: str) -> tuple[str | None, bool]:
    """Return explicit scope and whether both scopes form a real conflict."""
    domestic = _TRANSFER_DOMESTIC_RE.search(folded) is not None
    international = _TRANSFER_INTERNATIONAL_RE.search(folded) is not None
    if not (domestic and international):
        return ("domestic" if domestic else "international" if international else None), False
    if _TRANSFER_GEOGRAPHY_COMPARISON_RE.search(folded):
        return None, False
    if (re.search(r"\b(?:jo|nuk\s+eshte)\s+brenda\b.*\bjashte\b", folded)
            or re.search(r"^brenda\b.{0,12}\bjo\b.{0,12}\bjashte\b", folded)):
        return "international", False
    if (re.search(r"\b(?:jo|nuk\s+eshte)\s+jashte\b.*\bbrenda\b", folded)
            or re.search(r"\b(?:ne\s+fakt\s+)?brenda\b.{0,12}\bjo\b.{0,12}\bjashte\b", folded)
            or re.search(r"\bmendova\s+jashte\b.*\bne\s+fakt\s+brenda\b", folded)):
        return "domestic", False
    return None, True


def _transfer_clarify_message(segment: str | None, scope: str | None) -> str:
    if segment is None and scope is None:
        return TRANSFER_FEE_CLARIFY_MESSAGE
    if segment is None:
        return TRANSFER_FEE_SEGMENT_CLARIFY_MESSAGE
    return TRANSFER_FEE_SCOPE_CLARIFY_MESSAGE


def _transfer_fee_decision(
        question: str, frame: RateIntent | None = None) -> Decision | None:
    """Resolve transfer-fee amount asks before any router, embedding, or RAG call."""
    from .comparison import (CUSTOMER_SEGMENT_TERMS, RateIntent,
                             _conservative_value, _named_banks)

    folded = fold(question)
    inherited = frame if frame is not None and frame.family == "bank_transfer" else None
    explicit_service = bool(
        _TRANSFER_SERVICE_RE.search(folded)
        or (_TRANSFER_SEND_RE.search(folded)
            and re.search(r"\b(?:para|euro|lek\w*)\b", folded))
    )
    explicit_segment = _conservative_value(folded, CUSTOMER_SEGMENT_TERMS)
    explicit_scope, conflicting_scope = _explicit_transfer_scope(folded)
    explicit_domestic = explicit_scope == "domestic"
    explicit_international = explicit_scope == "international"
    explicit_comparison = _TRANSFER_COMPARISON_RE.search(folded) is not None
    candidate_banks, bank_spans = _named_banks(folded)
    if conflicting_scope:
        return Decision(
            Outcome.CLARIFY, TRANSFER_FEE_CLARIFY_MESSAGE, question=question,
            reason=DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
            rate_intent=RateIntent(
                bank_scope="named" if candidate_banks else "missing",
                banks=candidate_banks, product=None, metric="fee",
                fee_event=None, value_type=None, term_months=None,
                amount_band=None, breadth="leaf", family="bank_transfer",
                customer_segment=explicit_segment,
            ),
            trace_flags=frozenset({DecisionEvent.structured_lookup}),
        )
    bank_residue = folded
    for start, end in reversed(bank_spans):
        bank_residue = bank_residue[:start] + " " + bank_residue[end:]
    residue_words = re.findall(r"[^\W_]+", bank_residue, flags=re.UNICODE)
    bank_only_followup = bool(candidate_banks) and all(
        word in {"po", "per", "te", "tek", "banka", "banken"}
        for word in residue_words
    )
    explicit_price = _TRANSFER_PRICE_RE.search(folded) is not None
    detail_followup = bool(
        inherited is not None and _TRANSFER_DETAIL_FRAGMENT_RE.search(folded)
    )
    if (inherited is not None and not explicit_service and not explicit_price
            and explicit_segment is None and not explicit_domestic
            and not explicit_international and not explicit_comparison
            and not bank_only_followup and not detail_followup):
        return None
    if (inherited is not None and not explicit_service
            and _TRANSFER_OTHER_PRODUCT_RE.search(folded)):
        return None
    if inherited is None:
        if not explicit_service:
            return None
        if (_TRANSFER_REGULATORY_RE.search(folded)
                or (not explicit_price and _TRANSFER_PROCEDURE_RE.search(folded))):
            return None
    elif explicit_service and _TRANSFER_REGULATORY_RE.search(folded):
        return None

    if _is_account_action(question) or _ACTIVE_INCIDENT_FOR_RATE_RE.search(folded):
        return None

    segment = explicit_segment
    if segment not in ("individual", "business"):
        segment = inherited.customer_segment if inherited is not None else None

    transfer_scope = None
    if explicit_domestic:
        transfer_scope = "domestic"
    elif explicit_international:
        transfer_scope = "international"
    elif inherited is not None:
        transfer_scope = inherited.transfer_scope

    banks = candidate_banks
    if banks:
        bank_scope = "named"
    elif explicit_comparison:
        bank_scope = "all"
    elif inherited is not None:
        bank_scope = inherited.bank_scope
        banks = inherited.banks
    else:
        bank_scope = "missing"

    metric = "fee" if explicit_price else inherited.metric if inherited is not None else None
    intent = RateIntent(
        bank_scope=bank_scope, banks=banks, product=None, metric=metric,
        fee_event=None, value_type=None, term_months=None, amount_band=None,
        breadth="leaf", family="bank_transfer", customer_segment=segment,
        transfer_scope=transfer_scope,
    )
    if metric is None:
        message = (TRANSFER_FEE_SCOPE_CLARIFY_MESSAGE
                   if transfer_scope is None else TRANSFER_CONTEXT_MESSAGE)
        return Decision(
            Outcome.CLARIFY if transfer_scope is None else Outcome.ANSWER,
            message, question=question,
            reason=DecisionReason.TRANSFER_CONTEXT_ESTABLISHED,
            rate_intent=intent,
            trace_flags=frozenset({DecisionEvent.structured_lookup}),
        )
    if segment is None or transfer_scope is None:
        return Decision(
            Outcome.CLARIFY,
            _transfer_clarify_message(segment, transfer_scope), question=question,
            reason=DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
            rate_intent=intent,
            trace_flags=frozenset({DecisionEvent.structured_lookup}),
        )
    if bank_scope == "missing":
        return Decision(
            Outcome.CLARIFY, TRANSFER_FEE_BANK_CLARIFY_MESSAGE, question=question,
            reason=DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
            rate_intent=intent,
            trace_flags=frozenset({DecisionEvent.structured_lookup}),
        )
    return Decision(
        Outcome.UNSUPPORTED, TRANSFER_FEE_UNAVAILABLE_MESSAGE, question=question,
        reason=DecisionReason.TRANSFER_FEE_PRICE_UNAVAILABLE,
        rate_intent=intent,
        trace_flags=frozenset({DecisionEvent.structured_lookup}),
    )

def _structured_rate_enabled() -> bool:
    return os.environ.get("BOABOT_COMPARISON_STRUCTURED", "").strip().lower() in _ENABLE


def _fragment_meta_preflight(
        question: str, last_handoff: bool = False,
        last_answer: str = "") -> Decision | None:
    """Expose the deterministic never-retrieve fragment/meta floor."""
    from .router import (is_answer_clarification_request,
                         is_conversational_fragment, is_meta_help)

    if is_answer_clarification_request(question):
        # Meta turn about the PREVIOUS answer: reference it, never ask the
        # caller to re-clarify their banking question (that was the loop).
        # Deterministic; the frame is preserved (FRAGMENT_META is in the
        # PRESERVE set of next_structured_frame).
        if last_answer and last_answer.strip():
            excerpt = " ".join(last_answer.split())
            if len(excerpt) > 400:
                excerpt = excerpt[:400].rstrip() + "…"
            message = (
                "Përgjigja ime e mëparshme ishte: \"" + excerpt + "\". "
                "Nëse një pjesë e saj nuk është e qartë (shifrat, termat ose "
                "tabelat), më tregoni cilën dhe do ta shpjegoj më thjesht."
            )
        else:
            message = META_FOLLOWUP_MESSAGE
        return Decision(
            Outcome.ANSWER, message, question=question,
            handoff=last_handoff, reason=DecisionReason.FRAGMENT_META,
        )

    if not (is_conversational_fragment(question) or is_meta_help(question)
            or _is_bare_courtesy(question)):
        return None
    # Bare courtesy ("te lutem" alone) gets the polite keep-helping reply,
    # never a retrieval attempt.
    if _is_bare_courtesy(question):
        message = (META_FOLLOWUP_HANDOFF_MESSAGE if last_handoff
                   else COURTESY_MESSAGE)
        return Decision(
            Outcome.ANSWER, message, question=question,
            handoff=last_handoff, reason=DecisionReason.FRAGMENT_META,
        )
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


_DEICTIC_WHICH_BANK_RE = re.compile(
    r"\b(?:per\s+)?cil[ae]n?\s+bank\w*\b.*\bfjale\b", re.I,
)
_DEICTIC_WHICH_BANK_RE2 = re.compile(r"\b(?:cil[ae]|per\s+cil[ae]n?)\s+bank\w*\b", re.I)
_DEICTIC_WHICH_BANK_TAIL_RE = re.compile(r"\bfjale\b|\bparaske\b", re.I)


def _deictic_bank_scope_preflight(
        question: str, frame: RateIntent | None,
        ) -> Decision | None:
    """Deictic \"which bank\" question right after a structured listing.

    \"per cilen banke behet fjale?\" / \"cila banke e ka?\" after a rate/
    deposit listing is a bank-scoping continuation. If the frame's rows are
    attributed per bank, ask which bank; if they are product-label aggregates
    (housing-credit NEI, business), the honest reply is the attribution
    boundary — never a refusal. Runs as a DETERMINISTIC preflight in decide()
    on the ORIGINAL question (before rewrite/the LLM router), so the live
    rewrite of the deictic into a fuller rate ask (which parses
    ``unknown_bank``) can no longer hijack it.
    """
    if frame is None:
        return None
    folded = fold(question)
    if not (_DEICTIC_WHICH_BANK_RE.search(folded)
            or (_DEICTIC_WHICH_BANK_RE2.search(folded)
                and _DEICTIC_WHICH_BANK_TAIL_RE.search(folded))):
        return None
    from .comparison import _rows_for_missing_product, _source_bank_labels
    # Frame resolution mirrors the structured path's plan scoping: a frame
    # like "normat e interesit?" (product=None) resolves via
    # _rows_for_missing_product (deposit rows), NOT resolve_rate_rows on the
    # raw frame (which returns [] for family/product None).
    frame_rows = _rows_for_missing_product(frame)
    frame_has_banks = any(row.get("_bank_lines") for row in frame_rows)
    if frame_has_banks:
        labels = ", ".join(_source_bank_labels())
        return Decision(
            Outcome.CLARIFY,
            f"Për cilën bankë dëshironi? Kam të dhëna për: {labels}.",
            question=question, reason=DecisionReason.CATALOG_UNKNOWN_BANK,
            rate_intent=frame,
            trace_flags=frozenset({
                DecisionEvent.context_inherited,
                DecisionEvent.structured_lookup,
            }),
        )
    return Decision(
        Outcome.ANSWER,
        "Vlera siç raportohen nga Banka e Shqipërisë; tabela nuk i "
        "atribuon çdo shifër një banke të caktuar.",
        question=question, reason=DecisionReason.CATALOG_EXACT_HIT,
        rate_intent=frame,
        trace_flags=frozenset({
            DecisionEvent.context_inherited,
            DecisionEvent.structured_lookup,
        }),
    )


def _structured_rate_decision(
        question: str, *, frame: RateIntent | None = None) -> Decision | None:
    """Injectable pre-LLM seam for exact closed-catalog rate requests."""
    if not _structured_rate_enabled() or not _structured_rate_eligible(question):
        return None
    from .comparison import (CATALOG_DECLINE_REASONS, ResponseMode, _dimension_varies, _rate_rows, 
                            _row_slots, _source_bank_labels,
                             merge_elliptical, parse_rate_intent_hybrid,
                             plan_structured_response, resolve_rate_rows)

    parsed = parse_rate_intent_hybrid(question)
    plan = plan_structured_response(question, parsed)
    if plan is not None:
        if plan.mode is ResponseMode.CLARIFY:
            return Decision(
                Outcome.CLARIFY, plan.message, question=question,
                reason=DecisionReason.STRUCTURED_PLANNER_CLARIFY,
                rate_intent=plan.intent, response_plan=plan,
                trace_flags=frozenset({DecisionEvent.structured_lookup}),
            )
        return Decision(
            None, question=question,
            reason=DecisionReason.CATALOG_EXACT_HIT,
            rate_intent=plan.intent, response_plan=plan,
            trace_flags=frozenset({DecisionEvent.structured_lookup}),
        )


    if parsed.status == "not_rate":
        if frame is not None:
            merged = merge_elliptical(question, frame)
            merged_rows = resolve_rate_rows(merged) if merged is not None else []
            if merged is not None and frame.bank_scope == "all":
                cleared = merged._replace(bank_scope="all", banks=())
                cleared_rows = resolve_rate_rows(cleared)
                # Commit 5.1 admits family listings whose rows carry product
                # labels rather than banks. Treat those empty _bank_lines as
                # the same failed bank-scoped resolution this fallback fixes.
                bank_scoped_rows = any(
                    row.get("_bank_lines") for row in merged_rows
                )
                if cleared_rows and (not merged_rows or not bank_scoped_rows):
                    merged = cleared
                    merged_rows = cleared_rows
            if merged is not None and merged_rows:
                return Decision(
                    None, question=question,
                    reason=DecisionReason.CATALOG_EXACT_HIT,
                    rate_intent=merged,
                    trace_flags=frozenset({
                        DecisionEvent.context_inherited,
                        DecisionEvent.structured_lookup,
                    }),
                )
            # Bank-only continuation ("banka raiffeisen" after a structured
            # listing): the merge bound the bank but left product/family from
            # the bare phrase unset, so the merged intent resolves no rows.
            # Re-derive from the frame's OWN scope and bind only the bank —
            # this is the natural "give me that listing for this bank"
            # continuation, not a dense-refusal. The frame that answers a
            # bare "normat e interesit" ask is plan-resolved (missing_product
            # -> deposit), so resolution must mirror the plan's scoping:
            # _rows_for_missing_product with the bank bound, NOT
            # resolve_rate_rows on the raw family=None frame (that always
            # returns []). The continuation must be a PURE bank scoping —
            # family/product/metric inherited unchanged — or a "po per kredi?"
            # family switch could wrongly resolve deposit rows under a credit
            # frame (family-agnostic _rows_for_missing_product). [SUPERSEDED]
            # These bare-bank turns previously fell through to dense retrieval
            # and abstained with NO_EVIDENCE_MESSAGE even when the frame
            # carried the answer.
            if merged is not None and merged.bank_scope == "named":
                pure_bank_scoping = (
                    merged.family == frame.family
                    and merged.product == frame.product
                    and merged.metric == frame.metric
                )
                if pure_bank_scoping:
                    from .comparison import _rows_for_missing_product
                    bank_scoped_rows = _rows_for_missing_product(merged)
                    if bank_scoped_rows:
                        return Decision(
                            None, question=question,
                            reason=DecisionReason.CATALOG_EXACT_HIT,
                            rate_intent=merged,
                            trace_flags=frozenset({
                                DecisionEvent.context_inherited,
                                DecisionEvent.structured_lookup,
                            }),
                        )
            # [SUPERSEDED] The deictic "which bank" handling moved to
            # _deictic_bank_scope_preflight so it runs in decide() on the
            # ORIGINAL question, before the LLM can rewrite it into a fuller
            # rate ask (which parses unknown_bank and hijacked this branch).
        return None
    if parsed.status == "unsupported":
        if parsed.reason == "unrepresented_semantics":
            return Decision(
                None, question=question,
                reason=DecisionReason.DENSE_RETRIEVAL,
                trace_flags=frozenset({
                    DecisionEvent.unresolved_qualifier_detected,
                }),
            )
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
                "term_months": "afati (muaj)",
                "amount_band": "shuma",
                "customer_segment": "segmenti (individë apo biznese)",
                "fee_event": "lloji i komisionit",
                "loan_type": "lloji i kredisë (konsumatore, shtëpi/hipotekare, biznes)",
            }
            unresolved = [
                item for item in (
                    parsed.coverage.unresolved_qualifiers
                    if parsed.coverage is not None else ()
                ) if item in labels
            ]
            # Never ask for a dimension the corpus does not vary on: every row
            # is currency=ALL, so asking for it dead-ends on the only correct
            # answer the user can give.
            if parsed.intent is not None:
                unresolved = [
                    item for item in unresolved
                    if _dimension_varies(item, parsed.intent)
                ]
            # Ask for ONE dimension (Task AH) and name its real values from the
            # corpus, mirroring the maturity_band_required branch below.
            first = unresolved[0] if unresolved else None
            if first is None:
                message = (
                    "Nuk kam një dimension të mëtejshëm për ta ngushtuar këtë "
                    "krahasim në tabelat e publikuara."
                )
            else:
                message = f"Për ta krahasuar saktë, më duhet {labels[first]}."
                if first == "term_months" and parsed.intent is not None:
                    bands = sorted(
                        {
                            slots.maturity_band
                            for row in _rate_rows()
                            if (slots := _row_slots(row)).product
                            == parsed.intent.product
                            and slots.maturity_band is not None
                        },
                        key=lambda band: band[0],
                    )
                    if bands:
                        band_text = ", ".join(f"{a}-{b} muaj" for a, b in bands)
                        message = (
                            f"Për ta krahasuar saktë, më duhet afati. Tabela "
                            f"raporton për {band_text}. Për cilin po pyesni?"
                        )
            reason = DecisionReason.COMPARISON_DIMENSIONS_MISSING
        elif parsed.reason == "maturity_band_required":
            # Business-rate family: band required (rule 2/3 — CLARIFY, never
            # guess). List the actual source bands from the corpus.
            source_bands = sorted(
                {
                    slots.maturity_band
                    for row in _rate_rows()
                    if (slots := _row_slots(row)).business_size is not None
                    and slots.maturity_band is not None
                },
                key=lambda band: band[0],
            )
            band_text = ", ".join(f"{a}-{b} muaj" for a, b in source_bands)
            message = (
                f"Normat e biznesit raportohen sipas maturitetit. Për cilin "
                f"maturitet po pyesni? Tabela raporton për {band_text}."
            )
            reason = DecisionReason.MATURITY_BAND_REQUIRED
        return Decision(
            Outcome.CLARIFY, message, question=question,
            reason=reason,
            rate_intent=parsed.intent,
            trace_flags=(
                frozenset({DecisionEvent.structured_lookup})
                if parsed.intent is not None else frozenset()
            ),
        )
    return Decision(
        None, question=question, reason=DecisionReason.CATALOG_EXACT_HIT,
        rate_intent=parsed.intent,
        trace_flags=frozenset({DecisionEvent.structured_lookup}),
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

    # Explicit transaction changes outrank incident probing and fee routing.
    if _TRANSACTION_ACTION_RE.search(fold(clean_question)):
        return Decision(
            Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE,
            question=clean_question, handoff=True,
            reason=DecisionReason.ACCOUNT_ACTION_BACKSTOP,
        )

    # ---- Transfer-fee amount seam (deterministic, NEVER retrieves) ----
    transfer_fee = _transfer_fee_decision(clean_question, last_structured_frame)
    if transfer_fee is not None:
        return transfer_fee

    # ---- Fragment/meta floor (deterministic, NEVER retrieves) ----
    # [SUPERSEDED] This floor previously lived only inside router.classify_turn /
    # analyze_turn, which made its pre-retrieval position implicit. The router
    # checks remain for compatibility, but this exposed preflight is authoritative.
    fragment_meta = _fragment_meta_preflight(clean_question, last_handoff, last_answer)
    if fragment_meta is not None:
        return fragment_meta

    # ---- Deictic bank-scoping preflight (deterministic, BEFORE rewrite) ----
    # "per cilen banke behet fjale?" / "cila banke e ka?" right after a
    # structured listing asks which bank the listing is about. Must run on
    # the ORIGINAL question with the frame, BEFORE the LLM router/rewrite can
    # expand it into a fuller rate ask (which parses unknown_bank and
    # abstains). Frame-scoped: no frame -> normal path.
    if last_structured_frame is not None:
        deictic_bank = _deictic_bank_scope_preflight(
            clean_question, last_structured_frame,
        )
        if deictic_bank is not None:
            return deictic_bank

    # ---- Product-capability statement (deterministic, BEFORE the router) ----
    # Bare "cfare produktesh ofron secila banke?" gets the concise capability
    # summary + filter offer instead of a regulatory wall of text.
    if _is_product_capability_speech(clean_question):
        return Decision(
            Outcome.ANSWER, _product_capability_message(),
            question=clean_question, handoff=False,
            reason=DecisionReason.PRODUCT_CAPABILITY,
        )

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
    trace_flags: frozenset[DecisionEvent] = frozenset()
    if _structured_rate_enabled() and _structured_rate_eligible(clean_question):
        structured = _structured_rate_decision(
            clean_question, frame=last_structured_frame,
        )
        if structured is not None:
            if structured.reason is not DecisionReason.DENSE_RETRIEVAL:
                return structured
            trace_flags = structured.trace_flags

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
    routed = _route_label(label, clean_question, last_handoff, trace_flags)
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
            trace_flags=trace_flags,
        )

    query_embedding = _encode_question(clean_question)
    incident_score = None
    if (_incident_context_has_positive_evidence(
            clean_question, history, last_outcome, last_handoff)
            and not _is_informational_banking_query(
                clean_question, last_outcome, history, last_structured_frame)):
        # Deterministic backstop: the frozen incident classifier still runs on
        # non-informational turns so an LLM-missed incident escalates. Incident
        # vocabulary is routed through the classifier unchanged.
        incident_score = _probe_score(query_embedding)
    if incident_score is not None and incident_score >= _HANDOFF_THRESHOLD:
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            query_embedding=query_embedding, handoff_score=incident_score,
            reason=DecisionReason.INCIDENT_BACKSTOP,
            trace_flags=trace_flags,
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
                    rewritten_query=fused_rewrite, legal_flags=fused_legal,
                    trace_flags=trace_flags)
