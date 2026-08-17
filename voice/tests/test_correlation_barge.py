"""Stale ID and post-barge-in audio rejection invariants."""

from __future__ import annotations

import asyncio

import pytest

from voice.shared.correlation import CorrelationError, CorrelationRegistry
from voice.shared.barge_in import BargeInCoordinator
from voice.shared.events import AudioChunk, RenderRequest
from voice.shared.metrics import VoiceMetrics
from voice.arm_b.schema2 import OutputAudioGate


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


def test_barge_in_cancels_only_the_call_and_its_active_render_ids() -> None:
    async def scenario() -> None:
        events: list[str] = []

        class Turn:
            async def run(self, request, on_event=None):
                raise AssertionError("not used")

            async def cancel(self, correlation_key=None):
                events.append(f"turn:{correlation_key}")

        class TTS:
            def synthesize(self, approved_text, turn_id, generation_id, render_request_id):
                raise AssertionError("not used")

            async def cancel(self, render_request_id):
                events.append(f"tts:{render_request_id}")

        class Playback:
            def stop(self):
                events.append("playback:stop")

        registry = CorrelationRegistry()
        registry.open_call("call-a", "session-a")
        turn, generation = registry.next_turn("call-a")
        registry.register_render("call-a", turn, "render-a", generation)
        old_generation = generation
        coordinator = BargeInCoordinator(registry, Turn(), TTS(), Playback())

        await coordinator.speech_started("call-a")

        assert events == ["playback:stop", "turn:call-a", "tts:render-a"]
        assert registry.require("call-a").generation_id != old_generation
        assert registry.active_render_ids("call-a") == ()

    asyncio.run(scenario())
