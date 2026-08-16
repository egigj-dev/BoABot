"""Preconnected Azure TTS pool and turn cancellation for Schema 1 §§3/4."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from ..config import VoiceSettings
from ..events import AudioChunk, GenerationId, TurnId
from .base import TTS
from .ssml import canonicalize


class AzureTTS(TTS):
    """Reuse one synthesizer and release provider audio as it is synthesized."""

    def __init__(self, settings: VoiceSettings, chunk_size: int = 4096) -> None:
        self.settings = settings
        self.chunk_size = chunk_size
        self._synthesizer: Any = None
        self._cancelled: set[str] = set()
        self._active_render_id: str | None = None
        self._lock = asyncio.Lock()

    def _connect(self) -> Any:
        if self._synthesizer is None:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]

            credentials = self.settings.require_azure_tts()
            config = speechsdk.SpeechConfig(credentials["AZURE_TTS_KEY"], credentials["AZURE_TTS_REGION"])
            config.speech_synthesis_voice_name = self.settings.azure_tts_voice
            # AudioChunk is the bridge raw PCM transport. RIFF output adds a
            # WAV header to each SDK synthesis event, so joining streamed
            # events makes players stop after the first fragment.
            config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
            )
            self._synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        return self._synthesizer

    async def _synthesize(self, approved_text: str, turn_id: TurnId,
                          generation_id: GenerationId,
                          render_request_id: str) -> AsyncIterator[AudioChunk]:
        if render_request_id in self._cancelled:
            self._cancelled.discard(render_request_id)
            return
        started = time.monotonic()
        first_chunk = True
        async with self._lock:
            if render_request_id in self._cancelled:
                self._cancelled.discard(render_request_id)
                return
            synthesizer = self._connect()
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
            self._active_render_id = render_request_id

            def synthesized(event: Any) -> None:
                payload = bytes(event.result.audio_data or b"")
                for offset in range(0, len(payload), self.chunk_size):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, payload[offset:offset + self.chunk_size],
                    )

            def completed(_event: Any) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, None)

            def cancelled(event: Any) -> None:
                if render_request_id in self._cancelled:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                    return
                details = getattr(event.result, "cancellation_details", None)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    RuntimeError(f"Azure TTS synthesis cancelled: {details}"),
                )

            synthesizer.synthesizing.connect(synthesized)
            synthesizer.synthesis_completed.connect(completed)
            synthesizer.synthesis_canceled.connect(cancelled)
            future = synthesizer.speak_ssml_async(
                canonicalize(approved_text, self.settings.azure_tts_voice)
            )
            try:
                while True:
                    if render_request_id in self._cancelled:
                        return
                    item = await queue.get()
                    if item is None:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    yield AudioChunk(
                        item, turn_id, generation_id, render_request_id,
                        (time.monotonic() - started) * 1000 if first_chunk else None,
                        False,
                    )
                    first_chunk = False
                await asyncio.to_thread(future.get)
            finally:
                synthesizer.synthesizing.disconnect_all()
                synthesizer.synthesis_completed.disconnect_all()
                synthesizer.synthesis_canceled.disconnect_all()
                self._active_render_id = None
                self._cancelled.discard(render_request_id)

    def synthesize(self, approved_text: str, turn_id: TurnId, generation_id: GenerationId,
                   render_request_id: str) -> AsyncIterator[AudioChunk]:
        return self._synthesize(approved_text, turn_id, generation_id, render_request_id)

    async def cancel(self, render_request_id: str) -> None:
        self._cancelled.add(render_request_id)
        if self._synthesizer is not None and self._active_render_id == render_request_id:
            await asyncio.to_thread(self._synthesizer.stop_speaking_async().get)
