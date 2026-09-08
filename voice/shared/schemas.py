"""Canonical logical contracts between voice transports and BOA.

These models deliberately describe text turns, not banking intent or dialogue
state.  BOA remains the sole owner of those concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .events import TurnId
from .turn_client import TurnResult

VoiceSource = Literal["cascade", "s2s"]


@dataclass(frozen=True, slots=True)
class VoiceUserTurn:
    """A committed final transcript ready for the authoritative BOA turn API."""

    session_id: str | None
    text: str
    source: VoiceSource
    is_final: bool = True
    turn_id: TurnId = TurnId(0)
    correlation_key: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceBoaResponse:
    """BOA's authoritative response, normalized for either voice arm.

    ``turn_result`` is retained only for stream-compatible rendering during the
    migration from the existing SSE contract.  All routing and safety metadata
    is exposed here without voice code reimplementing any banking decision.
    """

    session_id: str
    text: str
    route: str | None
    reason: str | None
    handoff: bool
    pii_redacted: bool
    sources: tuple[dict[str, str], ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    turn_result: TurnResult | None = None

    @classmethod
    def from_turn_result(cls, result: TurnResult) -> "VoiceBoaResponse":
        return cls(
            session_id=result.done.session_id,
            text="".join(result.tokens),
            route=result.done.outcome,
            reason=result.done.reason,
            handoff=result.done.handoff,
            pii_redacted=result.done.pii_redacted,
            sources=result.done.sources,
            usage=result.done.usage,
            turn_result=result,
        )

    # Compatibility properties let the existing renderers finish consuming the
    # authoritative stream while all new BOA calls pass through BoaClient.
    @property
    def tokens(self) -> tuple[str, ...]:
        return self.turn_result.tokens if self.turn_result is not None else ()

    @property
    def done(self):
        if self.turn_result is None:
            raise RuntimeError("BOA response has no terminal turn metadata")
        return self.turn_result.done

    @property
    def approved_sentences(self) -> tuple[str, ...]:
        return self.turn_result.approved_sentences if self.turn_result is not None else ()

    @property
    def vetted_chunks(self) -> tuple[str, ...]:
        return self.turn_result.vetted_chunks if self.turn_result is not None else ()
