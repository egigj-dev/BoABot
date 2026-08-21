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


# Curated lexicon of high-frequency Albanian banking terms whose diacritics
# (ë, ç) users commonly type stripped (ASCII-only). The mapping is FROM the
# folded lossy form TO the canonical diacritic form, so restoration only fires
# when (a) the input token carried NO diacritics (it was typed lossily) and
# (b) the folded token is an exact lexicon hit -- never by guessing, so
# ordinary ASCII tokens (numbers, code, English loans) pass through untouched.
# This is deliberately conservative: unrestored unknown tokens are left as the
# user typed them rather than risk corrupting a word.
_DIACRITIC_LEXICON = {
    "cfar": "çfarë", "cfare": "çfarë",
    "cile": "çilë", "cili": "çili", "cilin": "çilin", "cilat": "çilat",
    "eshte": "është", "esht": "është",
    "pershendetje": "përshëndetje",
    "shqiperi": "shqipëria", "shaqiperi": "shqipëria",
    "bankes": "bankës",
    "qendrueshem": "qëndrueshëm",
    "pergjigje": "përgjigje", "pergjigjen": "përgjigjen",
    "perdoruesit": "përdoruesit",
    "perkateshe": "përkatëse",
    "permbajne": "përmbajnë", "permbaj": "përmbaj",
}
import re as _re
_WORD_RE = _re.compile(r"[^\W_]+", _re.UNICODE)


def restore_diacritics(text: str) -> str:
    """Restore known ë/ç diacritics on lossily-typed tokens (best effort).

    Token-level and lexicon-bounded: only whitespace-separated words get
    checked; a word is replaced exactly when it folded to a lexicon key AND
    the original word carried no diacritics. Unknown ASCII, numbers, and
    already-diacritic words are left untouched. The first letter's case is
    preserved from the input token.
    """
    if not text:
        return text

    def _restore_token(token: str) -> str:
        folded = fold(token)
        canonical = _DIACRITIC_LEXICON.get(folded)
        if canonical is None:
            return token
        # Only restore a token the user typed WITHOUT diacritics; already
        # diacritic forms are already canonical and must not be mangled.
        if any(ord(ch) > 127 for ch in token):
            return token
        if token[:1].isupper() and canonical:
            canonical = canonical[0].upper() + canonical[1:]
        return canonical

    # Split on whitespace, restore each word-like run, preserve the original
    # whitespace and punctuation shadow around it.
    out: list[str] = []
    pos = 0
    for match in _WORD_RE.finditer(text):
        out.append(text[pos:match.start()])
        out.append(_restore_token(match.group(0)))
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)
