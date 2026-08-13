"""Schema 2 native-answer sink and output-gate invariants."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator
from types import SimpleNamespace

from voice.correlation import CorrelationRegistry
from voice.events import AudioChunk, RenderRequest, Transcript, TurnDone
from voice.fidelity_guard import FidelityGuard
from voice.metrics import VoiceMetrics
from voice.schema2 import (
    ConstrainedLiveBridge,
    GeminiLiveSessionManager,
    GeminiLiveTranscriptionPipeline,
    NativeResponseSink,
    OutputAudioGate,
)
from voice.telephony import SimulatedCallControl
from voice.turn_client import TurnResult, _notify
from voice.tts.fake_tts import FakeTTS


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


def test_live_manager_drops_native_payload_before_yielding_final_transcript() -> None:
    async def scenario() -> None:
        metrics = VoiceMetrics()
        native = NativeResponseSink(metrics)
        manager = GeminiLiveSessionManager(object(), native)  # type: ignore[arg-type]

        class Session:
            async def send_realtime_input(self, **_kwargs):
                return None

            async def receive(self):
                yield SimpleNamespace(
                    data=b"native-audio",
                    text="native-text",
                    session_resumption_update=None,
                    server_content=SimpleNamespace(
                        input_transcription=SimpleNamespace(text="Pyetje shqip"),
                        interim_input_transcription=None,
                        turn_complete=True,
                        interrupted=False,
                    ),
                )

        manager._session = Session()

        async def microphone():
            yield b"pcm"

        source = manager.events(microphone())
        transcript = await anext(source)
        assert transcript.final
        assert transcript.text == "Pyetje shqip"
        assert metrics.counters["native_response_dropped_events"] == 2
        assert metrics.counters["native_response_dropped_bytes"] == 23
        await source.aclose()

    asyncio.run(scenario())


def test_live_transcription_poc_streams_turn_approval_to_azure_seam_with_latency() -> None:
    async def scenario() -> None:
        approved = "Përgjigje e autorizuar."
        first_audio = asyncio.Event()
        turn_completed = False

        class FakeLiveTranscriber:
            async def events(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
                started = time.monotonic()
                async for _frame in audio:
                    yield Transcript("Pyetje", False, provider="gemini_live_fake",
                                     started_s=started)
                yield Transcript(
                    "Pyetje e plotë", True, provider="gemini_live_fake",
                    started_s=started, finalized_s=time.monotonic(),
                    diagnostics={"stable_final": True},
                )

            async def interrupt(self) -> None:
                return None

            async def close(self) -> None:
                return None

        class StreamingTurn:
            async def run(self, request, on_event=None):
                nonlocal turn_completed
                assert request.question == "Pyetje e plotë"
                await _notify(on_event, {"type": "token", "text": approved})
                await _notify(on_event, {"type": "approved_sentence", "text": approved})
                await asyncio.wait_for(first_audio.wait(), timeout=1)
                assert not turn_completed
                turn_completed = True
                done = TurnDone("answer", "live-poc-session")
                await _notify(on_event, {
                    "type": "done", "outcome": done.outcome,
                    "session_id": done.session_id, "sources": [],
                    "handoff": False, "pii_redacted": False, "usage": {},
                })
                return TurnResult((approved,), done, approved_sentences=(approved,))

            async def cancel(self, correlation_key=None):
                return None

        delivered: list[bytes] = []

        async def caller_sink(chunk: AudioChunk) -> None:
            delivered.append(chunk.data)
            if chunk.data:
                first_audio.set()

        metrics = VoiceMetrics()
        registry = CorrelationRegistry()
        control = SimulatedCallControl()
        gate = OutputAudioGate(registry, caller_sink, metrics)
        tts = FakeTTS(chunk_size=6)
        bridge = ConstrainedLiveBridge(
            StreamingTurn(), tts, control, gate, registry, FidelityGuard(), metrics,
        )
        pipeline = GeminiLiveTranscriptionPipeline(FakeLiveTranscriber(), bridge)
        await pipeline.open_call("live-poc")

        async def microphone():
            yield b"fake-pcm"

        audits = await pipeline.run_audio("live-poc", microphone())
        audit = audits[0]
        assert turn_completed
        assert b"".join(delivered).decode() == approved
        assert tts.approved_inputs == [approved]
        assert audit.renderer == "azure"
        assert audit.rendered_sentences == 1
        assert audit.asr_finalize_to_first_approved_ms is not None
        assert audit.first_approved_to_tts_first_byte_ms is not None
        assert audit.asr_finalize_to_tts_first_byte_ms is not None
        assert len(metrics.latencies_ms["gemini_live.asr_finalize_to_first_approved"]) == 1
        assert len(metrics.latencies_ms["gemini_live.first_approved_to_tts_first_byte"]) == 1
        assert len(metrics.latencies_ms["gemini_live.asr_finalize_to_tts_first_byte"]) == 1

    asyncio.run(scenario())
