"""Opaque BOA session-id helpers for HTTP voice transports.

No banking data is kept here: the caller supplies an id and BOA owns its
meaning and all associated dialogue state.
"""

from __future__ import annotations

import uuid

from starlette.datastructures import Headers

SESSION_HEADER = "X-BoABot-Session-Id"


def request_boa_session_id(headers: Headers) -> str:
    supplied = headers.get(SESSION_HEADER, "").strip()
    return supplied or f"voice-{uuid.uuid4().hex}"
