"""Schema 1 guarded modular near-real-time orchestrator (§§2–7)."""

from __future__ import annotations

import asyncio
import contextlib

import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field

from .asr.base import StreamingASR
from ..shared.confidence import CRITICAL_RE, ConfidenceAction, ConfidenceDecision, ConfidencePolicy
from ..shared.barge_in import BargeInCoordinator
from ..shared.config import VoiceSettings
from ..shared.correlation import CorrelationError, CorrelationRegistry
from ..shared.events import AudioChunk, Transcript, TurnRequest
from ..shared.fidelity_guard import FidelityGuard
from ..shared.metrics import VoiceMetrics
from ..shared.sentence_buffer import SentenceBuffer
from ..shared.telephony import CallControl
from ..shared.turn_client import TurnResult, TurnService
from ..shared.tts.base import TTS
from ..shared.vad import EnergyVAD, VADEvent
AudioSink = Callable[[AudioChunk], Awaitable[None]]



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
    """Only policy-approved final transcripts reach `/turn` and then TTS."""

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
            self.settings.confidence_handoff,
            enabled=not self.settings.confidence_gate_disabled,
        )

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
        return audits

    async def handle_final(self, call_id: str, transcript: Transcript) -> Schema1TurnAudit:
        if not transcript.final or not transcript.text.strip():
            raise ValueError("a non-empty final transcript is required")
        turn_id, generation_id = self.registry.next_turn(call_id)
        current = self.registry.require(call_id)
        decision = self.confidence.effective(transcript)
        audit = Schema1TurnAudit(call_id, int(turn_id), decision.action.value, "pending")
        if decision.action is ConfidenceAction.HANDOFF:
            audit.server_outcome = "local_handoff"
            self.metrics.outcome("local_handoff", True)
            await self._handoff(call_id, audit, decision.reason)
            return audit
        if decision.action is ConfidenceAction.CLARIFY:
            audit.server_outcome = "clarify"
            self.confidence.record(decision, "clarify")
            self.metrics.outcome("clarify")
            await self._render_sentences(
                call_id, turn_id, generation_id,
                ("Ju lutem përsëriteni pyetjen më qartë.",), audit,
            )
            return audit
        request = TurnRequest(
            transcript.text.strip(),
            None if current.session_id.startswith("pending:") else current.session_id,
            turn_id,
            include_vetted_text=True,
            correlation_key=call_id,
        )
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_during_turn = decision.action is ConfidenceAction.PROCEED

        async def on_event(event: dict[str, object]) -> None:
            if event.get("type") == "approved_sentence" and stream_during_turn:
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    await sentence_queue.put(text.strip())

        async def render_stream() -> None:
            self._playing = True
            try:
                while True:
                    sentence = await sentence_queue.get()
                    if sentence is None:
                        return
                    await self._render_sentence(
                        call_id, turn_id, generation_id, sentence, audit,
                    )
            finally:
                self._playing = False

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
            self.metrics.increment("policy_override")
            await self._handoff(call_id, audit, "confidence policy not enforced by /turn outcome")
            return audit
        self.metrics.outcome(result.done.outcome, result.done.handoff)
        if result.done.handoff:
            await self._handoff(call_id, audit, "done.handoff")

        if result.approved_sentences and any(
            source.get("passage_text") for source in result.done.sources
        ):
            disagreements = sum(
                not self.fidelity_guard.verify_sources(
                    sentence, result.done.sources,
                ).approved
                for sentence in result.approved_sentences
            )
            self.metrics.increment("fidelity_disagreement", disagreements)

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
            await self._render_sentences(
                call_id, turn_id, generation_id, released, audit,
            )
        return audit

    async def _render_sentences(self, call_id, turn_id, generation_id,
                                sentences, audit: Schema1TurnAudit) -> None:
        self._playing = True
        try:
            for sentence in sentences:
                await self._render_sentence(
                    call_id, turn_id, generation_id, sentence, audit,
                )
        finally:
            self._playing = False

    async def _render_sentence(self, call_id, turn_id, generation_id,
                               sentence: str, audit: Schema1TurnAudit) -> None:
        request_id = uuid.uuid4().hex
        self.registry.register_render(
            call_id, turn_id, request_id, generation_id,
        )
        audit.authorized_text.append(sentence)
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
