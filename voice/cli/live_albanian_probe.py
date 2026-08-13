"""One-session, fail-loudly Albanian speech probe for Gemini Live."""

from __future__ import annotations

import asyncio
import audioop
import re
import wave
from pathlib import Path

from google import genai
from google.genai import types

from voice.config import VoiceSettings


TEXT = "Komisioni për shlyerje të parakohshme të kredisë për shtëpi është nga 0.00% deri në 2.00%."
OUTPUT_WAV = Path(__file__).with_name("live_albanian_probe.wav")


def _append_transcript(current: str, update: str) -> str:
    """Accept either cumulative or incremental Live transcription updates."""
    if update.startswith(current):
        return update
    if current.endswith(update):
        return current
    return current + update


def _pcm_rate(mime_types: set[str]) -> int:
    rates = {
        int(match.group(1))
        for mime_type in mime_types
        if (match := re.search(r"(?:^|;)\s*rate=(\d+)", mime_type, re.IGNORECASE))
    }
    if len(rates) != 1:
        raise RuntimeError(f"expected one explicit PCM sample rate, got {sorted(mime_types)!r}")
    return rates.pop()


async def _probe() -> None:
    settings = VoiceSettings.from_env()
    api_key = settings.require_gemini_live()["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    available = {
        str(model.name).removeprefix("models/"): tuple(model.supported_actions or ())
        for model in client.models.list(config={"page_size": 1000})
    }
    model_id = settings.gemini_live_model
    if model_id not in available or "bidiGenerateContent" not in available[model_id]:
        live_models = sorted(
            name for name, actions in available.items() if "bidiGenerateContent" in actions
        )
        raise RuntimeError(
            f"configured Live model {model_id!r} is unavailable; available Live models: "
            f"{live_models!r}. Refusing to open a fallback session."
        )

    api_version = client._api_client._http_options.api_version
    websocket_base = client._api_client._websocket_base_url()
    if isinstance(websocket_base, bytes):
        websocket_base = websocket_base.decode("utf-8")
    endpoint = (
        f"{websocket_base}/ws/google.ai.generativelanguage.{api_version}."
        "GenerativeService.BidiGenerateContent"
    )
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=["sq-AL"]
        ),
        speech_config=types.SpeechConfig(language_code="sq"),
        system_instruction=(
            "You are a literal speech renderer. Speak the user's text exactly as written, "
            "with no additions, omissions, translation, explanation, or reformulation."
        ),
    )

    audio = bytearray()
    mime_types: set[str] = set()
    transcript = ""
    transcript_language_codes: set[str] = set()
    warnings: list[str] = []

    # Exactly one real Live session is opened. There is no retry or fallback.
    async with client.aio.live.connect(model=model_id, config=config) as session:
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=TEXT)]),
            turn_complete=True,
        )
        async for message in session.receive():
            server = message.server_content
            if server and server.output_transcription:
                output = server.output_transcription
                if output.text:
                    transcript = _append_transcript(transcript, str(output.text))
                if output.language_code:
                    transcript_language_codes.add(str(output.language_code))
            if server and server.model_turn and server.model_turn.parts:
                for part in server.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        mime_type = str(part.inline_data.mime_type or "")
                        if not mime_type.lower().startswith("audio/pcm"):
                            raise RuntimeError(f"unexpected Live audio MIME type: {mime_type!r}")
                        mime_types.add(mime_type)
                        audio.extend(bytes(part.inline_data.data))
            if server and server.interrupted:
                warnings.append("Live marked the response interrupted")
            if server and server.turn_complete:
                break

    sample_rate = _pcm_rate(mime_types)
    if len(audio) % 2:
        raise RuntimeError(f"odd byte count for 16-bit PCM: {len(audio)}")
    duration_seconds = len(audio) / (sample_rate * 2)
    rms = audioop.rms(audio, 2) if audio else 0
    non_trivial = len(audio) >= sample_rate // 2 and duration_seconds >= 0.25 and rms >= 20
    if not non_trivial:
        raise RuntimeError(
            f"Gate 2 failed: trivial/silent audio bytes={len(audio)}, "
            f"duration_seconds={duration_seconds:.3f}, rms={rms}"
        )

    with wave.open(str(OUTPUT_WAV), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio)

    exact_match = transcript == TEXT
    language_issue = None
    if transcript and not exact_match:
        language_issue = "output transcript differs from the requested Albanian text"
    elif transcript_language_codes and not any(code.casefold().startswith("sq") for code in transcript_language_codes):
        language_issue = f"non-Albanian transcript language code(s): {sorted(transcript_language_codes)!r}"

    print(f"resolved_model_id: {model_id}")
    print(f"endpoint: {endpoint}")
    print(f"response_modalities_requested: ['AUDIO']")
    print(f"response_modalities_granted: ['AUDIO'] (observed PCM audio payloads)")
    print(f"audio_returned: {bool(audio)}")
    print(f"audio_byte_count: {len(audio)}")
    print(f"audio_duration_seconds: {duration_seconds:.3f}")
    print(f"audio_rms: {rms}")
    print(f"audio_wav: {OUTPUT_WAV}")
    print(f"output_transcript: {transcript}")
    print(f"output_transcript_matches_input: {exact_match}")
    print(f"output_transcript_language_codes: {sorted(transcript_language_codes)}")
    print(f"language_error_warning_or_substitution: {language_issue or 'none observed'}")
    print(f"live_warnings: {warnings or 'none'}")
    print(
        "output_language_constraint: speech_config.language_code='sq'; "
        "output_audio_transcription.language_codes=['sq-AL']"
    )
    if language_issue:
        raise RuntimeError(f"Gate 2 failed: {language_issue}")


def main() -> None:
    asyncio.run(_probe())


if __name__ == "__main__":
    main()
