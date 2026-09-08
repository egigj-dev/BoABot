"""The only voice-side integration adapter for BOA's text turn API."""

from __future__ import annotations

import uuid

from typing import Any, Protocol

from .events import TurnRequest
from .schemas import VoiceBoaResponse, VoiceUserTurn
from .turn_client import EventHandler, TurnService


class BoaTurnService(Protocol):
    async def process_boa_turn(
        self,
        user_turn: VoiceUserTurn,
        on_event: EventHandler | None = None,
        *,
        include_vetted_text: bool = False,
    ) -> VoiceBoaResponse: ...

    async def cancel(self, correlation_key: str | None = None) -> None: ...


class BoaClient:
    """Adapt committed voice text to the established ``POST /turn`` contract.

    It intentionally has no banking routing, retrieval, safety, or session
    state.  The supplied session id is opaque and is owned by BOA.
    """

    def __init__(self, turn_service: TurnService) -> None:
        self._turn_service = turn_service

    async def process_boa_turn(
        self,
        user_turn: VoiceUserTurn,
        on_event: EventHandler | None = None,
        *,
        include_vetted_text: bool = False,
    ) -> VoiceBoaResponse:
        if not user_turn.is_final:
            raise ValueError("only a committed final voice turn may invoke BOA")
        text = user_turn.text.strip()
        if not text:
            raise ValueError("a non-empty committed voice turn is required")
        request = TurnRequest(
            text,
            user_turn.session_id,
            user_turn.turn_id,
            include_vetted_text=include_vetted_text,
            correlation_key=user_turn.correlation_key or f"voice-{user_turn.source}-{uuid.uuid4().hex}",
        )
        result = await self._turn_service.run(request, on_event)
        return VoiceBoaResponse.from_turn_result(result)

    async def cancel(self, correlation_key: str | None = None) -> None:
        await self._turn_service.cancel(correlation_key)


def as_boa_client(service: BoaTurnService | TurnService) -> BoaTurnService:
    """Accept legacy test doubles while ensuring production calls share BoaClient."""

    if hasattr(service, "process_boa_turn"):
        return service  # type: ignore[return-value]
    return BoaClient(service)  # type: ignore[arg-type]
