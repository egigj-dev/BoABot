"""HTTP/SSE client for the authoritative ``POST /turn`` contract (Schema 1 §3)."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .events import TurnDone, TurnRequest

TERMINAL_OUTCOMES = {
    "answer", "clarify", "unsupported", "handoff", "repeat", "degraded",
}
EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class TurnClientError(RuntimeError):
    pass


class FirstTokenDeadline(TurnClientError):
    pass


class TurnCancelled(TurnClientError):
    pass


@dataclass(frozen=True, slots=True)
class TurnResult:
    tokens: tuple[str, ...]
    done: TurnDone
    tool_events: tuple[dict[str, Any], ...] = ()
    # Derived only from optional passage_text fields on cited done.sources.
    vetted_chunks: tuple[str, ...] = ()
    approved_sentences: tuple[str, ...] = ()


class TurnService(Protocol):
    async def run(self, request: TurnRequest, on_event: EventHandler | None = None) -> TurnResult: ...

    async def cancel(self, correlation_key: str | None = None) -> None: ...


async def _notify(handler: EventHandler | None, event: dict[str, Any]) -> None:
    if handler is None:
        return
    result = handler(event)
    if inspect.isawaitable(result):
        await result


class TurnClient:
    """Consume tool/token/done events and close the upstream response on cancel."""

    def __init__(self, base_url: str, first_token_budget_ms: int = 6000,
                 client: httpx.AsyncClient | None = None,
                 voice_bridge_key: str | None = None) -> None:
        self.url = f"{base_url.rstrip('/')}/turn"
        self.first_token_budget_s = first_token_budget_ms / 1000
        self._provided_client = client
        self.voice_bridge_key = voice_bridge_key or os.environ.get("BOABOT_VOICE_BRIDGE_KEY")
        self._responses: dict[str, httpx.Response] = {}
        self._owners: dict[str, asyncio.Task[Any]] = {}
        self._cancel_requested: set[str] = set()

    async def run(self, request: TurnRequest, on_event: EventHandler | None = None) -> TurnResult:
        """Run one turn with a true wall-clock deadline until the first token."""
        try:
            from httpx_sse import aconnect_sse  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TurnClientError("httpx-sse is required for TurnClient") from exc

        owned_client = self._provided_client is None
        client = self._provided_client or httpx.AsyncClient(timeout=None)
        context: Any = None
        entered = False
        key = request.correlation_key
        if key in self._owners:
            raise TurnClientError(f"turn already active for correlation key {key!r}")
        owner = asyncio.current_task()
        assert owner is not None
        self._owners[key] = owner
        self._cancel_requested.discard(key)
        started = time.monotonic()
        tokens: list[str] = []
        tools: list[dict[str, Any]] = []
        approved_sentences: list[str] = []
        done: TurnDone | None = None
        first_token = False
        try:
            headers = {"Accept": "text/event-stream"}
            if request.include_vetted_text and self.voice_bridge_key:
                headers["X-BoABot-Voice-Key"] = self.voice_bridge_key
            context = aconnect_sse(
                client, "POST", self.url, json=request.wire_payload(), headers=headers,
            )
            remaining = self.first_token_budget_s - (time.monotonic() - started)
            if remaining <= 0:
                raise FirstTokenDeadline("/turn first-token deadline exceeded")
            try:
                source = await asyncio.wait_for(context.__aenter__(), timeout=remaining)
                entered = True
            except asyncio.TimeoutError as exc:
                raise FirstTokenDeadline("/turn first-token deadline exceeded") from exc
            self._responses[key] = source.response
            self._responses[key].raise_for_status()
            iterator = source.aiter_sse()
            while done is None:
                try:
                    if first_token:
                        sse_event = await iterator.__anext__()
                    else:
                        remaining = self.first_token_budget_s - (time.monotonic() - started)
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        sse_event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    await self._close_response(key)
                    raise FirstTokenDeadline("/turn first-token deadline exceeded") from exc
                try:
                    event = json.loads(sse_event.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise TurnClientError("invalid JSON in /turn SSE stream") from exc
                if not isinstance(event, dict):
                    raise TurnClientError("invalid /turn SSE event")
                event_type = event.get("type")
                if event_type == "tool":
                    tools.append(event)
                    await _notify(on_event, event)
                elif event_type == "token":
                    text = event.get("text")
                    if not isinstance(text, str):
                        raise TurnClientError("token event missing text")
                    first_token = True
                    tokens.append(text)
                    await _notify(on_event, event)
                elif event_type == "approved_sentence":
                    text = event.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise TurnClientError("approved_sentence event missing text")
                    approved_sentences.append(text)
                    await _notify(on_event, event)
                elif event_type == "done":
                    outcome = event.get("outcome")
                    session_id = event.get("session_id")
                    if outcome not in TERMINAL_OUTCOMES or not isinstance(session_id, str) or not session_id:
                        raise TurnClientError("malformed terminal done event")
                    raw_sources = event.get("sources") or []
                    if not isinstance(raw_sources, list) or not all(isinstance(item, dict) for item in raw_sources):
                        raise TurnClientError("malformed sources in done event")
                    done = TurnDone(
                        outcome=outcome, session_id=session_id,
                        sources=tuple({str(k): str(v) for k, v in item.items()} for item in raw_sources),
                        handoff=bool(event.get("handoff")),
                        pii_redacted=bool(event.get("pii_redacted")),
                        usage=event.get("usage") if isinstance(event.get("usage"), dict) else {},
                        reason=str(event["reason"]) if event.get("reason") else None,
                    )
                    await _notify(on_event, event)
                elif event_type == "error":
                    raise TurnClientError(str(event.get("message") or "/turn error event"))
                else:
                    raise TurnClientError(f"unexpected /turn event type: {event_type!r}")
            if done is None:
                raise TurnClientError("/turn stream ended without terminal done event")
            vetted_chunks = tuple(source["passage_text"] for source in done.sources
                                  if source.get("passage_text"))
            return TurnResult(
                tuple(tokens), done, tuple(tools), vetted_chunks,
                tuple(approved_sentences),
            )
        except asyncio.CancelledError as exc:
            if key in self._cancel_requested:
                raise TurnCancelled("/turn cancelled") from exc
            raise
        finally:
            if context is not None and entered:
                await context.__aexit__(None, None, None)
            self._responses.pop(key, None)
            self._owners.pop(key, None)
            self._cancel_requested.discard(key)
            if owned_client:
                await client.aclose()

    async def _close_response(self, correlation_key: str) -> None:
        response = self._responses.get(correlation_key)
        if response is not None:
            await response.aclose()

    async def cancel(self, correlation_key: str | None = None) -> None:
        if correlation_key is None:
            active = tuple(self._owners)
            if not active:
                return
            if len(active) != 1:
                raise TurnClientError("correlation_key is required with concurrent turns")
            correlation_key = active[0]
        self._cancel_requested.add(correlation_key)
        await self._close_response(correlation_key)
        owner = self._owners.get(correlation_key)
        if owner is not None and owner is not asyncio.current_task():
            owner.cancel()
