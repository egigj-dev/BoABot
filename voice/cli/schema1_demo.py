"""Audited offline Schema 1 demo using the production orchestration seam."""

from __future__ import annotations

import argparse
import asyncio
import json

from voice.asr.fake_adapter import FakeStreamingASR
from voice.config import VoiceSettings
from voice.events import AudioChunk, Transcript
from voice.mock_turn import ScriptedTurnService
from voice.schema1 import Schema1Orchestrator
from voice.telephony import SimulatedCallControl
from voice.tts.fake_tts import FakeTTS


async def demo(live: bool = False) -> None:
    settings = VoiceSettings.from_env()
    if live:
        if settings.asr_provider == "azure":
            settings.require_azure_asr()
        elif settings.asr_provider == "chirp":
            settings.require_chirp()
        else:
            raise SystemExit("--live requires BOABOT_ASR_PROVIDER=azure|chirp")
        settings.require_azure_tts()
        raise SystemExit("Live media capture/telephony must be supplied by the deployment adapter")

    question = "Sa është komisioni i administrimit?"
    approved = "Komisioni i administrimit është 10 EUR."
    transcript = Transcript(question, final=True, confidence=0.97, provider="fake")
    asr = FakeStreamingASR([transcript])
    turn = ScriptedTurnService(
        approved, sources=({"id": "rate-demo", "doc": "Tabela e tarifave",
                            "article": "", "url": "https://example.invalid/vetted"},),
        vetted_chunks=(approved,), chunk_chars=9)
    tts = FakeTTS(chunk_size=11)
    call_control = SimulatedCallControl()
    rendered: list[bytes] = []

    async def output(chunk: AudioChunk) -> None:
        rendered.append(chunk.data)
        print(json.dumps({"event": "output.audio", "turn_id": int(chunk.turn_id),
                          "render_id": chunk.render_request_id, "bytes": len(chunk.data),
                          "final": chunk.final}, ensure_ascii=False))

    orchestrator = Schema1Orchestrator(asr, turn, tts, call_control, output, settings=settings)
    await orchestrator.open_call("offline-schema1")
    print(json.dumps({"event": "call.open", "call_id": "offline-schema1"}))
    print(json.dumps({"event": "asr.final", "provider": transcript.provider,
                      "confidence": transcript.confidence, "text": transcript.text}, ensure_ascii=False))

    async def microphone():
        yield b"deterministic-fake-pcm"

    audits = await orchestrator.run_audio("offline-schema1", microphone())
    audit = audits[0]
    print(json.dumps({"event": "turn.done", "outcome": audit.server_outcome,
                      "confidence_action": audit.confidence_action,
                      "handoff": audit.handoff_requested}, ensure_ascii=False))
    print(json.dumps({"event": "audit.authorized_output",
                      "authorized_text": "".join(audit.authorized_text),
                      "rendered_text": b"".join(rendered).decode("utf-8"),
                      "rendered_bytes": audit.rendered_bytes,
                      "only_turn_tokens": b"".join(rendered).decode("utf-8") == approved},
                     ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="validate live provider configuration")
    args = parser.parse_args()
    asyncio.run(demo(args.live))


if __name__ == "__main__":
    main()
