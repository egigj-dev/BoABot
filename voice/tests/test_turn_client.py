"""Exact ``api.py`` tool/token/done SSE contract tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from api import TurnReq
from voice.events import TurnId, TurnRequest
from voice.turn_client import FirstTokenDeadline, TurnClient


def _sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def test_turn_client_parses_exact_sse_contract() -> None:
    async def scenario() -> None:
        app = FastAPI()

        @app.post("/turn")
        async def turn() -> StreamingResponse:
            async def events():
                yield _sse({"type": "tool", "query": "telemetry only"})
                yield _sse({"type": "token", "text": "Përgjigje "})
                yield _sse({"type": "token", "text": "e aprovuar."})
                yield _sse({"type": "approved_sentence", "text": "Përgjigje e aprovuar."})
                yield _sse({"type": "done", "outcome": "answer", "session_id": "s-1",
                            "sources": [{"id": "x", "doc": "Rregullore", "article": "1",
                                         "url": "u", "passage_text": "Norma është 2.5%."}],
                            "handoff": False, "pii_redacted": False, "usage": {"completion_tokens": 2}})
            return StreamingResponse(events(), media_type="text/event-stream")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as http:
            seen: list[str] = []
            client = TurnClient("http://test", client=http)
            result = await client.run(TurnRequest("pyetje", None, TurnId(1)),
                                      lambda event: seen.append(str(event["type"])))
        assert "".join(result.tokens) == "Përgjigje e aprovuar."
        assert result.done.outcome == "answer"
        assert result.done.sources[0]["id"] == "x"
        assert result.done.sources[0]["passage_text"] == "Norma është 2.5%."
        assert result.vetted_chunks == ("Norma është 2.5%.",)
        assert result.approved_sentences == ("Përgjigje e aprovuar.",)
        assert seen == ["tool", "token", "token", "approved_sentence", "done"]

    asyncio.run(scenario())


def test_turn_client_tolerates_done_source_without_passage_text() -> None:
    async def scenario() -> None:
        app = FastAPI()

        @app.post("/turn")
        async def turn() -> StreamingResponse:
            async def events():
                yield _sse({"type": "token", "text": "Pa fakt numerik."})
                yield _sse({"type": "done", "outcome": "answer", "session_id": "s-2",
                            "sources": [{"id": "x", "doc": "Rregullore", "article": "1",
                                         "url": "u"}],
                            "handoff": False, "pii_redacted": False, "usage": {}})
            return StreamingResponse(events(), media_type="text/event-stream")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as http:
            result = await TurnClient("http://test", client=http).run(
                TurnRequest("pyetje", None, TurnId(1)))
        assert "passage_text" not in result.done.sources[0]
        assert result.vetted_chunks == ()

    asyncio.run(scenario())


def test_turn_request_serializes_vetted_text_opt_in() -> None:
    request = TurnRequest("pyetje", "session", TurnId(1), include_vetted_text=True)
    assert request.wire_payload() == {
        "question": "pyetje",
        "session_id": "session",
        "include_vetted_text": True,
    }


def test_api_turn_req_vetted_text_defaults_off() -> None:
    assert TurnReq(question="xy").include_vetted_text is False


def test_turn_client_has_true_first_token_wall_deadline() -> None:
    async def scenario() -> None:
        app = FastAPI()

        @app.post("/turn")
        async def turn() -> StreamingResponse:
            async def events():
                await asyncio.sleep(0.08)
                yield _sse({"type": "token", "text": "vonë"})
                yield _sse({"type": "done", "outcome": "answer", "session_id": "s",
                            "sources": [], "handoff": False, "pii_redacted": False, "usage": {}})
            return StreamingResponse(events(), media_type="text/event-stream")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as http:
            with pytest.raises(FirstTokenDeadline):
                await TurnClient("http://test", first_token_budget_ms=10, client=http).run(
                    TurnRequest("pyetje", None, TurnId(1)))

    asyncio.run(scenario())

def test_turn_client_cancels_only_the_requested_concurrent_turn() -> None:
    async def scenario() -> None:
        client = TurnClient("http://test")
        release = asyncio.Event()

        async def owner() -> None:
            await release.wait()

        first = asyncio.create_task(owner())
        second = asyncio.create_task(owner())

        class Response:
            def __init__(self) -> None:
                self.closed = False

            async def aclose(self) -> None:
                self.closed = True

        first_response = Response()
        second_response = Response()
        client._owners.update({"first": first, "second": second})
        client._responses.update({"first": first_response, "second": second_response})

        await client.cancel("first")
        await asyncio.sleep(0)
        assert first.cancelled()
        assert first_response.closed
        assert not second.cancelled()
        assert not second_response.closed

        release.set()
        await second

    asyncio.run(scenario())
