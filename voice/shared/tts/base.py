"""Approved-text-only TTS interface from Schema 1 §3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..events import AudioChunk, GenerationId, TurnId


class TTS(ABC):
    @abstractmethod
    def synthesize(self, approved_text: str, turn_id: TurnId, generation_id: GenerationId,
                   render_request_id: str) -> AsyncIterator[AudioChunk]: ...

    @abstractmethod
    async def cancel(self, render_request_id: str) -> None: ...
