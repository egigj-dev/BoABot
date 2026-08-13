"""Schema 2 constrained Gemini Live bridge and output enforcement (§§2–7)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .config import VoiceSettings
from .correlation import CorrelationError, CorrelationRegistry
from .events import AudioChunk, RenderRequest, Transcript, TurnRequest
from .fidelity_guard import FidelityGuard
from .metrics import VoiceMetrics
from .sentence_buffer import SentenceBuffer
from .schema1 import CRITICAL_RE, ConfidenceAction, ConfidenceDecision, ConfidencePolicy
from .telephony import CallControl
from .turn_client import TurnService
from .tts.base import TTS

AudioSink = Callable[[AudioChunk], Awaitable[None]]


def _append_transcript(current: str, update: str) -> str:
    """Combine either cumulative or delta Live transcription messages."""
    if not current or update.startswith(current):
        return update
    if current.endswith(update):
        return current
    separator = "" if current[-1:].isspace() or update[:1].isspace() else " "
    return f"{current}{separator}{update}"


class NativeResponseSink:
    """Load-bearing sink: Live-native answers are counted and always discarded."""

    def __init__(self, metrics: VoiceMetrics) -> None:
        self.metrics = metrics

    def drop_audio(self, data: bytes) -> None:
        self.metrics.increment("native_response_dropped_bytes", len(data))
        self.metrics.increment("native_response_dropped_events")

    def drop_text(self, text: str) -> None:
        self.metrics.increment("native_response_dropped_bytes", len(text.encode("utf-8")))
        self.metrics.increment("native_response_dropped_events")


class OutputAudioGate:
    """Forward only audio bearing the active render request and generation IDs."""

    def __init__(self, registry: CorrelationRegistry, sink: AudioSink,
                 metrics: VoiceMetrics) -> None:
        self.registry = registry
        self.sink = sink
        self.metrics = metrics
        self.active: RenderRequest | None = None

    def activate(self, request: RenderRequest) -> None:
        if self.active is not None:
            raise CorrelationError("an output render is already active")
        self.registry.register_render(request.call_id, request.turn_id, request.request_id,
                                      request.generation_id)
        self.active = request

    def clear(self) -> None:
        active = self.active
        self.active = None
        if active is not None:
            self.registry.finish_render(active.call_id, active.request_id)

    async def forward(self, chunk: AudioChunk) -> bool:
        active = self.active
        if active is None or chunk.render_request_id != active.request_id:
            self.metrics.increment("output_gate_dropped_bytes", len(chunk.data))
            return False
        try:
            self.registry.validate(active.call_id, chunk.turn_id, chunk.generation_id,
                                   chunk.render_request_id)
        except CorrelationError:
            self.metrics.increment("output_gate_dropped_bytes", len(chunk.data))
            return False
        await self.sink(chunk)
        return True


class Renderer(str, Enum):
    LIVE = "live"
    AZURE = "azure"


@dataclass(slots=True)
class RendererPolicy:
    """Schema 2 §5: risky or unqualified rendering always uses Azure TTS."""

    fidelity: FidelityGuard
    live_literal_qualified: bool = False

    def select(self, sentence: str) -> Renderer:
        risky = bool(self.fidelity.extract_claims(sentence) or self.fidelity.extract_entities(sentence))
        return Renderer.AZURE if risky or not self.live_literal_qualified else Renderer.LIVE


class GeminiLiveSessionManager:
    """Preview Live transport manager; BoABot session history never enters Live."""

    def __init__(self, settings: VoiceSettings, native_sink: NativeResponseSink,
                 use_custom_vad: bool = False) -> None:
        self.settings = settings
        self.native_sink = native_sink
        self.use_custom_vad = use_custom_vad
        self._client: Any = None
        self._session: Any = None
        self._session_context: Any = None
        self.resumption_handle: str | None = None

    async def connect(self) -> None:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        key = self.settings.require_gemini_live()["GEMINI_API_KEY"]
        self._client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=self.use_custom_vad)),
            session_resumption=types.SessionResumptionConfig(handle=self.resumption_handle),
        )
        self._session_context = self._client.aio.live.connect(
            model=self.settings.gemini_live_model, config=config)
        self._session = await self._session_context.__aenter__()

    async def events(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        """Send audio and yield input transcriptions; drop every native answer."""
        if self._session is None:
            await self.connect()

        audio_started_s: float | None = None

        async def sender() -> None:
            nonlocal audio_started_s
            activity_started = False
            async for frame in audio:
                if not frame:
                    continue
                if audio_started_s is None:
                    audio_started_s = time.monotonic()
                if self.use_custom_vad and not activity_started:
                    await self._session.send_realtime_input(activity_start={})
                    activity_started = True
                await self._session.send_realtime_input(
                    audio={"data": frame, "mime_type": "audio/pcm;rate=16000"})
            if activity_started:
                await self._session.send_realtime_input(activity_end={})
            else:
                await self._session.send_realtime_input(audio_stream_end=True)

        task = asyncio.create_task(sender())
        pending_transcript = ""
        try:
            async for response in self._session.receive():
                server = getattr(response, "server_content", None)
                if bool(getattr(server, "interrupted", False)):
                    self.native_sink.metrics.increment("live_interruptions")
                data = getattr(response, "data", None)
                text = getattr(response, "text", None)
                if data:
                    self.native_sink.drop_audio(bytes(data))
                if text:
                    self.native_sink.drop_text(str(text))
                update = getattr(response, "session_resumption_update", None)
                if update and getattr(update, "new_handle", None):
                    self.resumption_handle = str(update.new_handle)
                transcription = (
                    getattr(server, "input_transcription", None)
                    or getattr(server, "interim_input_transcription", None)
                )
                if transcription and getattr(transcription, "text", None):
                    pending_transcript = _append_transcript(
                        pending_transcript, str(transcription.text),
                    )
                    if not bool(getattr(server, "turn_complete", False)):
                        yield Transcript(
                            pending_transcript, final=False, provider="gemini_live",
                            started_s=audio_started_s,
                        )
                if bool(getattr(server, "turn_complete", False)) and pending_transcript:
                    yield Transcript(
                        pending_transcript.strip(), final=True, provider="gemini_live",
                        started_s=audio_started_s, finalized_s=time.monotonic(),
                        diagnostics={"stable_final": True},
                    )
                    pending_transcript = ""
                    if task.done():
                        break
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def interrupt(self) -> None:
        if self._session is not None:
            await self._session.send_realtime_input(activity_end={})

    async def close(self) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(None, None, None)
        self._session = None
        self._session_context = None


@dataclass(slots=True)
class Schema2TurnAudit:
    server_outcome: str
    rendered_sentences: int = 0
    renderer: str = "none"
    handoff_requested: bool = False
    fidelity_failure: str | None = None
    asr_finalize_to_first_approved_ms: float | None = None
    first_approved_to_tts_first_byte_ms: float | None = None
    asr_finalize_to_tts_first_byte_ms: float | None = None


class LiveTranscriber(Protocol):
    """Input-only Live seam used by the real manager and offline PoC."""

    def events(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]: ...

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


class ConstrainedLiveBridge:
    """Submit every finalized Live transcript to `/turn`; Live never authors output."""

    def __init__(self, turn_client: TurnService, azure_tts: TTS,
                 call_control: CallControl, output_gate: OutputAudioGate,
                 registry: CorrelationRegistry, fidelity: FidelityGuard,
                 metrics: VoiceMetrics,
                 confidence_policy: ConfidencePolicy | None = None) -> None:
        self.turn_client = turn_client
        self.azure_tts = azure_tts
        self.call_control = call_control
        self.output_gate = output_gate
        self.registry = registry
        self.fidelity = fidelity
        self.metrics = metrics
        self.renderer_policy = RendererPolicy(fidelity)
        self.confidence_policy = confidence_policy or ConfidencePolicy()

    async def handle_final(self, call_id: str, transcript: Transcript) -> Schema2TurnAudit:
        if not transcript.final or not transcript.text.strip():
            raise ValueError("only non-empty finalized Live input transcription may reach /turn")
        finalized_s = transcript.finalized_s or time.monotonic()
        turn_id, generation_id = self.registry.next_turn(call_id)
        correlation = self.registry.require(call_id)
        session_id = None if correlation.session_id.startswith("pending:") else correlation.session_id
        confidence = self._confidence_decision(transcript)
        stream_during_turn = confidence.action is ConfidenceAction.PROCEED
        audit = Schema2TurnAudit("pending")
        first_approved_s: float | None = None
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()

        def mark_first_approved() -> None:
            nonlocal first_approved_s
            if first_approved_s is not None:
                return
            first_approved_s = time.monotonic()
            elapsed = (first_approved_s - finalized_s) * 1000
            audit.asr_finalize_to_first_approved_ms = max(0.0, elapsed)
            self.metrics.observe(
                "gemini_live.asr_finalize_to_first_approved", elapsed,
            )

        async def on_event(event: dict[str, object]) -> None:
            if event.get("type") != "approved_sentence":
                return
            text = event.get("text")
            if not isinstance(text, str) or not text.strip():
                return
            mark_first_approved()
            if stream_during_turn:
                await sentence_queue.put(text.strip())

        async def render_stream() -> None:
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    return
                await self._render_sentence(
                    call_id, turn_id, generation_id, sentence, audit,
                    finalized_s, first_approved_s,
                )

        renderer_task = (
            asyncio.create_task(render_stream()) if stream_during_turn else None
        )
        request = TurnRequest(
            transcript.text.strip(), session_id, turn_id, correlation_key=call_id,
        )
        try:
            result = await self.turn_client.run(request, on_event)
        except BaseException:
            if renderer_task is not None:
                for render_id in self.registry.active_render_ids(call_id):
                    await self.azure_tts.cancel(render_id)
                renderer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renderer_task
            raise
        self.registry.update_session(call_id, result.done.session_id)
        self.metrics.outcome(result.done.outcome, result.done.handoff)
        audit.server_outcome = result.done.outcome

        if renderer_task is not None:
            await sentence_queue.put(None)
            try:
                await renderer_task
            except Exception as exc:
                audit.fidelity_failure = f"Azure TTS stream failed: {exc}"
                audit.handoff_requested = True
                await self.call_control.transfer(call_id, "Azure TTS stream failure")
                self.output_gate.clear()
                return audit

        if not self._server_authorizes_policy(confidence, result.done.outcome, result.done.handoff):
            audit.handoff_requested = True
            await self.call_control.transfer(call_id, "Live transcript confidence policy")
            return audit
        if result.done.handoff:
            audit.handoff_requested = True
            await self.call_control.transfer(call_id, "done.handoff")

        if renderer_task is None:
            sentences = list(result.approved_sentences)
            if not sentences:
                # Compatibility with an older /turn: token text is released only
                # after the post-/turn fidelity guard approves complete sentences.
                buffer = SentenceBuffer()
                for token in result.tokens:
                    sentences.extend(buffer.feed_event({"type": "token", "text": token}))
                sentences.extend(buffer.finish())
                for sentence in sentences:
                    verdict = self.fidelity.verify_sources(sentence, result.done.sources)
                    if not verdict.approved:
                        audit.fidelity_failure = verdict.reason
                        audit.handoff_requested = True
                        await self.call_control.transfer(call_id, "fidelity guard")
                        self.output_gate.clear()
                        return audit
            for sentence in sentences:
                mark_first_approved()
                await self._render_sentence(
                    call_id, turn_id, generation_id, sentence, audit,
                    finalized_s, first_approved_s,
                )
        return audit

    async def _render_sentence(
            self, call_id: str, turn_id: Any, generation_id: Any,
            sentence: str, audit: Schema2TurnAudit, finalized_s: float,
            first_approved_s: float | None) -> None:
        if self.renderer_policy.select(sentence) is not Renderer.AZURE:
            raise RuntimeError("native Live rendering is disabled")
        audit.renderer = Renderer.AZURE.value
        render_id = uuid.uuid4().hex
        request = RenderRequest(render_id, call_id, turn_id, generation_id, sentence)
        self.output_gate.activate(request)
        saw_first_byte = audit.asr_finalize_to_tts_first_byte_ms is not None
        emitted_audio = False
        try:
            async for chunk in self.azure_tts.synthesize(
                    sentence, turn_id, generation_id, render_id):
                emitted_audio = emitted_audio or bool(chunk.data)
                if chunk.data and not saw_first_byte:
                    saw_first_byte = True
                    first_audio_s = time.monotonic()
                    from_final = (first_audio_s - finalized_s) * 1000
                    audit.asr_finalize_to_tts_first_byte_ms = max(0.0, from_final)
                    self.metrics.observe(
                        "gemini_live.asr_finalize_to_tts_first_byte", from_final,
                    )
                    if first_approved_s is not None:
                        from_approved = (first_audio_s - first_approved_s) * 1000
                        audit.first_approved_to_tts_first_byte_ms = max(0.0, from_approved)
                        self.metrics.observe(
                            "gemini_live.first_approved_to_tts_first_byte",
                            from_approved,
                        )
                await self.output_gate.forward(chunk)
            if not emitted_audio:
                raise RuntimeError("Azure TTS returned no audio")
            audit.rendered_sentences += 1
        finally:
            self.output_gate.clear()

    def _confidence_decision(self, transcript: Transcript) -> ConfidenceDecision:
        if transcript.confidence is not None:
            return self.confidence_policy.effective(transcript)
        if CRITICAL_RE.search(transcript.text):
            return ConfidenceDecision(ConfidenceAction.HANDOFF,
                                      "Live critical confidence unavailable")
        if transcript.diagnostics.get("stable_final"):
            return ConfidenceDecision(ConfidenceAction.PROCEED,
                                      "stable finalized Live hypothesis")
        return ConfidenceDecision(ConfidenceAction.CLARIFY,
                                  "Live confidence and stability unavailable")

    @staticmethod
    def _server_authorizes_policy(decision: ConfidenceDecision, outcome: str,
                                  handoff: bool) -> bool:
        if decision.action is ConfidenceAction.PROCEED:
            return True
        if decision.action is ConfidenceAction.CLARIFY:
            return outcome in {"clarify", "handoff"}
        return outcome == "handoff" and handoff

    async def interrupt(self, call_id: str) -> None:
        render_ids = self.registry.active_render_ids(call_id)
        self.output_gate.clear()
        self.registry.require(call_id)
        await self.turn_client.cancel(call_id)
        for render_request_id in render_ids:
            await self.azure_tts.cancel(render_request_id)
        self.registry.invalidate_generation(call_id)


class GeminiLiveTranscriptionPipeline:
    """Gemini Live input transcription -> authoritative `/turn` -> Azure TTS."""

    def __init__(self, transcriber: LiveTranscriber,
                 bridge: ConstrainedLiveBridge) -> None:
        self.transcriber = transcriber
        self.bridge = bridge

    async def open_call(self, call_id: str, session_id: str | None = None,
                        live_session_id: str | None = None) -> None:
        self.bridge.registry.open_call(
            call_id, session_id or f"pending:{call_id}", live_session_id,
        )
        await self.bridge.call_control.answer(call_id)

    async def run_audio(self, call_id: str,
                        audio: AsyncIterable[bytes]) -> list[Schema2TurnAudit]:
        audits: list[Schema2TurnAudit] = []
        async for transcript in self.transcriber.events(audio):
            if transcript.final:
                audits.append(await self.bridge.handle_final(call_id, transcript))
            else:
                self.bridge.metrics.increment("gemini_live.partial_transcripts")
        return audits

    async def interrupt(self, call_id: str) -> None:
        await self.transcriber.interrupt()
        await self.bridge.interrupt(call_id)

    async def close(self) -> None:
        await self.transcriber.close()
