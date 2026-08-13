"""Repeat Gemini Live input transcription against one caller-supplied WAV."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from voice.config import VoiceSettings


REFERENCE_TEXT = (
    "Komisioni për shlyerje të parakohshme të kredisë për shtëpi është "
    "nga 0.00% deri në 2.00%."
)


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_index: int
    input_transcript: str
    char_length: int
    identical_to_reference: bool
    classification: str
    sdk_error: str | None


def _append_transcript(current: str, update: str) -> str:
    """Accept either cumulative or incremental Live transcription updates."""
    if update.startswith(current):
        return update
    if current.endswith(update):
        return current
    return current + update


def _minor_normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    characters = (
        character
        for character in decomposed
        if not unicodedata.combining(character)
        and not unicodedata.category(character).startswith("P")
    )
    return "".join(characters)


def _content_words(text: str) -> set[str]:
    words = set(re.findall(r"\w+", _minor_normalize(text), flags=re.UNICODE))
    return words - {"e", "i", "me", "nga", "ne", "per", "se", "te"}


def _classify(transcript: str, error: str | None) -> str:
    if error or not transcript.strip():
        return "UNCLASSIFIED"
    if transcript == REFERENCE_TEXT:
        return "EXACT"
    if _minor_normalize(transcript) == _minor_normalize(REFERENCE_TEXT):
        return "MINOR"
    reference_words = _content_words(REFERENCE_TEXT)
    transcript_words = _content_words(transcript)
    overlap = reference_words & transcript_words
    if len(overlap) >= 3 or (
        overlap
        and len(overlap) / min(len(reference_words), len(transcript_words)) >= 0.4
    ):
        return "DEGRADED"
    if len(transcript_words) >= 4:
        return "FABRICATED"
    return "UNCLASSIFIED"


def _read_wav(path: Path) -> tuple[bytes, int]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            properties = (
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getcomptype(),
            )
            sample_rate_hz = wav_file.getframerate()
            pcm = wav_file.readframes(wav_file.getnframes())
    except (EOFError, OSError, wave.Error) as exc:
        raise RuntimeError(f"invalid input WAV {path}: {exc}") from exc
    if properties != (1, 2, "NONE"):
        raise RuntimeError(
            "input must be mono 16-bit PCM WAV; "
            f"got channels={properties[0]}, sample_width={properties[1]}, "
            f"compression={properties[2]}"
        )
    if not pcm or sample_rate_hz <= 0:
        raise RuntimeError("input WAV contains no audio frames")
    return pcm, sample_rate_hz


async def _transcribe_once(
    client: Any,
    model_id: str,
    config: types.LiveConnectConfig,
    pcm: bytes,
    sample_rate_hz: int,
) -> str:
    transcript = ""
    connection = client.aio.live.connect(model=model_id, config=config)
    session: Any = None
    try:
        session = await connection.__aenter__()
        await session.send_realtime_input(activity_start={})
        chunk_bytes = max(2, sample_rate_hz * 2 // 10)
        for offset in range(0, len(pcm), chunk_bytes):
            frame = pcm[offset : offset + chunk_bytes]
            await session.send_realtime_input(
                audio={
                    "data": frame,
                    "mime_type": f"audio/pcm;rate={sample_rate_hz}",
                }
            )
            await asyncio.sleep(len(frame) / (sample_rate_hz * 2))
        await session.send_realtime_input(activity_end={})
        async for message in session.receive():
            server = message.server_content
            if server and server.input_transcription and server.input_transcription.text:
                transcript = _append_transcript(
                    transcript, str(server.input_transcription.text)
                )
            if server and server.turn_complete:
                break
        return transcript.strip()
    finally:
        # Live sessions bill for their duration. Always close the connection,
        # including SDK-error and cancellation paths.
        if session is not None:
            await connection.__aexit__(None, None, None)


def _print_table(records: list[RunRecord]) -> None:
    headings = ("run_index", "input_transcript", "char_length", "identical", "classification", "sdk_error")
    rows = [
        (
            str(record.run_index),
            json.dumps(record.input_transcript, ensure_ascii=False),
            str(record.char_length),
            str(record.identical_to_reference),
            record.classification,
            record.sdk_error or "",
        )
        for record in records
    ]
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        for index in range(len(headings))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headings)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


async def _run(wav_path: Path, runs: int) -> None:
    if runs <= 0:
        raise ValueError("--runs must be greater than zero")
    pcm, sample_rate_hz = _read_wav(wav_path)
    settings = VoiceSettings.from_env()
    api_key = settings.require_gemini_live()["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=["sq-AL"]
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=["sq-AL"]
        ),
        speech_config=types.SpeechConfig(language_code="sq"),
        system_instruction=(
            "Respond briefly to the caller in Albanian. Your response is not "
            "authoritative and will be discarded by the application."
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
    )
    records: list[RunRecord] = []
    for run_index in range(1, runs + 1):
        transcript = ""
        error: str | None = None
        try:
            transcript = await _transcribe_once(
                client,
                settings.gemini_live_model,
                config,
                pcm,
                sample_rate_hz,
            )
        except Exception as exc:  # Each SDK failure is evidence; never retry it.
            error = f"{type(exc).__name__}: {exc}"
        records.append(
            RunRecord(
                run_index=run_index,
                input_transcript=transcript,
                char_length=len(transcript),
                identical_to_reference=transcript == REFERENCE_TEXT,
                classification=_classify(transcript, error),
                sdk_error=error,
            )
        )

    print(f"wav: {wav_path}")
    print(f"reference_text: {REFERENCE_TEXT}")
    print(f"sample_rate_hz: {sample_rate_hz}")
    print(
        "classification_rule: EXACT means byte-for-byte text equality; MINOR means "
        "equality after case-folding and removing punctuation/diacritics; DEGRADED "
        "means non-minor text with at least three shared non-stopwords, or at least "
        "40% non-stopword overlap against the shorter text; "
        "FABRICATED means at least four non-stopwords and no non-stopword reference "
        "overlap; SDK errors, empty text, and shorter unrelated text are UNCLASSIFIED."
    )
    _print_table(records)
    fabrication_count = sum(record.classification == "FABRICATED" for record in records)
    print(f"fabrication_count: {fabrication_count}/{runs}")
    print(
        "json: "
        + json.dumps(
            {
                "wav": str(wav_path),
                "reference_text": REFERENCE_TEXT,
                "sample_rate_hz": sample_rate_hz,
                "fabrication_count": f"{fabrication_count}/{runs}",
                "runs": [asdict(record) for record in records],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", required=True, type=Path, help="mono 16-bit PCM WAV fixture")
    parser.add_argument("--runs", type=int, default=5, help="number of independent Live sessions")
    args = parser.parse_args()
    asyncio.run(_run(args.wav, args.runs))


if __name__ == "__main__":
    main()
