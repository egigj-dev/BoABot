"""Stale ID and post-barge-in audio rejection invariants."""

from __future__ import annotations

import asyncio

import pytest

from voice.correlation import CorrelationError, CorrelationRegistry
from voice.events import AudioChunk, RenderRequest
from voice.metrics import VoiceMetrics
from voice.schema2 import OutputAudioGate


def test_correlation_registry_rejects_stale_id() -> None:
    registry = CorrelationRegistry()
    registry.open_call("c", "s")
    old_turn, old_generation = registry.next_turn("c")
    registry.next_turn("c")
    with pytest.raises(CorrelationError):
        registry.validate("c", old_turn, old_generation)


def test_barge_in_generation_invalidation_rejects_old_frame() -> None:
    async def scenario() -> None:
        delivered: list[bytes] = []

        async def sink(chunk: AudioChunk) -> None:
            delivered.append(chunk.data)

        registry = CorrelationRegistry()
        registry.open_call("c", "s")
        turn, generation = registry.next_turn("c")
        gate = OutputAudioGate(registry, sink, VoiceMetrics())
        gate.activate(RenderRequest("render-old", "c", turn, generation, "approved"))
        registry.invalidate_generation("c")  # speech-start/barge-in
        admitted = await gate.forward(AudioChunk(b"stale", turn, generation, "render-old"))
        assert not admitted
        assert delivered == []

    asyncio.run(scenario())
