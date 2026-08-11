"""Schema 2 native-answer sink and output-gate invariants."""

from __future__ import annotations

import asyncio

from voice.correlation import CorrelationRegistry
from voice.events import AudioChunk, RenderRequest
from voice.metrics import VoiceMetrics
from voice.schema2 import NativeResponseSink, OutputAudioGate


def test_live_native_response_never_reaches_output_gate() -> None:
    async def scenario() -> None:
        delivered: list[bytes] = []

        async def caller_sink(chunk: AudioChunk) -> None:
            delivered.append(chunk.data)

        metrics = VoiceMetrics()
        native = NativeResponseSink(metrics)
        registry = CorrelationRegistry()
        registry.open_call("call", "session")
        turn, generation = registry.next_turn("call")
        gate = OutputAudioGate(registry, caller_sink, metrics)
        gate.activate(RenderRequest("approved-render", "call", turn, generation, "approved"))

        native.drop_audio(b"independent live answer")
        native.drop_text("independent live text")
        assert delivered == []
        assert metrics.counters["native_response_dropped_bytes"] > 0

        assert not await gate.forward(AudioChunk(b"wrong id", turn, generation, "native-generation"))
        assert delivered == []
        assert await gate.forward(AudioChunk(b"approved", turn, generation, "approved-render"))
        assert delivered == [b"approved"]

    asyncio.run(scenario())
