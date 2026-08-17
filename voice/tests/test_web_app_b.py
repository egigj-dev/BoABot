"""Offline contract tests for the local Arm B browser microphone interface."""

from __future__ import annotations

import io
import wave

from fastapi.testclient import TestClient

from voice.arm_b import web_app_b


def _wav(milliseconds: int = 100, sample_rate_hz: int = 16_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(b"\x00\x00" * (sample_rate_hz * milliseconds // 1_000))
    return output.getvalue()


def test_page_exposes_arm_b_microphone_and_audit_controls() -> None:
    response = TestClient(web_app_b.app).get("/")
    assert response.status_code == 200
    assert "BoABot · Arm B" in response.text
    assert "navigator.mediaDevices.getUserMedia" in response.text
    assert "OfflineAudioContext" in response.text
    assert 'id="record"' in response.text
    assert "spoken_transcript" in response.text
    assert "normalized_match" in response.text


def test_browser_turn_runs_arm_b_and_returns_public_audit(monkeypatch) -> None:
    async def fake_runner(pcm: bytes, _settings):
        assert pcm == b"\x00\x00" * 1_600
        return (
            {
                "turn_outcome": "answer",
                "handoff": False,
                "input_transcript": "Pyetje me Gemini Live.",
                "approved_text": "Përgjigje e aprovuar.",
                "spoken_transcript": "Përgjigje e aprovuar.",
                "verbatim_match": True,
                "normalized_match": True,
                "live_model_id": "fixture-live",
                "native_response_dropped_events": 2,
                "native_response_dropped_bytes": 128,
                "stage_latency_ms": {"live_input_final": 100.0},
                "sources": [{
                    "id": "demo", "doc": "Burimi", "article": "1",
                    "url": "https://example.invalid", "passage_text": "private",
                }],
            },
            b"\x00\x00" * 6_000,
            24_000,
        )

    monkeypatch.setattr(web_app_b, "arm_b_runner", fake_runner)
    response = TestClient(web_app_b.app).post(
        "/api/turn", content=_wav(), headers={"Content-Type": "audio/wav"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["outcome"] == "answer"
    assert "literal Gemini renderer" in result["response_status"]
    assert result["transcript"] == "Pyetje me Gemini Live."
    assert result["approved_text"] == result["spoken_transcript"]
    assert result["verbatim_match"] is True
    assert result["audio"]["sample_rate_hz"] == 24_000
    assert result["audio"]["duration_s"] == 0.25
    assert "passage_text" not in str(result["sources"])


def test_browser_turn_handoff_returns_no_audio(monkeypatch) -> None:
    async def fake_runner(_pcm: bytes, _settings):
        return (
            {
                "turn_outcome": "handoff", "handoff": True,
                "input_transcript": "Kam nevojë për agjent.", "sources": [],
            },
            b"",
            16_000,
        )

    monkeypatch.setattr(web_app_b, "arm_b_runner", fake_runner)
    response = TestClient(web_app_b.app).post(
        "/api/turn", content=_wav(), headers={"Content-Type": "audio/wav"}
    )
    assert response.status_code == 200
    assert response.json()["handoff"] is True
    assert "safely suppressed answer audio" in response.json()["response_status"]
    assert response.json()["audio"] is None


def test_browser_turn_rejects_non_wav_before_arm_b(monkeypatch) -> None:
    async def forbidden_runner(*_args):
        raise AssertionError("runner must not be called")

    monkeypatch.setattr(web_app_b, "arm_b_runner", forbidden_runner)
    response = TestClient(web_app_b.app).post(
        "/api/turn", content=b"not audio", headers={"Content-Type": "audio/webm"}
    )
    assert response.status_code == 415


def test_browser_turn_rejects_wrong_sample_rate_before_arm_b(monkeypatch) -> None:
    async def forbidden_runner(*_args):
        raise AssertionError("runner must not be called")

    monkeypatch.setattr(web_app_b, "arm_b_runner", forbidden_runner)
    response = TestClient(web_app_b.app).post(
        "/api/turn", content=_wav(sample_rate_hz=24_000),
        headers={"Content-Type": "audio/wav"},
    )
    assert response.status_code == 422
