"""Local browser microphone interface for the real Arm A single-turn pipeline."""

from __future__ import annotations

import base64
import io
import logging
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from voice.cli.live_run import run_single
from voice.config import VoiceSettings


logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 2_000_000
MAX_AUDIO_SECONDS = 30.0
ACCEPTED_AUDIO_TYPES = {"audio/wav", "audio/wave", "audio/x-wav"}
PAGE = Path(__file__).with_name("arm_a.html").read_text(encoding="utf-8")
ArmARunner = Callable[[Path, Path, VoiceSettings], Awaitable[dict[str, Any]]]
arm_a_runner: ArmARunner = run_single

app = FastAPI(title="BoABot Arm A microphone", docs_url=None, redoc_url=None)


def _wav_duration(payload: bytes, *, enforce_input_limit: bool = False) -> float:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            actual = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            )
            frames = wav.getnframes()
    except (EOFError, OSError, wave.Error) as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    expected = (1, 2, 16_000, "NONE")
    if actual != expected or frames <= 0:
        raise ValueError(
            "audio must be non-empty 16 kHz, 16-bit, mono PCM WAV; "
            f"got channels={actual[0]}, sample_width={actual[1]}, "
            f"sample_rate={actual[2]}, compression={actual[3]}, frames={frames}"
        )
    duration = frames / 16_000
    if enforce_input_limit and duration > MAX_AUDIO_SECONDS:
        raise ValueError(
            f"recording exceeds the {MAX_AUDIO_SECONDS:g}-second limit"
        )
    return duration


async def _read_audio(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ACCEPTED_AUDIO_TYPES:
        raise HTTPException(415, "Content-Type must be audio/wav")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording is too large")
    try:
        _wav_duration(bytes(payload), enforce_input_limit=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return bytes(payload)


def _browser_result(manifest: dict[str, Any], answer_audio: bytes | None) -> dict[str, Any]:
    audio = None
    if answer_audio:
        audio = {
            "mime_type": "audio/wav",
            "data_base64": base64.b64encode(answer_audio).decode("ascii"),
            "bytes": len(answer_audio),
            "duration_s": round(_wav_duration(answer_audio), 3),
        }
    public_sources = [
        {key: str(source.get(key, "")) for key in ("id", "doc", "article", "url")}
        for source in manifest.get("sources", [])
        if isinstance(source, dict)
    ]
    return {
        "outcome": manifest.get("outcome"),
        "handoff": bool(manifest.get("handoff")),
        "transcript": manifest.get("transcript_text", ""),
        "raw_transcript": manifest.get(
            "transcript_raw_text", manifest.get("transcript_text", "")
        ),
        "answer_text": manifest.get("answer_text", ""),
        "sources": public_sources,
        "confidence_action": manifest.get("confidence_action"),
        "confidence_reason": manifest.get("confidence_reason"),
        "stage_latency_ms": manifest.get("stage_latency_ms", {}),
        "guard_failure_after_audio_started": manifest.get(
            "guard_failure_after_audio_started"
        ),
        "audio": audio,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/turn")
async def browser_turn(request: Request) -> JSONResponse:
    payload = await _read_audio(request)
    request_id = uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"boabot-arm-a-{request_id[:8]}-", dir="/tmp"
        ) as temporary:
            workspace = Path(temporary)
            audio_path = workspace / "question.wav"
            out_dir = workspace / "result"
            audio_path.write_bytes(payload)
            manifest = await arm_a_runner(
                audio_path, out_dir, VoiceSettings.from_env()
            )
            answer_path = out_dir / "answer.wav"
            answer_audio = answer_path.read_bytes() if answer_path.is_file() else None
            result = _browser_result(manifest, answer_audio)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Arm A browser turn %s failed", request_id)
        raise HTTPException(
            502, f"Arm A could not complete this turn: {exc}"
        ) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
