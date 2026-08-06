"""Call-center conversation policy and in-memory session state."""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum

import numpy as np

from retrieve import model
from trust import (BUSINESS_DEPOSIT_MESSAGE, UNSAFE_INPUT_MESSAGE, input_gate,
                   is_business_deposit_question)

MAX_HISTORY_MESSAGES = 12
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 1_000

CLARIFY_MESSAGE = (
    "Mund ta sqaroni pak pyetjen? Për shembull, tregoni bankën, produktin "
    "ose rregulloren për të cilën po pyesni."
)
HANDOFF_MESSAGE = (
    "Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. "
    "Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë."
)
REPEAT_MESSAGE = "Nuk kam ende një përgjigje për ta përsëritur. Si mund t’ju ndihmoj?"

class Outcome(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    HANDOFF = "handoff"
    REPEAT = "repeat"

@dataclass(frozen=True)
class Decision:
    outcome: Outcome | None
    message: str = ""
    question: str = ""
    handoff: bool = False
    pii_redacted: bool = False
    query_embedding: np.ndarray | None = None  # Normalized caller vector for downstream retrieval reuse.
    handoff_score: float | None = None  # Best semantic handoff-exemplar cosine score.

@dataclass
class Session:
    session_id: str
    history: list[dict[str, str]]
    last_answer: str
    updated_at: float

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

    def record(self, session: Session, question: str, answer: str) -> None:
        with self._lock:
            session.history.extend((
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ))
            session.history = session.history[-MAX_HISTORY_MESSAGES:]
            session.last_answer = answer
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
    r"(?:\b(?:pin|cvv|cvc|otp)\b.{0,80}\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|tregova|"
    r"kerk|doli|nuk funksion)|\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|tregova|kerk|doli|"
    r"nuk funksion).{0,80}\b(?:pin|cvv|cvc|otp)\b)", re.I)

# Diverse caller-speech anchors chosen from handoff_phrases.jsonl, eight per protected intent.
_HANDOFF_EXEMPLARS = (
    ("lost_card", "Kam humbur kartën"), ("lost_card", "Karta ime është e humbur"),
    ("lost_card", "Nuk po e gjej kartën time"), ("lost_card", "Kartën e lashë në taksi"),
    ("lost_card", "Karta ime... nuk e gjej"), ("lost_card", "Më ka ikur karta nga portofoli"),
    ("lost_card", "Ku raportohen kartat e humbura?"), ("lost_card", "Karta u humb në udhëtim"),
    ("stolen_card", "Ma kanë vjedhur kartën"), ("stolen_card", "Karta ime është vjedhur"),
    ("stolen_card", "Dikush ma ka marrë kartën"), ("stolen_card", "Kartën ma morën nga çanta"),
    ("stolen_card", "Më pickuan kartën në autobus"), ("stolen_card", "Më hynë në shtëpi dhe morën kartën"),
    ("stolen_card", "Karta ime... ma kanë vjedhur"), ("stolen_card", "Kartën ma kanë rrëmbyer"),
    ("fraud_unauthorized", "Nuk e njoh këtë transaksion"), ("fraud_unauthorized", "Dikush ka perdorur karten time"),
    ("fraud_unauthorized", "Më janë marrë para nga llogaria"), ("fraud_unauthorized", "Kam një pagesë që s'e kam bërë unë"),
    ("fraud_unauthorized", "Shoh një transferim që nuk e autorizova"), ("fraud_unauthorized", "Karta ime... ka blerje që s'i njoh"),
    ("fraud_unauthorized", "Është tërhequr cash pa dijeninë time"), ("fraud_unauthorized", "Po më ikin lekët nga banka"),
    ("block_freeze", "Dua ta bllokoj kartën"), ("block_freeze", "Më duhet të ngrij kartën"),
    ("block_freeze", "Dua të pezulloj llogarinë"), ("block_freeze", "Ndalo pagesat nga karta ime"),
    ("block_freeze", "Karta ime... bllokojeni"), ("block_freeze", "Ta stopoj kartën"),
    ("block_freeze", "Çaktivizojeni kartelën"), ("block_freeze", "Dua të ndaloj transfertat nga llogaria"),
    ("secret_credential", "Kodi PIN më është zbuluar"), ("secret_credential", "Dikush e di PIN-in tim"),
    ("secret_credential", "Kam ndarë fjalëkalimin pa dashje"), ("secret_credential", "Më kërkuan kodin OTP dhe ua dhashë"),
    ("secret_credential", "CVV-ja ime është komprometuar"), ("secret_credential", "Kam klikuar link dhe futa fjalëkalimin"),
    ("secret_credential", "Kodi SMS i bankës i shkoi dikujt tjetër"), ("secret_credential", "Fjalëkalimin e kam treguar"),
)
_HANDOFF_THRESHOLD = 0.82  # Tuned cosine cutoff; change only with phrase-bank evaluation.
_HANDOFF_TEXTS = tuple(text for _, text in _HANDOFF_EXEMPLARS)  # Text-only view passed to bge-m3.
_HANDOFF_EMBEDDINGS = np.asarray(  # Cached normalized exemplar vectors, encoded once at module import.
    model().encode(_HANDOFF_TEXTS, normalize_embeddings=True)
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
    lowered = text.casefold()
    return any(term in lowered for term in (
        "përsërite", "perserite", "ma përsërit", "ma perserit",
        "nuk dëgjova", "nuk degjova", "thuaje prap", "repeat",
    ))

def _handoff_embedding(question: str) -> tuple[np.ndarray, float]:
    """Encode a caller turn once and return its strongest handoff-anchor cosine."""
    query_embedding = np.asarray(model().encode([question], normalize_embeddings=True)[0])
    best_score = float(np.max(_HANDOFF_EMBEDDINGS @ query_embedding))
    return query_embedding, best_score


def decide(question: str, last_answer: str, history: list[dict[str, str]]) -> Decision:
    """Route a caller turn before it can reach retrieval or the model."""
    gate = input_gate(question)
    if not gate.allowed:
        return Decision(Outcome.UNSUPPORTED, UNSAFE_INPUT_MESSAGE)

    if _is_repeat(question):
        return Decision(Outcome.REPEAT, last_answer or REPEAT_MESSAGE)

    if _SECRET_FAST_RE.search(question):
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True)

    if is_business_deposit_question(question, history):
        return Decision(Outcome.UNSUPPORTED, BUSINESS_DEPOSIT_MESSAGE)

    clean_question, pii_redacted = _redact_pii(question)
    if pii_redacted:
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True, pii_redacted=True)

    if len(clean_question.split()) < 3:
        return Decision(Outcome.CLARIFY, CLARIFY_MESSAGE)

    query_embedding, handoff_score = _handoff_embedding(clean_question)
    if handoff_score >= _HANDOFF_THRESHOLD:
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True,
                        query_embedding=query_embedding, handoff_score=handoff_score)
    return Decision(None, question=clean_question, query_embedding=query_embedding,
                    handoff_score=handoff_score)

