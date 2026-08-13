"""Schema 1 fake-adapter end-to-end authorization invariant."""

from __future__ import annotations

import asyncio

from voice.asr.fake_adapter import FakeStreamingASR
from voice.events import AudioChunk, Transcript, TurnDone
from voice.mock_turn import ScriptedTurnService
from voice.schema1 import Schema1Orchestrator
from voice.telephony import SimulatedCallControl
from voice.turn_client import TurnResult, _notify
from voice.tts.fake_tts import FakeTTS


def test_schema1_speaks_only_turn_authorized_output() -> None:
    async def scenario() -> None:
        approved = "Komisioni i administrimit është 10 EUR."
        turn = ScriptedTurnService(
            approved, sources=({"id": "vetted", "doc": "Tarifat", "article": "",
                                "url": "https://example.invalid/vetted"},),
            vetted_chunks=(approved,))
        asr = FakeStreamingASR([Transcript("Sa është komisioni?", True, 0.97, provider="fake")])
        tts = FakeTTS(chunk_size=7)
        output: list[bytes] = []

        async def sink(chunk: AudioChunk) -> None:
            output.append(chunk.data)

        orchestrator = Schema1Orchestrator(asr, turn, tts, SimulatedCallControl(), sink)
        await orchestrator.open_call("call-1")

        async def audio():
            yield b"fake-mic-frame"

        audits = await orchestrator.run_audio("call-1", audio())
        assert b"".join(output).decode() == approved
        assert tts.approved_inputs == [approved]
        assert turn.requests[0].question == "Sa është komisioni?"
        assert audits[0].authorized_text == [approved]

    asyncio.run(scenario())
def test_schema1_starts_audio_before_turn_done() -> None:
    async def scenario() -> None:
        audio_started = asyncio.Event()
        turn_finished = False
        approved = "Përgjigje e autorizuar."

        class StreamingTurn:
            async def run(self, request, on_event=None):
                nonlocal turn_finished
                await _notify(on_event, {"type": "token", "text": approved})
                await _notify(on_event, {"type": "approved_sentence", "text": approved})
                await asyncio.wait_for(audio_started.wait(), timeout=1)
                assert not turn_finished
                turn_finished = True
                done = TurnDone("answer", "streaming-session")
                await _notify(on_event, {
                    "type": "done", "outcome": "answer",
                    "session_id": done.session_id, "sources": [],
                    "handoff": False, "pii_redacted": False, "usage": {},
                })
                return TurnResult((approved,), done, approved_sentences=(approved,))

            async def cancel(self, correlation_key=None):
                return None

        async def sink(chunk: AudioChunk) -> None:
            if chunk.data:
                audio_started.set()

        orchestrator = Schema1Orchestrator(
            FakeStreamingASR([]), StreamingTurn(), FakeTTS(),
            SimulatedCallControl(), sink,
        )
        await orchestrator.open_call("call-streaming")
        audit = await orchestrator.handle_final(
            "call-streaming",
            Transcript("Më tregoni rregulloren", True, 0.97, provider="fake"),
        )
        assert turn_finished
        assert audio_started.is_set()
        assert audit.authorized_text == [approved]

    asyncio.run(scenario())
