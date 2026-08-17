"""Local browser microphone interface for the real Arm B live bridge."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import uuid
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..shared.config import VoiceSettings
from ..shared.events import AudioChunk
from .live_bridge import LiveTurnBridge


logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 2_000_000
MAX_AUDIO_SECONDS = 30.0
ACCEPTED_AUDIO_TYPES = {"audio/wav", "audio/wave", "audio/x-wav"}
PAGE = Path(__file__).with_name("arm_b.html").read_text(encoding="utf-8")
ArmBRunner = Callable[
    [bytes, VoiceSettings], Awaitable[tuple[dict[str, Any], bytes, int]]
]

app = FastAPI(title="BoABot Arm B microphone", docs_url=None, redoc_url=None)


def _read_pcm_wav(payload: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            actual = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            )
            frames = wav.getnframes()
            pcm = wav.readframes(frames)
    except (EOFError, OSError, wave.Error) as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    expected = (1, 2, 16_000, "NONE")
    if actual != expected or frames <= 0 or not pcm:
        raise ValueError(
            "audio must be non-empty 16 kHz, 16-bit, mono PCM WAV; "
            f"got channels={actual[0]}, sample_width={actual[1]}, "
            f"sample_rate={actual[2]}, compression={actual[3]}, frames={frames}"
        )
    if frames / 16_000 > MAX_AUDIO_SECONDS:
        raise ValueError(f"recording exceeds the {MAX_AUDIO_SECONDS:g}-second limit")
    return pcm


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
        return _read_pcm_wav(bytes(payload))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _pcm_frames(pcm: bytes, chunk_bytes: int = 3_200) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), chunk_bytes):
        frame = pcm[offset : offset + chunk_bytes]
        yield frame
        await asyncio.sleep(len(frame) / (16_000 * 2))


def _pcm_wav(pcm: bytes, sample_rate_hz: int) -> bytes:
    if not pcm or len(pcm) % 2:
        raise RuntimeError("Arm B returned invalid 16-bit PCM audio")
    if not 8_000 <= sample_rate_hz <= 96_000:
        raise RuntimeError(f"Arm B returned invalid PCM rate: {sample_rate_hz}")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm)
    return output.getvalue()


async def run_arm_b(
    pcm: bytes, settings: VoiceSettings
) -> tuple[dict[str, Any], bytes, int]:
    output_audio = bytearray()
    handoff_events: list[dict[str, Any]] = []

    async def audio_sink(chunk: AudioChunk) -> None:
        output_audio.extend(chunk.data)

    async def event_sink(event: dict[str, Any]) -> None:
        handoff_events.append(event)

    bridge = LiveTurnBridge(settings, audio_sink, event_sink)
    audit = await bridge.run_turn(
        _pcm_frames(pcm), input_sample_rate_hz=16_000
    )
    record = audit.as_dict()
    if audit.handoff:
        if output_audio:
            raise RuntimeError("Arm B handoff emitted caller audio")
        if not handoff_events:
            raise RuntimeError("Arm B handoff emitted no handoff event")
    elif not output_audio:
        raise RuntimeError("Arm B approved render emitted no caller audio")
    return record, bytes(output_audio), bridge.output_sample_rate_hz or 16_000


arm_b_runner: ArmBRunner = run_arm_b


def _browser_result(
    audit: dict[str, Any], output_pcm: bytes, sample_rate_hz: int
) -> dict[str, Any]:
    audio = None
    if output_pcm:
        wav = _pcm_wav(output_pcm, sample_rate_hz)
        audio = {
            "mime_type": "audio/wav",
            "data_base64": base64.b64encode(wav).decode("ascii"),
            "bytes": len(wav),
            "duration_s": round(len(output_pcm) / (sample_rate_hz * 2), 3),
            "sample_rate_hz": sample_rate_hz,
        }
    sources = [
        {key: str(source.get(key, "")) for key in ("id", "doc", "article", "url")}
        for source in audit.get("sources", [])
        if isinstance(source, dict)
    ]
    handoff = bool(audit.get("handoff"))
    outcome = audit.get("turn_outcome")
    response_status = (
        f"/turn returned {outcome or 'handoff'}; Arm B safely suppressed answer audio."
        if handoff
        else "Approved /turn text was sent to the literal Gemini renderer."
    )
    return {
        "outcome": outcome,
        "handoff": handoff,
        "response_status": response_status,
        "transcript": audit.get("input_transcript", ""),
        "approved_text": audit.get("approved_text"),
        "spoken_transcript": audit.get("spoken_transcript"),
        "verbatim_match": audit.get("verbatim_match"),
        "normalized_match": audit.get("normalized_match"),
        "live_model_id": audit.get("live_model_id", ""),
        "native_response_dropped_events": audit.get(
            "native_response_dropped_events", 0
        ),
        "native_response_dropped_bytes": audit.get(
            "native_response_dropped_bytes", 0
        ),
        "stage_latency_ms": audit.get("stage_latency_ms", {}),
        "sources": sources,
        "audio": audio,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> dict[str, bool | str]:
    return {"ok": True, "arm": "B"}


@app.post("/api/turn")
async def browser_turn(request: Request) -> JSONResponse:
    pcm = await _read_audio(request)
    request_id = uuid.uuid4().hex
    try:
        audit, output_pcm, sample_rate_hz = await arm_b_runner(
            pcm, VoiceSettings.from_env()
        )
        result = _browser_result(audit, output_pcm, sample_rate_hz)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Arm B browser turn %s failed", request_id)
        raise HTTPException(
            502, f"Arm B could not complete this turn: {exc}"
        ) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
