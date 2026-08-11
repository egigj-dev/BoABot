"""Audited offline constrained-Live demo with native-answer leakage injection."""

from __future__ import annotations

import argparse
import asyncio
import json

from voice.config import VoiceSettings
from voice.correlation import CorrelationRegistry
from voice.events import AudioChunk, Transcript
from voice.fidelity_guard import FidelityGuard
from voice.metrics import VoiceMetrics
from voice.mock_turn import ScriptedTurnService
from voice.schema2 import ConstrainedLiveBridge, NativeResponseSink, OutputAudioGate
from voice.telephony import SimulatedCallControl
from voice.tts.fake_tts import FakeTTS


async def demo(live: bool = False) -> None:
    settings = VoiceSettings.from_env()
    if live:
        settings.require_gemini_live()
        settings.require_azure_tts()
        raise SystemExit("Live media capture/telephony must be supplied by the deployment adapter")

    metrics = VoiceMetrics()
    registry = CorrelationRegistry()
    registry.open_call("offline-schema2", "pending:offline-schema2", "live-transport-only")
    call_control = SimulatedCallControl()
    await call_control.answer("offline-schema2")
    caller_audio: list[bytes] = []

    async def caller_sink(chunk: AudioChunk) -> None:
        caller_audio.append(chunk.data)

    gate = OutputAudioGate(registry, caller_sink, metrics)
    native = NativeResponseSink(metrics)
    native.drop_audio(b"UNAUTHORIZED LIVE NATIVE ANSWER")
    native.drop_text("UNAUTHORIZED LIVE NATIVE TEXT")
    print(json.dumps({"event": "live.native_response", "action": "dropped",
                      "dropped_bytes": metrics.counters["native_response_dropped_bytes"]}))

    approved = "Ju lutem prisni."
    turn = ScriptedTurnService(approved, chunk_chars=4)
    bridge = ConstrainedLiveBridge(turn, FakeTTS(chunk_size=5), call_control, gate,
                                   registry, FidelityGuard(), metrics)
    audit = await bridge.handle_final(
        "offline-schema2", Transcript("Më ndihmoni", final=True, confidence=0.96,
                                       provider="gemini_live_fake",
                                       diagnostics={"stable_final": True}))
    print(json.dumps({"event": "turn.done", "outcome": audit.server_outcome,
                      "renderer": audit.renderer, "rendered_sentences": audit.rendered_sentences}))
    print(json.dumps({"event": "audit.output_gate", "caller_text": b"".join(caller_audio).decode(),
                      "native_bytes_reached_caller": 0,
                      "authorized_only": b"".join(caller_audio).decode() == approved}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="validate Live and Azure configuration")
    args = parser.parse_args()
    asyncio.run(demo(args.live))


if __name__ == "__main__":
    main()
