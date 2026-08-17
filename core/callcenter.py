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

    if _is_account_action(clean_question):
        return Decision(
            Outcome.HANDOFF, ACCOUNT_HANDOFF_MESSAGE, handoff=True,
            reason="account_action",
        )

    query_embedding = _encode_question(clean_question)
    incident_score = _probe_score(query_embedding)
    if incident_score is not None and incident_score >= _HANDOFF_THRESHOLD:
        return Decision(
            Outcome.HANDOFF, SECURITY_HANDOFF_MESSAGE, handoff=True,
            query_embedding=query_embedding, handoff_score=incident_score,
            reason="active_incident",
        )
    account_score = _account_action_score(query_embedding)
    if is_ambiguous_card_maintenance(clean_question):
        return Decision(
            Outcome.CLARIFY, CARD_CLARIFY_MESSAGE, question=clean_question,
            query_embedding=query_embedding, handoff_score=account_score,
            reason="disambiguation",
        )
    return Decision(None, question=clean_question, query_embedding=query_embedding,
                    handoff_score=account_score)
