"""Schema 2 §3 strict call, turn, generation, and render correlation registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import GenerationId, TurnId


class CorrelationError(ValueError):
    pass


@dataclass(slots=True)
class Correlation:
    call_id: str
    session_id: str
    turn_id: TurnId
    generation_id: GenerationId
    live_session_id: str | None = None
    render_ids: set[str] = field(default_factory=set)


class CorrelationRegistry:
    """Reject missing, duplicate, or stale identifiers before output admission."""

    def __init__(self) -> None:
        self._calls: dict[str, Correlation] = {}

    def open_call(self, call_id: str, session_id: str, live_session_id: str | None = None) -> Correlation:
        if not call_id or not session_id:
            raise CorrelationError("call_id and session_id are required")
        if call_id in self._calls:
            raise CorrelationError(f"duplicate call_id: {call_id}")
        item = Correlation(call_id, session_id, TurnId(0), GenerationId(0), live_session_id)
        self._calls[call_id] = item
        return item

    def next_turn(self, call_id: str) -> tuple[TurnId, GenerationId]:
        item = self.require(call_id)
        item.turn_id = TurnId(int(item.turn_id) + 1)
        item.generation_id = GenerationId(int(item.generation_id) + 1)
        item.render_ids.clear()
        return item.turn_id, item.generation_id

    def invalidate_generation(self, call_id: str) -> GenerationId:
        item = self.require(call_id)
        item.generation_id = GenerationId(int(item.generation_id) + 1)
        item.render_ids.clear()
        return item.generation_id

    def register_render(self, call_id: str, turn_id: TurnId, request_id: str,
                        generation_id: GenerationId | None = None) -> None:
        current = self.require(call_id)
        item = self.validate(call_id, turn_id, generation_id or current.generation_id)
        if not request_id or request_id in item.render_ids:
            raise CorrelationError("missing or duplicate render request id")
        item.render_ids.add(request_id)

    def validate(self, call_id: str, turn_id: TurnId, generation_id: GenerationId,
                 render_request_id: str | None = None) -> Correlation:
        item = self.require(call_id)
        if turn_id != item.turn_id or generation_id != item.generation_id:
            raise CorrelationError("stale turn or generation id")
        if render_request_id is not None and render_request_id not in item.render_ids:
            raise CorrelationError("unknown render request id")
        return item

    def update_session(self, call_id: str, session_id: str) -> None:
        if not session_id:
            raise CorrelationError("session_id is required")
        self.require(call_id).session_id = session_id

    def require(self, call_id: str) -> Correlation:
        try:
            return self._calls[call_id]
        except KeyError as exc:
            raise CorrelationError(f"unknown call_id: {call_id}") from exc

    def close_call(self, call_id: str) -> None:
        if call_id not in self._calls:
            raise CorrelationError(f"unknown call_id: {call_id}")
        del self._calls[call_id]
