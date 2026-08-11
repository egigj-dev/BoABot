"""Deterministic `/turn` service double for both offline schema demos."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .events import TurnDone, TurnRequest
from .turn_client import EventHandler, TurnResult, _notify


@dataclass(slots=True)
class ScriptedTurnService:
    """Return only configured authoritative token deltas through ``TurnService``."""

    text: str
    outcome: str = "answer"
    session_id: str = "offline-session"
    sources: tuple[dict[str, str], ...] = ()
    vetted_chunks: tuple[str, ...] = ()
    handoff: bool = False
    chunk_chars: int = 12
    delay_ms: float = 0
    requests: list[TurnRequest] = field(default_factory=list, init=False)
    cancelled: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.cancelled = False

    async def run(self, request: TurnRequest, on_event: EventHandler | None = None) -> TurnResult:
        self.requests.append(request)
        tokens: list[str] = []
        tool = {"type": "tool", "query": request.question}
        await _notify(on_event, tool)
        for offset in range(0, len(self.text), self.chunk_chars):
            if self.cancelled:
                break
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000)
            token = self.text[offset:offset + self.chunk_chars]
            tokens.append(token)
            await _notify(on_event, {"type": "token", "text": token})
        done_sources = [dict(source) for source in self.sources]
        if request.include_vetted_text:
            for index, source in enumerate(done_sources):
                passage = self.vetted_chunks[index] if index < len(self.vetted_chunks) else ""
                source.setdefault("passage_text", passage)
        else:
            for source in done_sources:
                source.pop("passage_text", None)
        done = TurnDone(self.outcome, self.session_id, tuple(done_sources), self.handoff)
        await _notify(on_event, {"type": "done", "outcome": done.outcome,
                                 "session_id": done.session_id,
                                 "sources": list(done.sources), "handoff": done.handoff,
                                 "pii_redacted": False, "usage": {}})
        passages = tuple(source["passage_text"] for source in done.sources
                         if source.get("passage_text"))
        return TurnResult(tuple(tokens), done, (tool,), passages)

    async def cancel(self) -> None:
        self.cancelled = True
