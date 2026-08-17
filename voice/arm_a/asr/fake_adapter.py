"""Deterministic ASR used by the exact Schema 1 orchestration path offline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Iterable

from ...shared.events import Transcript
from .base import StreamingASR


class FakeStreamingASR(StreamingASR):
    def __init__(self, transcripts: Iterable[Transcript], delay_ms: float = 0) -> None:
        self._script = tuple(transcripts)
        self._delay_s = delay_ms / 1000
        self._cancelled = False

    async def _run(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        async for _frame in audio:
            if self._cancelled:
                return
        for transcript in self._script:
            if self._cancelled:
                return
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield transcript

    def start(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        self._cancelled = False
        return self._run(audio)

    async def stop(self) -> None:
        return None

    async def cancel(self) -> None:
        self._cancelled = True
