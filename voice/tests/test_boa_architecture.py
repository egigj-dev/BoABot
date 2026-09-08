"""Architecture invariants for the two voice transport arms."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from voice.arm_a.asr.fake_adapter import FakeStreamingASR
from voice.arm_a.schema1 import Schema1Orchestrator
from voice.arm_b.live_bridge import LiveTurnBridge
from voice.arm_b.schema2 import (
    ConstrainedLiveBridge,
    GeminiLiveTranscriptionPipeline,
    OutputAudioGate,
)
from voice.shared.boa_client import BoaClient
from voice.shared.config import VoiceSettings
from voice.shared.correlation import CorrelationRegistry
from voice.shared.events import AudioChunk, Transcript
from voice.shared.fidelity_guard import FidelityGuard
from voice.shared.metrics import VoiceMetrics
from voice.shared.mock_turn import ScriptedTurnService
from voice.shared.schemas import VoiceUserTurn
from voice.shared.telephony import SimulatedCallControl
from voice.shared.tts.fake_tts import FakeTTS


async def _audio() -> AsyncIterator[bytes]:
    yield b"pcm"


async def _sink(_chunk: AudioChunk) -> None:
    return None


def test_shared_boa_adapter_keeps_request_semantics_source_neutral() -> None:
    async def scenario() -> None:
        service = ScriptedTurnService("Përgjigje.", session_id="boa-session")
        boa = BoaClient(service)
        for source in ("cascade", "s2s"):
            response = await boa.process_boa_turn(
                VoiceUserTurn("shared-session", "Sa kushton?", source)
            )
            assert response.session_id == "boa-session"
            assert response.text == "Përgjigje."
        assert [(request.question, request.session_id) for request in service.requests] == [
            ("Sa kushton?", "shared-session"),
            ("Sa kushton?", "shared-session"),
        ]

    asyncio.run(scenario())


def test_shared_boa_adapter_rejects_partials_before_any_boa_request() -> None:
    async def scenario() -> None:
        service = ScriptedTurnService("must not run")
        boa = BoaClient(service)
        with pytest.raises(ValueError, match="committed final"):
            await boa.process_boa_turn(
                VoiceUserTurn("session", "interim transcript", "cascade", is_final=False)
            )
        assert service.requests == []

    asyncio.run(scenario())


def test_arm_a_calls_boa_only_for_a_final_asr_transcript() -> None:
    async def scenario() -> None:
        service = ScriptedTurnService("Përgjigje.")
        asr = FakeStreamingASR([
            Transcript("Sa", False, provider="fake"),
            Transcript("Sa kushton?", True, 0.99, provider="fake"),
        ])
        arm = Schema1Orchestrator(
            asr, BoaClient(service), FakeTTS(), SimulatedCallControl(), _sink,
        )
        await arm.open_call("call", "shared-session")
        await arm.run_audio("call", _audio())
        assert [request.question for request in service.requests] == ["Sa kushton?"]
        assert service.requests[0].session_id == "shared-session"

    asyncio.run(scenario())


def test_arm_b_calls_boa_only_for_a_final_live_transcript() -> None:
    async def scenario() -> None:
        class FakeLive:
            async def events(self, _audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
                yield Transcript("Sa", False, provider="fake-live")
                yield Transcript(
                    "Sa kushton?", True, 0.99, provider="fake-live",
                    diagnostics={"stable_final": True},
                )

            async def interrupt(self) -> None:
                return None

            async def close(self) -> None:
                return None

        service = ScriptedTurnService("Përgjigje.")
        registry = CorrelationRegistry()
        bridge = ConstrainedLiveBridge(
            BoaClient(service), FakeTTS(), SimulatedCallControl(),
            OutputAudioGate(registry, _sink, VoiceMetrics()), registry,
            FidelityGuard(), VoiceMetrics(),
        )
        pipeline = GeminiLiveTranscriptionPipeline(FakeLive(), bridge)
        await pipeline.open_call("call", "shared-session")
        await pipeline.run_audio("call", _audio())
        assert [request.question for request in service.requests] == ["Sa kushton?"]
        assert service.requests[0].session_id == "shared-session"

    asyncio.run(scenario())


def test_realtime_bridge_preserves_session_and_renders_boa_handoff_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        service = ScriptedTurnService("Po ju lidh me një agjent.", handoff=True)
        events: list[dict[str, object]] = []

        async def event_sink(event: dict[str, object]) -> None:
            events.append(event)

        bridge = LiveTurnBridge(
            VoiceSettings.from_env(), _sink, event_sink,
            boa_client=BoaClient(service),
        )

        async def fake_transcribe(_audio: AsyncIterable[bytes], _rate: int) -> str:
            return "Nuk e njoh transfertën."

        rendered: list[str] = []

        async def fake_render(text, *_args):
            rendered.append(text)
            return text, 1.0, 2.0

        monkeypatch.setattr(bridge, "_transcribe", fake_transcribe)
        monkeypatch.setattr(bridge, "_render", fake_render)
        audit = await bridge.run_turn(_audio(), session_id="shared-session")
        assert service.requests[0].session_id == "shared-session"
        assert rendered == ["Po ju lidh me një agjent."]
        assert audit.handoff is True
        assert events and events[0]["type"] == "handoff"

    asyncio.run(scenario())
