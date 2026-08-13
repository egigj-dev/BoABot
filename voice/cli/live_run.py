"""Fail-closed, fully live Schema 1 command-line runner.

Derived structures used by this module:
- ``status_inventory`` maps each database chunk status to its live row count.
- ``preflight_rows`` contains display-only dependency/result pairs for the preflight table.
- ``final_transcripts`` contains all final ASR events emitted for one input WAV.
- ``interim_hypotheses`` contains every Azure interim and its fixture-relative timestamp.
- ``approved_sentences`` contains server-guarded sentences accepted unchanged for Azure TTS.
- ``sentence_wavs`` contains complete correlated WAV payloads returned by real Azure TTS.
- ``stage_latency_ms`` maps each required stage name to one measured latency.
- ``manifest_sources`` contains public citation fields copied from the terminal event.
- ``manifest`` contains the complete, JSON-serializable audit record for one live turn.
- ``audio_files`` contains top-level batch WAV paths in deterministic name order.
- ``run_records`` contains compact pointers to every completed per-file batch manifest.
- ``flagged_turns`` contains unsupported or handed-off batch turns and their transcripts.
- ``runs_summary`` contains batch counts and ``VoiceMetrics`` percentile summaries.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import hashlib
import io
import json
import struct
import time
import uuid
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from voice.asr.azure_adapter import AzureStreamingASR
from voice.config import VoiceSettings
from voice.events import GenerationId, Transcript, TurnId, TurnRequest
from voice.fidelity_guard import FidelityGuard
from voice.metrics import VoiceMetrics
from voice.schema1 import CRITICAL_RE, ConfidenceAction, ConfidencePolicy
from voice.turn_client import TurnClient
from voice.tts.azure_tts import AzureTTS


def _valid_pcm_wav(payload: bytes) -> bool:
    """Return true only for a non-empty PCM WAV payload."""
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            return (
                wav.getcomptype() == "NONE"
                and wav.getnchannels() == 1
                and wav.getsampwidth() == 2
                and wav.getframerate() == 16_000
                and wav.getnframes() > 0
            )
    except (EOFError, wave.Error):
        return False


def _wav_pcm(payload: bytes) -> bytes:
    """Extract 16 kHz mono PCM from one complete Azure sentence WAV."""
    if not _valid_pcm_wav(payload):
        raise RuntimeError("Azure TTS sentence is not a non-empty 16 kHz mono PCM WAV")
    with wave.open(io.BytesIO(payload), "rb") as wav:
        return wav.readframes(wav.getnframes())


def _pcm_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)
    return output.getvalue()


async def _preflight_postgres() -> str:
    from psycopg import connect

    from retrieve import DSN

    def query() -> tuple[int, dict[str, int]]:
        with connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM chunks")
            count = int(cursor.fetchone()[0])
            cursor.execute("SELECT status, count(*) FROM chunks GROUP BY status ORDER BY status")
            status_inventory = {str(status): int(rows) for status, rows in cursor.fetchall()}
        return count, status_inventory

    count, status_inventory = await asyncio.to_thread(query)
    if count <= 0:
        raise RuntimeError("Postgres chunks row count is not greater than zero")
    inventory = ", ".join(f"{key}={value}" for key, value in status_inventory.items())
    return f"chunks={count}; status inventory: {inventory}"


async def _preflight_embedding() -> str:
    from retrieve import model

    probe_embedding = await asyncio.to_thread(
        lambda: model().encode(["provë e shërbimit zanor"], normalize_embeddings=True)
    )
    if getattr(probe_embedding, "shape", None) != (1, 1024):
        raise RuntimeError(f"bge-m3 returned unexpected shape {getattr(probe_embedding, 'shape', None)!r}")
    return f"BAAI/bge-m3 loaded; probe shape={tuple(probe_embedding.shape)}"


async def _preflight_turn(settings: VoiceSettings) -> str:
    client = TurnClient(settings.turn_base_url, settings.first_token_budget_ms)
    saw_done_with_outcome = False

    async def inspect_event(event: dict[str, Any]) -> None:
        nonlocal saw_done_with_outcome
        if event.get("type") == "done" and isinstance(event.get("outcome"), str):
            saw_done_with_outcome = True

    result = await client.run(
        TurnRequest("Përshëndetje.", None, TurnId(0), include_vetted_text=False), inspect_event
    )
    if not saw_done_with_outcome:
        raise RuntimeError("/turn SSE stream had no done event with an outcome field")
    return f"{client.url}; done outcome={result.done.outcome}"


async def _preflight_stt(settings: VoiceSettings) -> str:
    import azure.cognitiveservices.speech as speechsdk

    credentials = settings.require_azure_asr()
    speech_config = speechsdk.SpeechConfig(
        credentials["AZURE_SPEECH_KEY"], credentials["AZURE_SPEECH_REGION"]
    )
    speech_config.speech_recognition_language = "sq-AL"
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16_000, bits_per_sample=16, channels=1
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
    push_stream.write(struct.pack("<h", 0) * 8_000)
    push_stream.close()
    result = await asyncio.to_thread(lambda: recognizer.recognize_once_async().get())
    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.CancellationDetails.from_result(result)
        raise RuntimeError(
            f"Azure STT canceled: reason={details.reason}; "
            f"error_code={details.error_code}; details={details.error_details}"
        )
    if result.reason not in {speechsdk.ResultReason.RecognizedSpeech, speechsdk.ResultReason.NoMatch}:
        raise RuntimeError(f"Azure STT returned unexpected result reason: {result.reason}")
    return f"region={settings.azure_speech_region}; recognition result={result.reason}"


async def _synthesize_bytes(tts: AzureTTS, text: str) -> bytes:
    chunks: list[bytes] = []
    async for chunk in tts.synthesize(text, TurnId(0), GenerationId(0), "preflight"):
        chunks.append(chunk.data)
    return b"".join(chunks)


async def _preflight_tts(settings: VoiceSettings) -> str:
    tts = AzureTTS(settings)
    payload = await _synthesize_bytes(tts, "Përshëndetje.")
    if not payload:
        raise RuntimeError("Azure TTS returned empty audio")
    if not _valid_pcm_wav(payload):
        raise RuntimeError("Azure TTS returned audio without a valid 16 kHz mono PCM WAV header")
    return f"region={settings.azure_tts_region}; voice={settings.azure_tts_voice}; bytes={len(payload)}"


def _read_input_wav(audio_path: Path) -> tuple[bytes, float, int]:
    try:
        with wave.open(str(audio_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate_hz = wav.getframerate()
            frame_count = wav.getnframes()
            compression = wav.getcomptype()
            pcm = wav.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as exc:
        raise RuntimeError(f"invalid input WAV {audio_path}: {exc}") from exc
    if (channels, sample_width, compression) != (1, 2, "NONE"):
        raise RuntimeError(
            "input must be 16-bit mono PCM WAV; "
            f"got channels={channels}, sample_width={sample_width}, "
            f"sample_rate_hz={sample_rate_hz}, compression={compression}"
        )
    if not pcm or frame_count <= 0:
        raise RuntimeError("input WAV contains no audio frames")
    duration_s = frame_count / sample_rate_hz
    if sample_rate_hz != 16_000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, sample_rate_hz, 16_000, None)
    return pcm, duration_s, sample_rate_hz


def _install_azure_event_signal_compat() -> None:
    """Provide SDK 1.51's missing per-handler method for this process only."""
    import azure.cognitiveservices.speech as speechsdk

    if not hasattr(speechsdk.EventSignal, "disconnect"):
        speechsdk.EventSignal.disconnect = (  # type: ignore[attr-defined]
            lambda signal, _handler: signal.disconnect_all()
        )


