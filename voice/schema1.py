"""Schema 1 guarded modular near-real-time orchestrator (§§2–7)."""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from .asr.base import StreamingASR
from .barge_in import BargeInCoordinator
from .config import VoiceSettings
from .correlation import CorrelationError, CorrelationRegistry
from .events import AudioChunk, Transcript, TurnRequest
from .fidelity_guard import FidelityGuard
from .metrics import VoiceMetrics
from .sentence_buffer import SentenceBuffer
from .telephony import CallControl
from .turn_client import TurnResult, TurnService
from .tts.base import TTS
from .vad import EnergyVAD, VADEvent

CRITICAL_RE = re.compile(
    r"\b(?:Banka|Bankën|Bankës|ALL|EUR|USD|PIN|CVV|CVC|OTP|\d+(?:[,.]\d+)?%?)\b",
    re.IGNORECASE,
)
SAFETY_TERMS = {"pin", "cvv", "cvc", "otp"}
AudioSink = Callable[[AudioChunk], Awaitable[None]]


class ConfidenceAction(str, Enum):
    PROCEED = "proceed"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class ConfidenceDecision:
    action: ConfidenceAction
    reason: str


@dataclass(slots=True)
class ConfidencePolicy:
    """Schema 1 §5 initial thresholds; calibrate separately for each provider."""

    proceed: float = 0.75
    critical: float = 0.85
    handoff: float = 0.55
    failed_clarifications: int = 0

    def evaluate(self, transcript: Transcript) -> ConfidenceDecision:
        folded = transcript.text.casefold()
        alternative_terms = {term for alternative in transcript.alternatives
                             for term in SAFETY_TERMS if term in alternative.casefold()}
        primary_terms = {term for term in SAFETY_TERMS if term in folded}
        if alternative_terms != primary_terms and (alternative_terms or primary_terms):
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "safety-keyword ambiguity")
        if transcript.confidence is None:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "provider confidence unavailable")
        if transcript.confidence < self.handoff:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "overall confidence below handoff threshold")
        critical_spans = CRITICAL_RE.findall(transcript.text)
        if critical_spans:
            if not transcript.critical_confidences:
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "critical-span confidence unavailable")
            if any(transcript.critical_confidences.get(span, 0.0) < self.critical for span in critical_spans):
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "critical span below threshold")
            normalized_alternatives = {alternative.casefold().strip() for alternative in transcript.alternatives}
            if normalized_alternatives and any(span.casefold() not in " ".join(normalized_alternatives)
                                               for span in critical_spans):
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "conflicting critical alternative")
        if transcript.confidence < self.proceed:
            return ConfidenceDecision(ConfidenceAction.CLARIFY, "overall confidence below proceed threshold")
        return ConfidenceDecision(ConfidenceAction.PROCEED, "accepted")

    def record(self, decision: ConfidenceDecision, server_outcome: str) -> None:
        if decision.action is ConfidenceAction.CLARIFY and server_outcome == "clarify":
            self.failed_clarifications += 1
        elif decision.action is ConfidenceAction.PROCEED:
            self.failed_clarifications = 0

    def effective(self, transcript: Transcript) -> ConfidenceDecision:
        decision = self.evaluate(transcript)
        if decision.action is ConfidenceAction.CLARIFY and self.failed_clarifications >= 2:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "two consecutive failed clarifications")
        return decision


class SpeculativeWarmer:
    """Read-only placeholder for Schema 1 §2 partial warming.

    TODO: an isolated deployment may warm connections/retrieval pages. This stub
    intentionally issues no request, writes no session state, returns no hits,
    and can never feed generation.
    """

    async def warm(self, _partial_text: str) -> None:
        return None


@dataclass(slots=True)
class Schema1TurnAudit:
    call_id: str
    turn_id: int
    confidence_action: str
    server_outcome: str
    authorized_text: list[str] = field(default_factory=list)
    rendered_bytes: int = 0
    fidelity_failure: str | None = None
    handoff_requested: bool = False


