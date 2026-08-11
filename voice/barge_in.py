"""Barge-in cancellation and stale-generation invalidation (Schema 1 §7)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Protocol

from .correlation import CorrelationRegistry
from .events import TurnId
from .turn_client import TurnService
from .tts.base import TTS


class Clearable(Protocol):
    def clear(self) -> Any: ...


class Playback(Protocol):
    def stop(self) -> Any: ...


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


@dataclass(frozen=True, slots=True)
class InterruptionRecord:
    call_id: str
    turn_id: TurnId
    delivered_offset: int


class BargeInCoordinator:
    """Mute first, clear buffers, cancel remote work, then invalidate generation."""

    def __init__(self, registry: CorrelationRegistry, turn_client: TurnService, tts: TTS,
                 playback: Playback, buffers: tuple[Clearable, ...] = ()) -> None:
        self.registry = registry
        self.turn_client = turn_client
        self.tts = tts
        self.playback = playback
        self.buffers = buffers
        self.records: list[InterruptionRecord] = []

    async def speech_started(self, call_id: str, delivered_offset: int = 0) -> InterruptionRecord:
        current = self.registry.require(call_id)
        record = InterruptionRecord(call_id, current.turn_id, max(0, delivered_offset))
        await _maybe_await(self.playback.stop())
        for buffer in self.buffers:
            await _maybe_await(buffer.clear())
        await self.turn_client.cancel()
        await self.tts.cancel(current.turn_id)
        self.registry.invalidate_generation(call_id)
        self.records.append(record)
        return record
