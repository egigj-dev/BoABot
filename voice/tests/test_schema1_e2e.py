"""Schema 1 fake-adapter end-to-end authorization invariant."""

from __future__ import annotations

import asyncio

from voice.asr.fake_adapter import FakeStreamingASR
from voice.events import AudioChunk, Transcript
from voice.mock_turn import ScriptedTurnService
from voice.schema1 import Schema1Orchestrator
from voice.telephony import SimulatedCallControl
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
