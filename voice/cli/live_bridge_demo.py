"""Run one real arm-B turn and print its JSON audit plus match rates."""

from __future__ import annotations

import argparse
import audioop
import asyncio
import io
import json
import uuid
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from voice.config import VoiceSettings
from voice.events import AudioChunk, GenerationId, TurnId
from voice.live_bridge import LiveTurnBridge
from voice.tts.azure_tts import AzureTTS


def _wav_pcm(payload: bytes, label: str) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav_file:
            properties = (
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
                wav_file.getcomptype(),
            )
            pcm = wav_file.readframes(wav_file.getnframes())
    except (EOFError, OSError, wave.Error) as exc:
        raise RuntimeError(f"invalid {label} WAV: {exc}") from exc
    if properties != (1, 2, 16_000, "NONE"):
        raise RuntimeError(
            f"{label} must be 16 kHz, 16-bit, mono PCM WAV; got {properties!r}"
        )
    if not pcm:
        raise RuntimeError(f"{label} WAV contains no audio")
    return pcm, properties[2]


async def _text_as_caller_audio(text: str, settings: VoiceSettings) -> tuple[bytes, int]:
    """Use the configured real Albanian Azure voice to create demo caller audio."""
    tts = AzureTTS(settings)
    payload = bytearray()
    async for chunk in tts.synthesize(
        text, TurnId(0), GenerationId(0), f"demo-input-{uuid.uuid4().hex}"
    ):
        payload.extend(chunk.data)
    return _wav_pcm(bytes(payload), "synthesized caller")


def _wav_file_as_caller_audio(path: Path) -> tuple[bytes, int]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"cannot read input WAV {path}: {exc}") from exc
    return _wav_pcm(payload, "input")


async def _frames(pcm: bytes, chunk_bytes: int = 3_200) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), chunk_bytes):
        frame = pcm[offset : offset + chunk_bytes]
        yield frame
        await asyncio.sleep(len(frame) / (16_000 * 2))


async def _confirm_turn_server(base_url: str) -> None:
    """Fail loudly if the configured HTTP service cannot be reached."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(base_url)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"/turn server is unreachable at {base_url}: {exc}") from exc
    if response.status_code >= 500:
        raise RuntimeError(
            f"/turn server preflight at {base_url} returned HTTP {response.status_code}"
        )


async def _run(args: argparse.Namespace) -> None:
    settings = VoiceSettings.from_env()
    await _confirm_turn_server(settings.turn_base_url)
    if args.text is not None:
        if not args.text.strip():
            raise ValueError("--text must not be empty")
        pcm, sample_rate_hz = await _text_as_caller_audio(args.text, settings)
    else:
        pcm, sample_rate_hz = _wav_file_as_caller_audio(args.wav)

    output_audio = bytearray()
    handoff_events: list[dict[str, Any]] = []

    async def caller_sink(chunk: AudioChunk) -> None:
        output_audio.extend(chunk.data)

    async def event_sink(event: dict[str, Any]) -> None:
        handoff_events.append(event)

    bridge = LiveTurnBridge(settings, caller_sink, event_sink)
    audit = await bridge.run_turn(_frames(pcm), input_sample_rate_hz=sample_rate_hz)
    record = audit.as_dict()
    if audit.handoff:
        if output_audio:
            raise RuntimeError("handoff turn emitted caller audio")
        if not handoff_events:
            raise RuntimeError("handoff turn emitted no handoff event")
    elif not output_audio:
        raise RuntimeError("approved Live render emitted no caller audio")

    if args.out is not None:
        rate = bridge.output_sample_rate_hz or 16_000
        path = Path(args.out)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(rate)
            wav_file.writeframes(bytes(output_audio))
        print(
            f"audio saved: {path} ({len(output_audio)} pcm bytes, {rate} Hz, "
            f"{len(output_audio) / (2 * rate):.3f}s)"
        )
    else:
        print(
            "audio NOT saved (no --out); per-turn JSON below. Re-run with --out "
            "to capture the approved render for listening."
        )

    if args.out_wav is not None:
        source_rate = bridge.output_sample_rate_hz or 16_000
        pcm_16khz = bytes(output_audio)
        if source_rate != 16_000:
            pcm_16khz, _ = audioop.ratecv(
                pcm_16khz, 2, 1, source_rate, 16_000, None
            )
        path = Path(args.out_wav)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16_000)
            wav_file.writeframes(pcm_16khz)
        print(
            f"16 kHz caller audio saved: {path} ({len(pcm_16khz)} pcm bytes, "
            f"{len(pcm_16khz) / (2 * 16_000):.3f}s)"
        )

    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    print(
        "match_rates: "
        + (
            "verbatim_match=n/a; normalized_match=n/a"
            if audit.verbatim_match is None or audit.normalized_match is None
            else (
                f"verbatim_match={int(audit.verbatim_match)}/1 "
                f"({float(audit.verbatim_match):.3f}); "
                f"normalized_match={int(audit.normalized_match)}/1 "
                f"({float(audit.normalized_match):.3f})"
            )
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="text synthesized into real caller audio")
    source.add_argument("--wav", type=Path, help="16 kHz mono PCM caller WAV")
    parser.add_argument(
        "--out",
        type=Path,
        help="save the approved Live render as a mono 16-bit WAV at this path "
        "(the audio the demo otherwise discards)",
    )
    parser.add_argument(
        "--out-wav",
        type=Path,
        help="save the accumulated caller-facing audio as 16 kHz mono PCM WAV",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
