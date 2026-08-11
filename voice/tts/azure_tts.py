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
    """Reuse one synthesizer connection; SDK loading occurs only on first use."""

    def __init__(self, settings: VoiceSettings, chunk_size: int = 4096) -> None:
        self.settings = settings
        self.chunk_size = chunk_size
        self._synthesizer: Any = None
        self._cancelled: set[TurnId] = set()
        self._lock = asyncio.Lock()

    def _connect(self) -> Any:
        if self._synthesizer is None:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]

            credentials = self.settings.require_azure_tts()
            config = speechsdk.SpeechConfig(credentials["AZURE_TTS_KEY"], credentials["AZURE_TTS_REGION"])
            config.speech_synthesis_voice_name = self.settings.azure_tts_voice
            self._synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        return self._synthesizer

    async def _synthesize(self, approved_text: str, turn_id: TurnId,
                          generation_id: GenerationId,
                          render_request_id: str) -> AsyncIterator[AudioChunk]:
        started = time.monotonic()
        async with self._lock:
            synthesizer = self._connect()
            result = await asyncio.to_thread(
                lambda: synthesizer.speak_ssml_async(
                    canonicalize(approved_text, self.settings.azure_tts_voice)).get())
        payload = bytes(result.audio_data)
        for offset in range(0, len(payload), self.chunk_size):
            if turn_id in self._cancelled:
                return
            yield AudioChunk(
                payload[offset:offset + self.chunk_size], turn_id, generation_id,
                render_request_id,
                (time.monotonic() - started) * 1000 if offset == 0 else None,
                offset + self.chunk_size >= len(payload),
            )

    def synthesize(self, approved_text: str, turn_id: TurnId, generation_id: GenerationId,
                   render_request_id: str) -> AsyncIterator[AudioChunk]:
        return self._synthesize(approved_text, turn_id, generation_id, render_request_id)

    async def cancel(self, turn_id: TurnId) -> None:
        self._cancelled.add(turn_id)
        if self._synthesizer is not None:
            await asyncio.to_thread(self._synthesizer.stop_speaking_async().get)
