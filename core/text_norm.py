"""Shared Unicode normalization for trust-boundary text comparisons."""

from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Case-fold text and remove combining marks without changing whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )


def fold_ws(text: str) -> str:
    """Return :func:`fold` output with whitespace collapsed."""
    return " ".join(fold(text).split())
