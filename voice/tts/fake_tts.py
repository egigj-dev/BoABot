"""Deterministic approved-text renderer for offline tests and demos."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from ..events import AudioChunk, GenerationId, TurnId
from .base import TTS


class FakeTTS(TTS):
    def __init__(self, chunk_size: int = 32, delay_ms: float = 0) -> None:
        self.chunk_size = chunk_size
        self.delay_s = delay_ms / 1000
        self.cancelled: set[str] = set()
        self.approved_inputs: list[str] = []

    async def _synthesize(self, approved_text: str, turn_id: TurnId,
                          generation_id: GenerationId,
                          render_request_id: str) -> AsyncIterator[AudioChunk]:
        self.approved_inputs.append(approved_text)
        payload = approved_text.encode("utf-8")
        started = time.monotonic()
        for offset in range(0, len(payload), self.chunk_size):
            if render_request_id in self.cancelled:
                return
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield AudioChunk(
                data=payload[offset:offset + self.chunk_size], turn_id=turn_id,
                generation_id=generation_id, render_request_id=render_request_id,
                first_byte_ms=(time.monotonic() - started) * 1000 if offset == 0 else None,
                final=offset + self.chunk_size >= len(payload),
            )

    def synthesize(self, approved_text: str, turn_id: TurnId, generation_id: GenerationId,
                   render_request_id: str) -> AsyncIterator[AudioChunk]:
        return self._synthesize(approved_text, turn_id, generation_id, render_request_id)

    async def cancel(self, render_request_id: str) -> None:
        self.cancelled.add(render_request_id)