async def _pcm_frames(pcm: bytes, chunk_bytes: int = 3_200) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), chunk_bytes):
        frame = pcm[offset:offset + chunk_bytes]
        yield frame
        await asyncio.sleep(len(frame) / (16_000 * 2))


def _assert_real_components(
    asr: AzureStreamingASR, tts: AzureTTS, turn_service: TurnClient
) -> None:
    assert isinstance(asr, AzureStreamingASR), "live run ASR is not AzureStreamingASR"
    assert isinstance(tts, AzureTTS), "live run TTS is not AzureTTS"
    assert isinstance(turn_service, TurnClient), "live run turn service is not TurnClient"
    assert not isinstance(asr, __import__("voice.asr.fake_adapter", fromlist=["FakeStreamingASR"]).FakeStreamingASR), "FakeStreamingASR is forbidden in live runs"
    assert not isinstance(tts, __import__("voice.tts.fake_tts", fromlist=["FakeTTS"]).FakeTTS), "FakeTTS is forbidden in live runs"
    assert not isinstance(turn_service, __import__("voice.mock_turn", fromlist=["ScriptedTurnService"]).ScriptedTurnService), "ScriptedTurnService from mock_turn is forbidden in live runs"


async def run_single(
    audio_path: Path, out_dir: Path, settings: VoiceSettings
) -> dict[str, Any]:
    started = time.perf_counter()
    pcm, duration_s, sample_rate_hz = _read_input_wav(audio_path)
    audio_sha256 = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    asr = AzureStreamingASR(settings)
    tts = AzureTTS(settings)
    turn_service = TurnClient(settings.turn_base_url, settings.first_token_budget_ms)
    _install_azure_event_signal_compat()
    _assert_real_components(asr, tts, turn_service)

    final_transcripts: list[Transcript] = []
    interim_hypotheses: list[dict[str, Any]] = []
    asr_first_interim_ms: float | None = None
    stable_interim_ms: float | None = None
    stable_interim_text: str | None = None
    current_interim_text: str | None = None
    current_interim_started_ms: float | None = None
    asr_final_ms: float | None = None
    async with asyncio.timeout(45):
        async for transcript in asr.start(_pcm_frames(pcm)):
            observed_ms = (time.perf_counter() - started) * 1_000
            if not transcript.final and transcript.text.strip():
                interim_hypotheses.append(
                    {"timestamp_ms": round(observed_ms, 3), "text": transcript.text}
                )
                if asr_first_interim_ms is None:
                    asr_first_interim_ms = observed_ms
                if transcript.text != current_interim_text:
                    if (
                        stable_interim_ms is None
                        and current_interim_started_ms is not None
                        and observed_ms - current_interim_started_ms >= 300
                    ):
                        stable_interim_ms = current_interim_started_ms + 300
                        stable_interim_text = current_interim_text
                    current_interim_text = transcript.text
                    current_interim_started_ms = observed_ms
            elif transcript.final and transcript.text.strip():
                final_transcripts.append(transcript)
                asr_final_ms = observed_ms
                if (
                    stable_interim_ms is None
                    and current_interim_started_ms is not None
                    and asr_final_ms - current_interim_started_ms >= 300
                ):
                    stable_interim_ms = current_interim_started_ms + 300
                    stable_interim_text = current_interim_text
    if len(final_transcripts) != 1:
        raise RuntimeError(f"Azure ASR must return exactly one non-empty final transcript; got {len(final_transcripts)}")
    transcript = final_transcripts[0]
    if asr_final_ms is None:
        raise RuntimeError("Azure ASR final latency was not captured")
    stable_before_final_ms = (
        asr_final_ms - stable_interim_ms
        if stable_interim_ms is not None
        else None
    )
    print(
        "asr_interim_hypotheses: "
        + json.dumps(interim_hypotheses, ensure_ascii=False)
    )
    print(
        "asr_stable_interim: "
        + json.dumps(
            {
                "timestamp_ms": (
                    round(stable_interim_ms, 3)
                    if stable_interim_ms is not None
                    else None
                ),
                "text": stable_interim_text,
                "stable_before_final_ms": (
                    round(stable_before_final_ms, 3)
                    if stable_before_final_ms is not None
                    else None
                ),
            },
            ensure_ascii=False,
        )
    )

    confidence = ConfidencePolicy(
        settings.confidence_proceed, settings.confidence_critical, settings.confidence_handoff
    )
    decision = confidence.effective(transcript)
    critical_spans_detected = CRITICAL_RE.findall(transcript.text)
    if decision.action is not ConfidenceAction.PROCEED:
        end_to_end_complete_ms = (time.perf_counter() - started) * 1_000
        manifest = {
            "all_components_real": True,
            "answer_text": "",
            "asr_model_id": "azure-speech-service-default:sq-AL",
            "asr_input_sample_rate_hz": 16_000,
            "asr_provider": transcript.provider,
            "asr_region": settings.azure_speech_region,
            "audio_out_bytes": 0,
            "audio_out_sha256": None,
            "audio_path": str(audio_path.resolve()),
            "audio_sha256": audio_sha256,
            "confidence_action": decision.action.value,
            "confidence_reason": decision.reason,
            "correlation_errors": [],
            "critical_confidences": transcript.critical_confidences,
            "critical_spans_detected": critical_spans_detected,
            "duration_s": duration_s,
            "fidelity_violations": [],
            "guard_failure_after_audio_started": None,
            "handoff": decision.action is ConfidenceAction.HANDOFF,
            "outcome": decision.action.value,
            "question_sent": None,
            "sample_rate_hz": sample_rate_hz,
            "sources": [],
            "stage_latency_ms": {
                "asr_first_interim": (
                    round(asr_first_interim_ms, 3)
                    if asr_first_interim_ms is not None
                    else None
                ),
                "asr_stable_interim": (
                    round(stable_interim_ms, 3)
                    if stable_interim_ms is not None
                    else None
                ),
                "asr_final": round(asr_final_ms, 3),
                "turn_first_approved_sentence": None,
                "tts_first_byte": None,
                "end_to_end_first_audio": None,
                "end_to_end_complete": round(end_to_end_complete_ms, 3),
            },
            "asr_interim_hypotheses": interim_hypotheses,
            "asr_stable_interim_text": stable_interim_text,
            "asr_stable_before_final_ms": (
                round(stable_before_final_ms, 3)
                if stable_before_final_ms is not None
                else None
            ),
            "transcript_alternatives": list(transcript.alternatives),
            "transcript_confidence": transcript.confidence,
            "transcript_text": transcript.text,
            "tts_provider": "azure",
            "tts_voice": settings.azure_tts_voice,
            "timing_origins": {
                "asr_first_interim": "fixture playback start (fixture-paced)",
                "asr_stable_interim": "fixture playback start (fixture-paced)",
                "asr_final": "fixture playback start (fixture-paced)",
                "end_to_end_first_audio": "fixture playback start (fixture-paced)",
                "turn_first_approved_sentence": "/turn call start",
                "tts_first_byte": "/turn call start",
            },
            "turn_called": False,
            "turn_url": turn_service.url,
            "usage": {},
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"transcript: {transcript.text}")
        print(
            f"confidence: {transcript.confidence}; action={decision.action.value}; "
            f"reason={decision.reason}"
        )
        print("/turn: not called")
        print(f"run.json: {out_dir / 'run.json'}")
        return manifest

    turn_started = time.perf_counter()
    turn_first_token_ms: float | None = None
    turn_first_approved_sentence_ms: float | None = None
    approved_sentences: list[str] = []
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    sentence_wavs: list[bytes] = []
    correlation_errors: list[str] = []
    fidelity_violations: list[str] = []
    guard_failure_after_audio_started: str | None = None
    tts_first_byte_ms: float | None = None
    tts_provider_first_byte_ms: float | None = None
    end_to_end_first_audio_ms: float | None = None

    async def on_turn_event(event: dict[str, Any]) -> None:
        nonlocal turn_first_token_ms, turn_first_approved_sentence_ms
        if event.get("type") == "token" and turn_first_token_ms is None:
            turn_first_token_ms = (time.perf_counter() - turn_started) * 1_000
        if event.get("type") == "approved_sentence":
            sentence = event.get("text")
            if not isinstance(sentence, str) or not sentence.strip():
                raise RuntimeError("/turn emitted an empty approved_sentence")
            if turn_first_approved_sentence_ms is None:
                turn_first_approved_sentence_ms = (
                    time.perf_counter() - turn_started
                ) * 1_000
            approved_sentences.append(sentence)
            # api.py emits this event only after its FidelityGuard accepts the
            # complete sentence. Start the provider without waiting for done.
            await sentence_queue.put(sentence)

    async def render_sentences() -> None:
        nonlocal tts_first_byte_ms, tts_provider_first_byte_ms
        nonlocal end_to_end_first_audio_ms
        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                return
            render_request_id = uuid.uuid4().hex
            sentence_chunks: list[bytes] = []
            async for chunk in tts.synthesize(
                sentence, TurnId(1), GenerationId(1), render_request_id
            ):
                if tts_first_byte_ms is None:
                    tts_first_byte_ms = (
                        time.perf_counter() - turn_started
                    ) * 1_000
                    tts_provider_first_byte_ms = chunk.first_byte_ms
                    end_to_end_first_audio_ms = (
                        time.perf_counter() - started
                    ) * 1_000
                if (
                    chunk.turn_id != TurnId(1)
                    or chunk.generation_id != GenerationId(1)
                    or chunk.render_request_id != render_request_id
                ):
                    correlation_error = (
                        "Azure TTS returned a correlation-mismatched audio chunk"
                    )
                    correlation_errors.append(correlation_error)
                    raise RuntimeError(correlation_error)
                sentence_chunks.append(chunk.data)
            sentence_payload = b"".join(sentence_chunks)
            if not sentence_payload:
                raise RuntimeError("Azure TTS returned no audio for approved sentence")
            _wav_pcm(sentence_payload)
            sentence_wavs.append(sentence_payload)

    renderer_task = asyncio.create_task(render_sentences())

    try:
        result = await turn_service.run(
            TurnRequest(transcript.text.strip(), None, TurnId(1), include_vetted_text=True),
            on_turn_event,
        )
    except BaseException:
        renderer_task.cancel()
        try:
            await renderer_task
        except asyncio.CancelledError:
            pass
        raise
    await sentence_queue.put(None)
    await renderer_task
    answer_text = "".join(result.tokens).strip()
    if not answer_text or not approved_sentences:
        raise RuntimeError("/turn returned no answer text for TTS")
    if turn_first_token_ms is None or turn_first_approved_sentence_ms is None:
        raise RuntimeError("/turn stage latency markers are incomplete")
    guard = FidelityGuard()
    for sentence in approved_sentences:
        verdict = guard.verify_sources(sentence, result.done.sources)
        if not verdict.approved:
            violation = f"{sentence!r}: {verdict.reason}"
            fidelity_violations.append(violation)
            if end_to_end_first_audio_ms is not None:
                guard_failure_after_audio_started = violation
    approved_text = " ".join(approved_sentences)
    output_audio = _pcm_wav(b"".join(_wav_pcm(payload) for payload in sentence_wavs))
    if not output_audio or not _valid_pcm_wav(output_audio):
        raise RuntimeError("Azure TTS answer is not a non-empty 16 kHz mono PCM WAV")
    if tts_first_byte_ms is None or end_to_end_first_audio_ms is None:
        raise RuntimeError("Azure TTS returned no first-byte latency marker")
    out_dir.mkdir(parents=True, exist_ok=True)
    answer_path = out_dir / "answer.wav"
    answer_path.write_bytes(output_audio)
    with wave.open(str(answer_path), "rb") as wav:
        output_duration_s = wav.getnframes() / wav.getframerate()
    if output_duration_s <= 0:
        raise RuntimeError("answer.wav has zero duration")
    end_to_end_complete_ms = (time.perf_counter() - started) * 1_000
    stage_latency_ms = {
        "asr_first_interim": (
            round(asr_first_interim_ms, 3)
            if asr_first_interim_ms is not None
            else None
        ),
        "asr_stable_interim": (
            round(stable_interim_ms, 3)
            if stable_interim_ms is not None
            else None
        ),
        "asr_final": round(asr_final_ms, 3),
        "turn_first_token": round(turn_first_token_ms, 3),
        "turn_first_approved_sentence": round(turn_first_approved_sentence_ms, 3),
        "tts_first_byte": round(tts_first_byte_ms, 3),
        "tts_provider_first_byte": (
            round(tts_provider_first_byte_ms, 3)
            if tts_provider_first_byte_ms is not None
            else None
        ),
        "end_to_end_first_audio": round(end_to_end_first_audio_ms, 3),
        "end_to_end_complete": round(end_to_end_complete_ms, 3),
    }
    manifest_sources = [
        {key: source.get(key, "") for key in ("id", "doc", "article", "url")}
        for source in result.done.sources
    ]
    manifest = {
        "all_components_real": True,
        "answer_text": answer_text,
        "asr_model_id": "azure-speech-service-default:sq-AL",
        "asr_input_sample_rate_hz": 16_000,
        "asr_provider": transcript.provider,
        "asr_region": settings.azure_speech_region,
        "asr_interim_hypotheses": interim_hypotheses,
        "asr_stable_interim_text": stable_interim_text,
        "asr_stable_before_final_ms": (
            round(stable_before_final_ms, 3)
            if stable_before_final_ms is not None
            else None
        ),
        "audio_out_bytes": len(output_audio),
        "audio_out_sha256": hashlib.sha256(output_audio).hexdigest(),
        "audio_path": str(audio_path.resolve()),
        "audio_sha256": audio_sha256,
        "confidence_action": decision.action.value,
        "confidence_reason": decision.reason,
        "correlation_errors": correlation_errors,
        "critical_confidences": transcript.critical_confidences,
        "critical_spans_detected": critical_spans_detected,
        "duration_s": duration_s,
        "fidelity_violations": fidelity_violations,
        "guard_failure_after_audio_started": guard_failure_after_audio_started,
        "handoff": result.done.handoff,
        "outcome": result.done.outcome,
        "question_sent": transcript.text.strip(),
        "sample_rate_hz": sample_rate_hz,
        "sources": manifest_sources,
        "stage_latency_ms": stage_latency_ms,
        "transcript_alternatives": list(transcript.alternatives),
        "transcript_confidence": transcript.confidence,
        "transcript_text": transcript.text,
        "tts_provider": "azure",
        "tts_voice": settings.azure_tts_voice,
        "timing_origins": {
            "asr_first_interim": "fixture playback start (fixture-paced)",
            "asr_stable_interim": "fixture playback start (fixture-paced)",
            "asr_final": "fixture playback start (fixture-paced)",
            "end_to_end_first_audio": "fixture playback start (fixture-paced)",
            "turn_first_approved_sentence": "/turn call start",
            "tts_first_byte": "/turn call start",
            "tts_provider_first_byte": "sentence synthesis start",
        },
        "turn_called": True,
        "turn_url": turn_service.url,
        "usage": result.done.usage,
    }
    (out_dir / "run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"transcript: {transcript.text}")
    print(f"confidence: {transcript.confidence}; action={decision.action.value}; reason={decision.reason}")
    print(f"/turn: outcome={result.done.outcome}; handoff={result.done.handoff}; sources={len(result.done.sources)}")
    print(f"answer.wav: {answer_path}; bytes={len(output_audio)}; duration_s={output_duration_s:.3f}")
    print(f"run.json: {out_dir / 'run.json'}")
    if guard_failure_after_audio_started is not None:
        raise RuntimeError(
            "FidelityGuard rejected a sentence after audio had already started: "
            f"{guard_failure_after_audio_started}"
        )
    return manifest


async def run_batch(audio_dir: Path, settings: VoiceSettings) -> None:
    if not audio_dir.is_dir():
        raise RuntimeError(f"audio batch directory does not exist: {audio_dir}")
    audio_files = sorted(
        (path for path in audio_dir.iterdir() if path.is_file() and path.suffix.lower() == ".wav"),
        key=lambda path: path.name,
    )
    if not audio_files:
        raise RuntimeError(f"audio batch directory contains no WAV files: {audio_dir}")

    metrics = VoiceMetrics()
    run_records: list[dict[str, Any]] = []
    flagged_turns: list[dict[str, Any]] = []
    batch_runs_dir = audio_dir / "runs"
    for audio_path in audio_files:
        run_dir = batch_runs_dir / audio_path.name
        manifest = await run_single(audio_path, run_dir, settings)
        for stage, milliseconds in manifest["stage_latency_ms"].items():
            metrics.observe(stage, float(milliseconds))
        metrics.outcome(str(manifest["outcome"]), bool(manifest["handoff"]))
        run_records.append({
            "audio_path": manifest["audio_path"],
            "run_json": str((run_dir / "run.json").resolve()),
        })
        if manifest["outcome"] == "unsupported" or manifest["handoff"] is True:
            flagged_turns.append({
                "audio_path": manifest["audio_path"],
                "handoff": manifest["handoff"],
                "outcome": manifest["outcome"],
                "transcript_text": manifest["transcript_text"],
            })

    runs_summary = {
        "count": len(run_records),
        "metrics": metrics.summary(),
        "runs": run_records,
        "unsupported_or_handoff_count": len(flagged_turns),
        "unsupported_or_handoff_turns": flagged_turns,
    }
    summary_path = audio_dir / "runs_summary.json"
    summary_path.write_text(
        json.dumps(runs_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"batch count: {len(run_records)}")
    print(f"unsupported or handoff count: {len(flagged_turns)}")
    for flagged in flagged_turns:
        print(
            f"flagged transcript: {flagged['transcript_text']} "
            f"(outcome={flagged['outcome']}, handoff={flagged['handoff']})"
        )
    print(f"runs_summary.json: {summary_path}")


async def preflight(settings: VoiceSettings) -> None:
    checks = (
        ("Postgres", lambda: _preflight_postgres()),
        ("bge-m3", lambda: _preflight_embedding()),
        ("/turn", lambda: _preflight_turn(settings)),
        ("Azure STT", lambda: _preflight_stt(settings)),
        ("Azure TTS", lambda: _preflight_tts(settings)),
    )
    preflight_rows: list[tuple[str, str, str]] = []
    for dependency, check in checks:
        try:
            detail = await check()
        except Exception as exc:
            preflight_rows.append((dependency, "FAIL", f"{type(exc).__name__}: {exc}"))
            _print_table(preflight_rows)
            raise SystemExit(f"preflight aborted at {dependency}: {type(exc).__name__}: {exc}") from exc
        preflight_rows.append((dependency, "PASS", detail))
    _print_table(preflight_rows)


def _print_table(rows: list[tuple[str, str, str]]) -> None:
    widths = [max(len(title), *(len(row[index]) for row in rows)) for index, title in enumerate(("Dependency", "Gate", "Detail"))]
    print(" | ".join(title.ljust(widths[index]) for index, title in enumerate(("Dependency", "Gate", "Detail"))))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="verify every live dependency")
    parser.add_argument("--audio", type=Path, help="single 16 kHz mono PCM WAV input")
    parser.add_argument("--audio-dir", type=Path, help="directory of WAV files for a live batch")
    parser.add_argument("--out", type=Path, help="output directory for a single live turn")
    args = parser.parse_args()
    if args.preflight:
        if args.audio or args.audio_dir or args.out:
            parser.error("--preflight cannot be combined with audio or output arguments")
        asyncio.run(preflight(VoiceSettings.from_env()))
        return
    if args.audio and args.out:
        if args.audio_dir:
            parser.error("--audio cannot be combined with --audio-dir")
        asyncio.run(run_single(args.audio, args.out, VoiceSettings.from_env()))
        return
    if args.audio_dir:
        if args.audio or args.out:
            parser.error("--audio-dir cannot be combined with --audio or --out")
        asyncio.run(run_batch(args.audio_dir, VoiceSettings.from_env()))
        return
    parser.error("use --preflight, --audio-dir, or provide both --audio and --out")


if __name__ == "__main__":
    main()
