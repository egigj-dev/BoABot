"""Neutral StreamingASR interface from Schema 1 §3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator

from ...shared.events import Transcript


class StreamingASR(ABC):
    """Adapters emit transcript diagnostics only, never answers or intents."""

    @abstractmethod
    def start(self, audio: AsyncIterable[bytes]) -> AsyncIterator[Transcript]: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def cancel(self) -> None: ...
