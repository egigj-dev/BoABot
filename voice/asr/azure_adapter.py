"""Azure continuous ``sq-AL`` adapter for Schema 1 §§3/5.

The Azure SDK is imported only when recognition starts. Phrase adaptation is
populated from versioned corpus labels, and final events retain provider timing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ..config import VoiceSettings
from ..events import Transcript
from ..phrases import build_phrase_list
from .base import StreamingASR


class AzureStreamingASR(StreamingASR):
    def __init__(self, settings: VoiceSettings, phrases: tuple[str, ...] | None = None) -> None:
        self.settings = settings
        self.phrases = phrases or build_phrase_list()
        self._recognizer: Any = None
        self._push_stream: Any = None
        self._cancelled = False

    async def _run(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]

        credentials = self.settings.require_azure_asr()
        config = speechsdk.SpeechConfig(credentials["AZURE_SPEECH_KEY"], credentials["AZURE_SPEECH_REGION"])
        config.speech_recognition_language = "sq-AL"
        config.output_format = speechsdk.OutputFormat.Detailed
        fmt = speechsdk.audio.AudioStreamFormat(samples_per_second=16_000, bits_per_sample=16, channels=1)
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
        self._recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio_config)
        grammar = speechsdk.PhraseListGrammar.from_recognizer(self._recognizer)
        for phrase in self.phrases:
            grammar.addPhrase(phrase)
        queue: asyncio.Queue[Transcript | BaseException | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        started = time.monotonic()

        def partial(event: Any) -> None:
            text = str(event.result.text or "")
            if text:
                loop.call_soon_threadsafe(queue.put_nowait, Transcript(
                    text=text, final=False, provider="azure", started_s=started))

        def final(event: Any) -> None:
            text = str(event.result.text or "")
            if not text:
                return
            confidence = None
            alternatives: tuple[str, ...] = ()
            try:
                raw = event.result.properties.get_property(
                    speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
                detail = json.loads(raw) if raw else {}
                nbest = detail.get("NBest") or []
                if nbest:
                    confidence = float(nbest[0].get("Confidence"))
                    alternatives = tuple(str(item.get("Display") or item.get("Lexical") or "")
                                         for item in nbest[1:] if item)
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                pass
            loop.call_soon_threadsafe(queue.put_nowait, Transcript(
                text=text, final=True, confidence=confidence, alternatives=alternatives,
                provider="azure", started_s=started, finalized_s=time.monotonic(),
                diagnostics={"offset": getattr(event.result, "offset", None),
                             "duration": getattr(event.result, "duration", None)}))

        def stopped(_event: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, None)

        self._recognizer.recognizing.connect(partial)
        self._recognizer.recognized.connect(final)
        self._recognizer.canceled.connect(stopped)
        self._recognizer.session_stopped.connect(stopped)
        await asyncio.to_thread(self._recognizer.start_continuous_recognition)

        async def feed() -> None:
            try:
                async for frame in audio:
                    if self._cancelled:
                        break
                    self._push_stream.write(frame)
            finally:
                self._push_stream.close()

        feeder = asyncio.create_task(feed())
        try:
            while not self._cancelled:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await feeder
            await self.stop()

    def start(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        self._cancelled = False
        return self._run(audio)

    async def stop(self) -> None:
        if self._recognizer is not None:
            await asyncio.to_thread(self._recognizer.stop_continuous_recognition)

    async def cancel(self) -> None:
        self._cancelled = True
        if self._push_stream is not None:
            self._push_stream.close()
        await self.stop()