class Schema1Orchestrator:
    """Every final transcript reaches `/turn`; only its verified tokens reach TTS."""

    def __init__(self, asr: StreamingASR, turn_client: TurnService, tts: TTS,
                 call_control: CallControl, audio_sink: AudioSink,
                 settings: VoiceSettings | None = None,
                 registry: CorrelationRegistry | None = None,
                 fidelity_guard: FidelityGuard | None = None,
                 metrics: VoiceMetrics | None = None,
                 warmer: SpeculativeWarmer | None = None,
                 vad: EnergyVAD | None = None,
                 barge_in: BargeInCoordinator | None = None) -> None:
        self.settings = settings or VoiceSettings.from_env()
        self.asr = asr
        self.turn_client = turn_client
        self.tts = tts
        self.call_control = call_control
        self.audio_sink = audio_sink
        self.registry = registry or CorrelationRegistry()
        self.fidelity_guard = fidelity_guard or FidelityGuard()
        self.metrics = metrics or VoiceMetrics()
        self.warmer = warmer or SpeculativeWarmer()
        self.vad = vad or EnergyVAD()
        self.barge_in = barge_in
        self._playing = False
        self.confidence = ConfidencePolicy(
            self.settings.confidence_proceed, self.settings.confidence_critical,
            self.settings.confidence_handoff)

    async def open_call(self, call_id: str, session_id: str | None = None) -> None:
        self.registry.open_call(call_id, session_id or f"pending:{call_id}")
        await self.call_control.answer(call_id)

    async def run_audio(self, call_id: str, audio: AsyncIterable[bytes]) -> list[Schema1TurnAudit]:
        audits: list[Schema1TurnAudit] = []

        async def monitored_audio():
            async for frame in audio:
                for event in self.vad.process(frame):
                    if (event is VADEvent.SPEECH_START and self._playing
                            and self.barge_in is not None):
                        await self.barge_in.speech_started(call_id)
                yield frame

        async for transcript in self.asr.start(monitored_audio()):
            if transcript.final:
                audits.append(await self.handle_final(call_id, transcript))
            else:
                await self.warmer.warm(transcript.text)
        return audits

    async def handle_final(self, call_id: str, transcript: Transcript) -> Schema1TurnAudit:
        if not transcript.final or not transcript.text.strip():
            raise ValueError("a non-empty final transcript is required")
        turn_id, generation_id = self.registry.next_turn(call_id)
        current = self.registry.require(call_id)
        decision = self.confidence.effective(transcript)
        request = TurnRequest(
            transcript.text.strip(),
            None if current.session_id.startswith("pending:") else current.session_id,
            turn_id,
            correlation_key=call_id,
        )
        audit = Schema1TurnAudit(call_id, int(turn_id), decision.action.value, "pending")
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_during_turn = decision.action is ConfidenceAction.PROCEED

        async def on_event(event: dict[str, object]) -> None:
            if event.get("type") == "approved_sentence" and stream_during_turn:
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    await sentence_queue.put(text.strip())

        async def render_stream() -> None:
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    return
                await self._render_sentence(
                    call_id, turn_id, generation_id, sentence, audit,
                )

        renderer_task = (
            asyncio.create_task(render_stream()) if stream_during_turn else None
        )
        try:
            result = await self.turn_client.run(request, on_event)
        except BaseException:
            if renderer_task is not None:
                for request_id in self.registry.active_render_ids(call_id):
                    await self.tts.cancel(request_id)
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
                audit.fidelity_failure = f"TTS stream failed: {exc}"
                await self._handoff(call_id, audit, "TTS stream failure")
                return audit

        # The current TurnReq cannot carry confidence diagnostics. Every final
        # still reaches /turn, but low-confidence audio is spoken only when its
        # structured outcome is at least as restrictive as the local policy.
        allowed = self._server_authorizes_policy(decision, result)
        self.confidence.record(decision, result.done.outcome)
        if not allowed:
            await self._handoff(call_id, audit, "confidence policy not enforced by /turn outcome")
            return audit
        if result.done.handoff:
            await self._handoff(call_id, audit, "done.handoff")

        if renderer_task is None:
            released = list(result.approved_sentences)
            if not released:
                # Backward-compatible fail-closed path for an older /turn server.
                sentences = SentenceBuffer()
                for token in result.tokens:
                    released.extend(sentences.feed_event({"type": "token", "text": token}))
                released.extend(sentences.finish())
                for sentence in released:
                    verdict = self.fidelity_guard.verify_sources(sentence, result.done.sources)
                    if not verdict.approved:
                        audit.fidelity_failure = verdict.reason
                        await self.turn_client.cancel(call_id)
                        await self._handoff(call_id, audit, "fidelity guard")
                        return audit
            for sentence in released:
                await self._render_sentence(
                    call_id, turn_id, generation_id, sentence, audit,
                )
        return audit

    async def _render_sentence(self, call_id, turn_id, generation_id,
                               sentence: str, audit: Schema1TurnAudit) -> None:
        request_id = uuid.uuid4().hex
        self.registry.register_render(
            call_id, turn_id, request_id, generation_id,
        )
        audit.authorized_text.append(sentence)
        self._playing = True
        emitted_audio = False
        try:
            async for chunk in self.tts.synthesize(
                    sentence, turn_id, generation_id, request_id):
                try:
                    self.registry.validate(
                        call_id, chunk.turn_id, chunk.generation_id,
                        chunk.render_request_id,
                    )
                except CorrelationError:
                    self.metrics.increment("stale_audio_frames")
                    continue
                await self.audio_sink(chunk)
                emitted_audio = emitted_audio or bool(chunk.data)
                audit.rendered_bytes += len(chunk.data)
            if not emitted_audio:
                raise RuntimeError("TTS returned no audio")
        finally:
            self._playing = False
            self.registry.finish_render(call_id, request_id)

    @staticmethod
    def _server_authorizes_policy(decision: ConfidenceDecision, result: TurnResult) -> bool:
        if decision.action is ConfidenceAction.PROCEED:
            return True
        if decision.action is ConfidenceAction.CLARIFY:
            return result.done.outcome in {"clarify", "handoff"}
        return result.done.outcome == "handoff" and result.done.handoff

    async def _handoff(self, call_id: str, audit: Schema1TurnAudit, reason: str) -> None:
        audit.handoff_requested = True
        accepted = await self.call_control.transfer(call_id, reason)
        self.metrics.increment("handoff_accepted" if accepted else "handoff_failed")
