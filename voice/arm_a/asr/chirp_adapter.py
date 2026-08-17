"""Pinned Speech-to-Text V2 Chirp 3 adapter for Schema 1 §§3/5.

Albanian ``sq-AL`` Chirp 3 support is Preview and must be separately qualified.
The Google SDK is imported lazily when a stream starts.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ...shared.config import VoiceSettings
from ...shared.events import Transcript
from .base import StreamingASR


class ChirpStreamingASR(StreamingASR):
    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._cancelled = False
        self._stream: Any = None

    async def _run(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        from google.cloud import speech_v2  # type: ignore[import-not-found]
        from google.cloud.speech_v2.types import cloud_speech  # type: ignore[import-not-found]

        # gRPC-aio request streams must be fed by an async iterable; a sync
        # source cancels the RPC with a bare asyncio.CancelledError.
        async def to_async() -> AsyncIterator[bytes]:
            if hasattr(audio, "__aiter__"):
                async for frame in audio:  # type: ignore[union-attr]
                    yield frame
            else:
                for frame in audio:  # type: ignore[union-attr]
                    yield frame

        credentials = self.settings.require_chirp()
        client = speech_v2.SpeechAsyncClient(
            client_options={"api_endpoint": f"{self.settings.gcp_speech_region}-speech.googleapis.com"})
        recognizer = (f"projects/{credentials['GOOGLE_CLOUD_PROJECT']}/locations/"
                      f"{self.settings.gcp_speech_region}/recognizers/_")
        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.settings.pcm_sample_rate_hz,
                audio_channel_count=1,
            ),
            language_codes=["sq-AL"],
            model=self.settings.chirp_model,
            features=cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
        )
        streaming_config = cloud_speech.StreamingRecognitionConfig(config=config)

        async def requests() -> AsyncIterator[Any]:
            yield cloud_speech.StreamingRecognizeRequest(
                recognizer=recognizer, streaming_config=streaming_config)
            async for frame in to_async():
                if self._cancelled:
                    return
                yield cloud_speech.StreamingRecognizeRequest(audio=frame)

        started = time.monotonic()
        self._stream = await client.streaming_recognize(requests=requests())
        async for response in self._stream:
            if self._cancelled:
                return
            for result in response.results:
                if not result.alternatives:
                    continue
                best = result.alternatives[0]
                yield Transcript(
                    text=best.transcript,
                    final=bool(result.is_final),
                    confidence=float(best.confidence) if best.confidence else None,
                    alternatives=tuple(item.transcript for item in result.alternatives[1:]),
                    provider="chirp_3", started_s=started,
                    finalized_s=time.monotonic() if result.is_final else None,
                    diagnostics={"stability": float(result.stability)},
                )

    def start(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]:
        self._cancelled = False
        return self._run(audio)

    async def stop(self) -> None:
        if self._stream is not None and hasattr(self._stream, "cancel"):
            self._stream.cancel()

    async def cancel(self) -> None:
        self._cancelled = True
        await self.stop()
