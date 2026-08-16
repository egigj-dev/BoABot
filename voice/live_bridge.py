"""Live-gated bridge: Live transcription -> authoritative /turn -> literal Live audio."""

from __future__ import annotations

import inspect
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import VoiceSettings
from .correlation import CorrelationRegistry
from .events import AudioChunk, RenderRequest, TurnId, TurnRequest
from .metrics import VoiceMetrics
from .schema2 import NativeResponseSink, OutputAudioGate
from .turn_client import TurnClient

BridgeEventSink = Callable[[dict[str, Any]], Awaitable[None] | None]
AudioSink = Callable[[AudioChunk], Awaitable[None]]

_DECIMAL_SEPARATORS = {".", ",", "\u066b"}
_FORBIDDEN_LIVE_CONTEXT_KEYS = {"chunks", "passages", "sources", "tool", "tools"}


def _append_transcript(current: str, update: str) -> str:
    """Accept either cumulative or incremental Live transcription updates."""
    if update.startswith(current):
        return update
    if current.endswith(update):
        return current
    return current + update


def normalize_for_match(text: str) -> str:
    """Casefold and remove punctuation, preserving numeric fidelity markers."""
    folded = text.casefold()
    normalized: list[str] = []
    for index, character in enumerate(folded):
        if character == "%":
            normalized.append(character)
            continue
        if character in _DECIMAL_SEPARATORS:
            before = folded[index - 1] if index else ""
            after = folded[index + 1] if index + 1 < len(folded) else ""
            if before.isdigit() and after.isdigit():
                normalized.append(character)
            continue
        if not unicodedata.category(character).startswith("P"):
            normalized.append(character)
    return "".join(normalized).strip()


@dataclass(frozen=True, slots=True)
class LiveBridgeTurn:
    """One JSON-serializable arm-B turn audit."""

    call_id: str
    turn_id: int
    live_model_id: str
    input_transcript: str
    turn_outcome: str
    handoff: bool
    sources: tuple[dict[str, str], ...]
    approved_text: str | None
    spoken_transcript: str | None
    verbatim_match: bool | None
    normalized_match: bool | None
    native_response_dropped_events: int
    native_response_dropped_bytes: int
    stage_latency_ms: dict[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "live_model_id": self.live_model_id,
            "input_transcript": self.input_transcript,
            "turn_outcome": self.turn_outcome,
            "handoff": self.handoff,
            "sources": [dict(source) for source in self.sources],
            "approved_text": self.approved_text,
            "spoken_transcript": self.spoken_transcript,
            "verbatim_match": self.verbatim_match,
            "normalized_match": self.normalized_match,
            "native_response_dropped_events": self.native_response_dropped_events,
            "native_response_dropped_bytes": self.native_response_dropped_bytes,
            "stage_latency_ms": self.stage_latency_ms,
        }


