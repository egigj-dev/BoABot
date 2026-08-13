"""Typed correlation and media events shared by both voice schemas (§§2–3)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NewType

TurnId = NewType("TurnId", int)
GenerationId = NewType("GenerationId", int)


class EventKind(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    ERROR = "error"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class CallEvent:
    kind: EventKind
    call_id: str
    turn_id: TurnId
    generation_id: GenerationId
    monotonic_s: float = field(default_factory=time.monotonic)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    final: bool
    confidence: float | None = None
    alternatives: tuple[str, ...] = ()
    critical_confidences: dict[str, float] = field(default_factory=dict)
    provider: str = "unknown"
    started_s: float | None = None
    finalized_s: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnRequest:
    question: str
    session_id: str | None
    turn_id: TurnId
    include_vetted_text: bool = True
    correlation_key: str = field(default_factory=lambda: uuid.uuid4().hex)

    def wire_payload(self) -> dict[str, str | bool | None]:
        """Return the fields accepted by ``api.py:TurnReq``."""
        return {"question": self.question, "session_id": self.session_id,
                "include_vetted_text": self.include_vetted_text}


@dataclass(frozen=True, slots=True)
class TurnDone:
    outcome: str
    session_id: str
    sources: tuple[dict[str, str], ...] = ()
    handoff: bool = False
    pii_redacted: bool = False
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovedSentence:
    text: str
    turn_id: TurnId
    generation_id: GenerationId
    sources: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RenderRequest:
    request_id: str
    call_id: str
    turn_id: TurnId
    generation_id: GenerationId
    approved_text: str


@dataclass(frozen=True, slots=True)
class AudioChunk:
    data: bytes
    turn_id: TurnId
    generation_id: GenerationId
    render_request_id: str
    first_byte_ms: float | None = None
    final: bool = False
