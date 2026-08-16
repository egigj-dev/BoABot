"""Arm B `LiveTurnBridge._transcribe` finalization invariant.

Only a Gemini Live input transcript that the provider has actually marked
complete (`server_content.turn_complete`) may ever be returned for submission
to `/turn`. If the Live `receive()` stream ends for any reason (reconnect,
provider timeout, closed session) before `turn_complete` is observed, the
partial text accumulated so far is an interim hypothesis and must never reach
`/turn` as though it were the caller's accepted final question.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import google.genai as genai_module

from voice.config import VoiceSettings
from voice.live_bridge import LiveTurnBridge


def _transcription_message(text: str | None, *, turn_complete: bool) -> SimpleNamespace:
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text=text) if text else None,
            output_transcription=None,
            model_turn=None,
            interrupted=False,
            turn_complete=turn_complete,
        )
    )


class _FakeSession:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self._messages = messages
        self.sent: list[dict[str, object]] = []

    async def send_realtime_input(self, **kwargs: object) -> None:
        self.sent.append(kwargs)

    async def receive(self):
        for message in self._messages:
            yield message


class _FakeConnectContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _fake_client_factory(session: _FakeSession):
    class FakeClient:
        def __init__(self, api_key: str | None = None) -> None:
            live = SimpleNamespace(
                connect=lambda model=None, config=None: _FakeConnectContext(session)
            )
            self.aio = SimpleNamespace(live=live)

    return FakeClient


async def _one_frame():
    yield b"\x00\x00" * 100


def _settings(monkeypatch: pytest.MonkeyPatch) -> VoiceSettings:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    return VoiceSettings.from_env()


async def _null_sink(_chunk) -> None:
    return None


def test_transcribe_rejects_unfinalized_transcript_when_stream_ends_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: the Live stream ends without ever signaling turn_complete."""
    session = _FakeSession([_transcription_message("Sa është", turn_complete=False)])
    monkeypatch.setattr(genai_module, "Client", _fake_client_factory(session))
    bridge = LiveTurnBridge(_settings(monkeypatch), _null_sink)

    with pytest.raises(RuntimeError, match="turn_complete"):
        import asyncio

        asyncio.run(bridge._transcribe(_one_frame(), 16_000))


def test_transcribe_accepts_transcript_when_turn_complete_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        [
            _transcription_message("Sa është", turn_complete=False),
            _transcription_message(" interesi?", turn_complete=True),
        ]
    )
    monkeypatch.setattr(genai_module, "Client", _fake_client_factory(session))
    bridge = LiveTurnBridge(_settings(monkeypatch), _null_sink)

    import asyncio

    text = asyncio.run(bridge._transcribe(_one_frame(), 16_000))
    assert text == "Sa është interesi?"


def test_transcribe_drops_native_answer_content_from_input_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native model text/audio on the input session is counted and never returned."""
    native_reply = SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=None,
            output_transcription=None,
            model_turn=SimpleNamespace(
                parts=[SimpleNamespace(inline_data=None, text="native answer prose")]
            ),
            interrupted=False,
            turn_complete=False,
        )
    )
    session = _FakeSession(
        [native_reply, _transcription_message("Faleminderit", turn_complete=True)]
    )
    monkeypatch.setattr(genai_module, "Client", _fake_client_factory(session))
    bridge = LiveTurnBridge(_settings(monkeypatch), _null_sink)

    import asyncio

    text = asyncio.run(bridge._transcribe(_one_frame(), 16_000))
    assert text == "Faleminderit"
    assert "native answer prose" not in text
    assert bridge.metrics.counters["native_response_dropped_events"] == 1