class LiveTurnBridge:
    """Arm B: Live transcribes and renders, while /turn alone authors answers."""

    def __init__(
        self,
        settings: VoiceSettings,
        audio_sink: AudioSink,
        event_sink: BridgeEventSink | None = None,
        metrics: VoiceMetrics | None = None,
    ) -> None:
        self.settings = settings
        self.metrics = metrics or VoiceMetrics()
        self.native_sink = NativeResponseSink(self.metrics)
        self.registry = CorrelationRegistry()
        self.output_gate = OutputAudioGate(self.registry, audio_sink, self.metrics)
        self.event_sink = event_sink
        self.turn_client = TurnClient(
            settings.turn_base_url, settings.first_token_budget_ms
        )
        self.output_sample_rate_hz: int | None = None

    async def run_turn(
        self,
        caller_audio: AsyncIterable[bytes],
        *,
        input_sample_rate_hz: int = 16_000,
        call_id: str | None = None,
    ) -> LiveBridgeTurn:
        """Run one real turn; provider and /turn failures intentionally propagate."""
        started = time.perf_counter()
        call_id = call_id or uuid.uuid4().hex
        self.registry.open_call(call_id, f"pending:{call_id}")
        turn_id, generation_id = self.registry.next_turn(call_id)
        dropped_events_before = self.metrics.counters["native_response_dropped_events"]
        dropped_bytes_before = self.metrics.counters["native_response_dropped_bytes"]

        input_transcript = await self._transcribe(caller_audio, input_sample_rate_hz)
        live_input_final_ms = (time.perf_counter() - started) * 1_000
        if not input_transcript.strip():
            raise RuntimeError("Gemini Live returned no finalized input transcript")

        turn_started = time.perf_counter()
        result = await self.turn_client.run(
            TurnRequest(
                input_transcript.strip(),
                None,
                TurnId(turn_id),
                include_vetted_text=False,
            )
        )
        turn_complete_ms = (time.perf_counter() - turn_started) * 1_000
        self.registry.update_session(call_id, result.done.session_id)

        # Arm C is forbidden: passage_text must not be requested, returned, or
        # included in the only context object handed to the render session.
        assert not result.vetted_chunks, "arm C violation: /turn returned vetted chunks"
        assert all("passage_text" not in source for source in result.done.sources), (
            "arm C violation: a retrieved passage reached the bridge"
        )

        sources = tuple(dict(source) for source in result.done.sources)
        bridge_handoff = result.done.handoff or result.done.outcome in {
            "unsupported",
            "handoff",
        }
        if bridge_handoff:
            await self._emit(
                {
                    "type": "handoff",
                    "call_id": call_id,
                    "turn_id": int(turn_id),
                    "outcome": result.done.outcome,
                }
            )
            return self._audit(
                call_id=call_id,
                turn_id=int(turn_id),
                input_transcript=input_transcript,
                outcome=result.done.outcome,
                handoff=True,
                sources=sources,
                approved_text=None,
                spoken_transcript=None,
                dropped_events_before=dropped_events_before,
                dropped_bytes_before=dropped_bytes_before,
                live_input_final_ms=live_input_final_ms,
                turn_complete_ms=turn_complete_ms,
                live_first_audio_ms=None,
                end_to_end_first_audio_ms=None,
            )

        approved_text = "".join(result.tokens)
        if not approved_text.strip():
            raise RuntimeError(
                f"/turn outcome {result.done.outcome!r} returned no approved answer text"
            )

        render_context = {"approved_text": approved_text}
        assert set(render_context) == {"approved_text"}
        assert not (_FORBIDDEN_LIVE_CONTEXT_KEYS & set(render_context)), (
            "arm C violation: retrieved context would be handed to Live"
        )
        render_id = uuid.uuid4().hex
        self.output_gate.activate(
            RenderRequest(
                render_id,
                call_id,
                turn_id,
                generation_id,
                render_context["approved_text"],
            )
        )
        try:
            spoken_transcript, live_first_audio_ms, end_to_end_first_audio_ms = (
                await self._render(
                    render_context["approved_text"],
                    turn_id,
                    generation_id,
                    render_id,
                    started,
                )
            )
        finally:
            self.output_gate.clear()

        return self._audit(
            call_id=call_id,
            turn_id=int(turn_id),
            input_transcript=input_transcript,
            outcome=result.done.outcome,
            handoff=False,
            sources=sources,
            approved_text=approved_text,
            spoken_transcript=spoken_transcript,
            dropped_events_before=dropped_events_before,
            dropped_bytes_before=dropped_bytes_before,
            live_input_final_ms=live_input_final_ms,
            turn_complete_ms=turn_complete_ms,
            live_first_audio_ms=live_first_audio_ms,
            end_to_end_first_audio_ms=end_to_end_first_audio_ms,
        )

    async def _transcribe(
        self, caller_audio: AsyncIterable[bytes], sample_rate_hz: int
    ) -> str:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        key = self.settings.require_gemini_live()["GEMINI_API_KEY"]
        client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["sq"])
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["sq"])
            ),
            speech_config=types.SpeechConfig(language_code="sq"),
            system_instruction=(
                "The caller speaks Albanian (Shqip). Interpret and transcribe the "
                "input as Albanian; do not translate it into Spanish, English, or "
                "another language. Respond briefly in Albanian. Your response is not "
                "authoritative and will be discarded by the application."
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True
                )
            ),
        )
        transcript = ""
        finalized = False
        async with client.aio.live.connect(
            model=self.settings.gemini_live_model, config=config
        ) as session:
            sent_audio = False
            await session.send_realtime_input(activity_start={})
            async for frame in caller_audio:
                if not frame:
                    continue
                sent_audio = True
                await session.send_realtime_input(
                    audio={
                        "data": frame,
                        "mime_type": f"audio/pcm;rate={sample_rate_hz}",
                    }
                )
            if not sent_audio:
                raise ValueError("caller_audio produced no bytes")
            await session.send_realtime_input(activity_end={})

            async for message in session.receive():
                server = message.server_content
                if server and server.input_transcription and server.input_transcription.text:
                    transcript = _append_transcript(
                        transcript, str(server.input_transcription.text)
                    )
                self._drop_native_message(message)
                if server and server.turn_complete:
                    finalized = True
                    break
        if not finalized:
            # The Live receive stream ended (reconnect, provider timeout, closed
            # session) without ever signaling turn_complete. Whatever text was
            # accumulated so far is an interim hypothesis, not an accepted final
            # transcript, and must never reach /turn.
            raise RuntimeError(
                "Gemini Live input session ended before turn_complete; "
                "no finalized transcript is available"
            )
        return transcript.strip()

    def _drop_native_message(self, message: Any) -> None:
        server = message.server_content
        if server and server.model_turn and server.model_turn.parts:
            for part in server.model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    self.native_sink.drop_audio(bytes(part.inline_data.data))
                if part.text:
                    self.native_sink.drop_text(str(part.text))
        if server and server.output_transcription and server.output_transcription.text:
            self.native_sink.drop_text(str(server.output_transcription.text))
        if server and server.interrupted:
            self.metrics.increment("live_interruptions")

    async def _render(
        self,
        approved_text: str,
        turn_id: Any,
        generation_id: Any,
        render_id: str,
        overall_started: float,
    ) -> tuple[str, float, float]:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        key = self.settings.require_gemini_live()["GEMINI_API_KEY"]
        client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["sq"])
            ),
            speech_config=types.SpeechConfig(language_code="sq"),
            temperature=0,
            system_instruction=(
                "You are a literal speech renderer. Speak the user's text exactly as "
                "written, with no additions, omissions, translation, explanation, or "
                "reformulation."
            ),
        )
        transcript = ""
        audio_seen = False
        mime_types: set[str] = set()
        render_started = time.perf_counter()
        live_first_audio_ms: float | None = None
        end_to_end_first_audio_ms: float | None = None
        async with client.aio.live.connect(
            model=self.settings.gemini_live_model, config=config
        ) as session:
            await session.send_client_content(
                turns=types.Content(
                    role="user", parts=[types.Part(text=approved_text)]
                ),
                turn_complete=True,
            )
            async for message in session.receive():
                server = message.server_content
                if server and server.output_transcription and server.output_transcription.text:
                    transcript = _append_transcript(
                        transcript, str(server.output_transcription.text)
                    )
                if server and server.model_turn and server.model_turn.parts:
                    for part in server.model_turn.parts:
                        if not (part.inline_data and part.inline_data.data):
                            continue
                        data = bytes(part.inline_data.data)
                        mime_type = str(part.inline_data.mime_type or "")
                        if not mime_type.lower().startswith("audio/pcm"):
                            raise RuntimeError(
                                f"unexpected Live audio MIME type: {mime_type!r}"
                            )
                        mime_types.add(mime_type)
                        if live_first_audio_ms is None:
                            live_first_audio_ms = (
                                time.perf_counter() - render_started
                            ) * 1_000
                            end_to_end_first_audio_ms = (
                                time.perf_counter() - overall_started
                            ) * 1_000
                        audio_seen = True
                        forwarded = await self.output_gate.forward(
                            AudioChunk(
                                data,
                                turn_id,
                                generation_id,
                                render_id,
                            )
                        )
                        if not forwarded:
                            raise RuntimeError(
                                "OutputAudioGate rejected approved Live render audio"
                            )
                if server and server.interrupted:
                    raise RuntimeError("Live interrupted the approved render")
                if server and server.turn_complete:
                    break
        if not audio_seen or live_first_audio_ms is None or end_to_end_first_audio_ms is None:
            raise RuntimeError("Live returned no audio for approved_text")
        self.output_sample_rate_hz = self._pcm_rate(mime_types)
        return transcript.strip(), live_first_audio_ms, end_to_end_first_audio_ms

    @staticmethod
    def _pcm_rate(mime_types: set[str]) -> int:
        rates = {
            int(match.group(1))
            for mime_type in mime_types
            if (match := re.search(r"(?:^|;)\s*rate=(\d+)", mime_type, re.I))
        }
        if len(rates) != 1:
            raise RuntimeError(
                f"expected one explicit Live PCM sample rate, got {sorted(mime_types)!r}"
            )
        return rates.pop()

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if inspect.isawaitable(result):
            await result

    def _audit(
        self,
        *,
        call_id: str,
        turn_id: int,
        input_transcript: str,
        outcome: str,
        handoff: bool,
        sources: tuple[dict[str, str], ...],
        approved_text: str | None,
        spoken_transcript: str | None,
        dropped_events_before: int,
        dropped_bytes_before: int,
        live_input_final_ms: float,
        turn_complete_ms: float,
        live_first_audio_ms: float | None,
        end_to_end_first_audio_ms: float | None,
    ) -> LiveBridgeTurn:
        return LiveBridgeTurn(
            call_id=call_id,
            turn_id=turn_id,
            live_model_id=self.settings.gemini_live_model,
            input_transcript=input_transcript,
            turn_outcome=outcome,
            handoff=handoff,
            sources=sources,
            approved_text=approved_text,
            spoken_transcript=spoken_transcript,
            verbatim_match=(
                approved_text == spoken_transcript
                if approved_text is not None and spoken_transcript is not None
                else None
            ),
            normalized_match=(
                normalize_for_match(approved_text)
                == normalize_for_match(spoken_transcript)
                if approved_text is not None and spoken_transcript is not None
                else None
            ),
            native_response_dropped_events=(
                self.metrics.counters["native_response_dropped_events"]
                - dropped_events_before
            ),
            native_response_dropped_bytes=(
                self.metrics.counters["native_response_dropped_bytes"]
                - dropped_bytes_before
            ),
            stage_latency_ms={
                "live_input_final": round(live_input_final_ms, 3),
                "turn_complete": round(turn_complete_ms, 3),
                "live_first_audio": (
                    round(live_first_audio_ms, 3)
                    if live_first_audio_ms is not None
                    else None
                ),
                "end_to_end_first_audio": (
                    round(end_to_end_first_audio_ms, 3)
                    if end_to_end_first_audio_ms is not None
                    else None
                ),
            },
        )
