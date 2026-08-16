"""Offline contract tests for the local Arm A browser microphone interface."""

from __future__ import annotations

import io
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from voice import web_app


def _wav(milliseconds: int = 100) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * (16 * milliseconds))
    return output.getvalue()


def test_page_exposes_microphone_pcm_and_playback_controls() -> None:
    response = TestClient(web_app.app).get("/")
    assert response.status_code == 200
    assert "navigator.mediaDevices.getUserMedia" in response.text
    assert "OfflineAudioContext" in response.text
    assert "audio/wav" in response.text
    assert 'id="record"' in response.text
    assert 'id="stop"' in response.text
    assert "player.id = 'answer-audio'" in response.text


def test_browser_turn_runs_arm_a_and_returns_safe_public_result(monkeypatch) -> None:
    async def fake_runner(audio_path: Path, out_dir: Path, _settings):
        assert audio_path.read_bytes() == _wav()
        out_dir.mkdir(parents=True)
        (out_dir / "answer.wav").write_bytes(_wav(250))
        return {
            "outcome": "answer",
            "handoff": False,
            "transcript_text": "Pyetje me mikrofon.",
            "transcript_raw_text": "Pyetje origjinale me mikrofon.",
            "answer_text": "Përgjigje e aprovuar.",
            "sources": [{"id": "demo", "doc": "Burimi", "article": "1", "url": "https://example.invalid", "passage_text": "private"}],
            "confidence_action": "proceed",
            "confidence_reason": "fixture",
            "stage_latency_ms": {"asr_final": 125.0},
            "guard_failure_after_audio_started": None,
        }

    monkeypatch.setattr(web_app, "arm_a_runner", fake_runner)
    response = TestClient(web_app.app).post(
        "/api/turn", content=_wav(), headers={"Content-Type": "audio/wav"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["outcome"] == "answer"
    assert result["transcript"] == "Pyetje me mikrofon."
    assert result["raw_transcript"] == "Pyetje origjinale me mikrofon."
    assert result["answer_text"] == "Përgjigje e aprovuar."
    assert result["audio"]["mime_type"] == "audio/wav"
    assert result["audio"]["bytes"] == len(_wav(250))
    assert "passage_text" not in str(result["sources"])


def test_browser_turn_rejects_non_wav_before_arm_a(monkeypatch) -> None:
    async def forbidden_runner(*_args):
        raise AssertionError("runner must not be called")

    monkeypatch.setattr(web_app, "arm_a_runner", forbidden_runner)
    response = TestClient(web_app.app).post(
        "/api/turn", content=b"not audio", headers={"Content-Type": "audio/webm"}
    )
    assert response.status_code == 415


def test_browser_turn_rejects_malformed_wav_before_arm_a(monkeypatch) -> None:
    async def forbidden_runner(*_args):
        raise AssertionError("runner must not be called")

    monkeypatch.setattr(web_app, "arm_a_runner", forbidden_runner)
    response = TestClient(web_app.app).post(
        "/api/turn", content=b"RIFF-invalid", headers={"Content-Type": "audio/wav"}
    )
    assert response.status_code == 422
