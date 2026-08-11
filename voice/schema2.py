"""Schema 2 constrained Gemini Live bridge and output enforcement (§§2–7)."""

from __future__ import annotations

import uuid
import contextlib
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

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
        self.registry.register_render(request.call_id, request.turn_id, request.request_id,
                                      request.generation_id)
        self.active = request

    def clear(self) -> None:
        self.active = None

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

        async def sender() -> None:
            async for frame in audio:
                await self._session.send_realtime_input(
                    audio={"data": frame, "mime_type": "audio/pcm;rate=16000"})

        import asyncio
        task = asyncio.create_task(sender())
        pending_transcript = ""
        try:
            async for response in self._session.receive():
                server = getattr(response, "server_content", None)
                transcription = getattr(server, "input_transcription", None)
                if transcription and getattr(transcription, "text", None):
                    pending_transcript = str(transcription.text)
                    if not bool(getattr(server, "turn_complete", False)):
                        yield Transcript(pending_transcript, final=False, provider="gemini_live")
                if bool(getattr(server, "turn_complete", False)) and pending_transcript:
                    yield Transcript(pending_transcript, final=True, provider="gemini_live",
                                     diagnostics={"stable_final": True})
                    pending_transcript = ""
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
        if not transcript.final:
            raise ValueError("only finalized Live input transcription may reach /turn")
        turn_id, generation_id = self.registry.next_turn(call_id)
        correlation = self.registry.require(call_id)
        session_id = None if correlation.session_id.startswith("pending:") else correlation.session_id
        result = await self.turn_client.run(TurnRequest(transcript.text, session_id, turn_id))
        self.registry.update_session(call_id, result.done.session_id)
        self.metrics.outcome(result.done.outcome, result.done.handoff)
        audit = Schema2TurnAudit(result.done.outcome)
        confidence = self._confidence_decision(transcript)
        if not self._server_authorizes_policy(confidence, result.done.outcome, result.done.handoff):
            audit.handoff_requested = True
            await self.call_control.transfer(call_id, "Live transcript confidence policy")
            return audit
        if result.done.handoff:
            audit.handoff_requested = True
            await self.call_control.transfer(call_id, "done.handoff")

        buffer = SentenceBuffer()
        sentences: list[str] = []
        for token in result.tokens:
            sentences.extend(buffer.feed_event({"type": "token", "text": token}))
        sentences.extend(buffer.finish())
        # One renderer is selected for the whole turn to prevent voice changes.
        renderer = Renderer.LIVE
        if any(self.renderer_policy.select(sentence) is Renderer.AZURE for sentence in sentences):
            renderer = Renderer.AZURE
        if renderer is Renderer.LIVE:
            # Live literal rendering remains disabled until qualification. This
            # branch is unreachable with the default fail-closed policy.
            raise RuntimeError("Live literal rendering is not qualified")
        audit.renderer = renderer.value
        for sentence in sentences:
            verdict = self.fidelity.verify_sources(sentence, result.done.sources)
            if not verdict.approved:
                audit.fidelity_failure = verdict.reason
                audit.handoff_requested = True
                await self.call_control.transfer(call_id, "fidelity guard")
                self.output_gate.clear()
                return audit
            render_id = uuid.uuid4().hex
            request = RenderRequest(render_id, call_id, turn_id, generation_id, sentence)
            self.output_gate.activate(request)
            async for chunk in self.azure_tts.synthesize(
                    sentence, turn_id, generation_id, render_id):
                await self.output_gate.forward(chunk)
            audit.rendered_sentences += 1
        return audit

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
        self.output_gate.clear()
        current = self.registry.require(call_id)
        await self.turn_client.cancel()
        await self.azure_tts.cancel(current.turn_id)
        self.registry.invalidate_generation(call_id)
