"""Schema 1 §3 token-only sentence buffering with ``api.py`` boundary semantics."""

from __future__ import annotations

import re

SENTENCE_END_RE = re.compile(r"[.!?][\"'»”\)\]]?(?:\s|$)")


class SentenceBuffer:
    """Release complete punctuation-terminated sentences from token deltas."""

    def __init__(self) -> None:
        self._text = ""

    @property
    def pending(self) -> str:
        return self._text

    def feed_token(self, text: str) -> list[str]:
        self._text += text
        released: list[str] = []
        while (match := SENTENCE_END_RE.search(self._text)) is not None:
            boundary = match.end()
            sentence = self._text[:boundary].strip()
            self._text = self._text[boundary:]
            if sentence:
                released.append(sentence)
        return released

    def feed_event(self, event: dict[str, object]) -> list[str]:
        """Allowlist token events; tool content can never enter the text buffer."""
        if event.get("type") != "token":
            return []
        value = event.get("text")
        return self.feed_token(value if isinstance(value, str) else "")

    def finish(self) -> list[str]:
        tail = self._text.strip()
        self._text = ""
        return [tail] if tail else []

    def clear(self) -> None:
        self._text = ""
