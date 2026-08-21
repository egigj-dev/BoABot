"""Call-center conversation policy and in-memory session state."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from .retrieve import EMBEDDING_MODEL_NAME, model
from .text_norm import fold
from .trust import UNSAFE_INPUT_MESSAGE, input_gate

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

@dataclass(frozen=True)
class Decision:
    outcome: Outcome | None
    message: str = ""
    question: str = ""
    handoff: bool = False
    pii_redacted: bool = False
    query_embedding: np.ndarray | None = None  # Normalized caller vector for downstream retrieval reuse.
    handoff_score: float | None = None  # Frozen positive-vs-negative neighbour margin.
    reason: str = ""

@dataclass
class Session:
    session_id: str
    history: list[dict[str, str]]
    last_answer: str
    updated_at: float
    last_outcome: Outcome | None = None
    last_handoff: bool = False

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
    r"kerk|doli|nuk funksion)|\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|tregova|kerk|doli|"
    r"nuk funksion).{0,80}\b(?:pin|cvv|cvc|otp)\b)", re.I)

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
    r"\b(?:bank|rregullore|komision|norm|tarif|kredi|depozit|kart|neni|shlyerje|"
    r"interes|licenc|mbikeqyr)\w*",
    re.I,
)
_INCIDENT_MARKER_RE = re.compile(
    r"\b(?:humb|vjedh|vidh|bllok|pin|cvv|cvc|otp|kod|ikin|iken|dikush|mashtr|"
    r"raportoj|kartel|ime|time|mua)\b",
    re.I,
)


def _is_informational_banking_query(text: str) -> bool:
    folded = fold(text)
    return (
        bool(_QUESTION_MARKER_RE.search(folded))
        and bool(_DOMAIN_MARKER_RE.search(folded))
        and not _INCIDENT_MARKER_RE.search(folded)
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


def _fallback_label(question: str) -> str:
    """Old lexical intent used when the router is off/unavailable."""
    if _is_smalltalk(question):
        return "smalltalk"
    if _is_account_action(question):
        return "account_action"
    if is_ambiguous_card_maintenance(question):
        return "clarify"
    return "answer"


def _route_label(label: str, question: str, last_handoff: bool) -> Decision | None:
    """Map a router label to a terminal Decision; return None to fall through."""
    if label == "smalltalk":
        return Decision(Outcome.ANSWER, _smalltalk_reply(question),
                        question=question, handoff=False, reason="smalltalk")
    if label == "out_of_domain":
        return Decision(Outcome.UNSUPPORTED, OUT_OF_DOMAIN_MESSAGE,
                        question=question, handoff=False, reason="out_of_domain")
    if label == "meta_followup":
        msg = META_FOLLOWUP_HANDOFF_MESSAGE if last_handoff else META_FOLLOWUP_MESSAGE
        return Decision(Outcome.ANSWER, msg, question=question,
                        handoff=last_handoff, reason="meta_followup")
    if label == "legal_advice":
        return Decision(Outcome.UNSUPPORTED, LEGAL_ADVICE_MESSAGE,
                        question=question, handoff=False, reason="legal_advice_router")
    if label == "account_action":
        return Decision(Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE,
                        handoff=True, reason="account_action")
    if label in ("incident", "incident_handoff"):
        return Decision(Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE,
                        handoff=True, reason="incident_router")
    if label == "clarify":
        return Decision(Outcome.CLARIFY, CARD_CLARIFY_MESSAGE, question=question,
                        reason="disambiguation")
    return None  # "answer" / unknown -> fall through to retrieval


def _classify_turn(question: str, last_outcome=None, last_handoff: bool = False):
    """Injectable seam: the router call. Tests monkeypatch this directly."""
    try:
        from .router import classify_turn as impl
        return impl(question, last_outcome, last_handoff)
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

def decide(question: str, last_answer: str, history: list[dict[str, str]],
           last_outcome: Outcome | None = None, last_handoff: bool = False) -> Decision:
    """Route a caller turn before it can reach retrieval or the model."""
    gate = input_gate(question)
    if not gate.allowed:
        return Decision(Outcome.UNSUPPORTED, UNSAFE_INPUT_MESSAGE)

    clean_question, pii_redacted = _redact_pii(question)
    if _SECRET_FAST_RE.search(clean_question):
        # The raw credential-bearing text is deliberately not copied into the
        # decision/session history; expose that redaction happened in telemetry.
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            pii_redacted=True, reason="credential",
        )
    if pii_redacted:
        return Decision(
            Outcome.HANDOFF, PII_HANDOFF_MESSAGE, question=clean_question,
            handoff=True, pii_redacted=True, reason="pii",
        )

    if _is_repeat(clean_question):
        return Decision(
            Outcome.REPEAT, last_answer or REPEAT_MESSAGE,
            handoff=last_handoff, reason="repeat",
        )

    # ---- Legal-advice floor (deterministic, kept BEFORE the router) ----
    if _is_legal_advice_explicit(clean_question):
        return Decision(
            Outcome.UNSUPPORTED, LEGAL_ADVICE_MESSAGE,
            question=clean_question, handoff=False, reason="legal_advice_explicit",
        )

    # ---- LLM turn-router (semantic intent) ----
    # Replaces the lexical smalltalk / account_action / card-clarify gates with a
    # single semantic classification. The router is OFF by default
    # (BOABOT_LLM_ROUTER=1 to enable) and falls back to _fallback_label so
    # offline behavior and tests are unchanged until explicitly enabled. The
    # security gates above and the legal-advice floor stay deterministic.
    #
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
            reason="account_action_backstop",
        )

    query_embedding = _encode_question(clean_question)
    incident_score = None
    if not _is_informational_banking_query(clean_question):
        # Deterministic backstop: the frozen incident classifier still runs on
        # non-informational turns so an LLM-missed incident escalates. Incident
        # vocabulary is routed through the classifier unchanged.
        incident_score = _probe_score(query_embedding)
    if incident_score is not None and incident_score >= _HANDOFF_THRESHOLD:
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            query_embedding=query_embedding, handoff_score=incident_score,
            reason="active_incident",
        )
    account_score = _account_action_score(query_embedding)
    # [SUPERSEDED] Ambiguous card-maintenance handled by the router label
    #              "clarify". Old lexical form:
    #   if is_ambiguous_card_maintenance(clean_question):
    #       return Decision(Outcome.CLARIFY, CARD_CLARIFY_MESSAGE,
    #                       question=clean_question, query_embedding=query_embedding,
    #                       handoff_score=account_score, reason="disambiguation")
    return Decision(None, question=clean_question, query_embedding=query_embedding,
                    handoff_score=account_score)
