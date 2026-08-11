"""Deterministic Albanian SSML canonicalizer for Schema 1 §5."""

from __future__ import annotations

import re
from html import escape

ACRONYMS = {"ALL": "A L L", "EUR": "E U R", "USD": "U S D", "PIN": "P I N",
            "CVV": "C V V", "CVC": "C V C", "OTP": "O T P"}
BANK_PRONUNCIATION = {
    "Banka OTP Albania": "Banka O T P Albania",
    "Banka BKT": "Banka B K T",
}
TOKEN_RE = re.compile(
    r"\b(?:ALL|EUR|USD|PIN|CVV|CVC|OTP)\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|"
    r"\b\d+(?:[,.]\d+)?\s*(?:%|ALL|EUR|USD)(?=\s|[.,!?;:]|$)"
)


def _token_ssml(token: str) -> str:
    if token in BANK_PRONUNCIATION:
        return f'<sub alias="{escape(BANK_PRONUNCIATION[token])}">{escape(token)}</sub>'
    if token in ACRONYMS:
        return f'<sub alias="{escape(ACRONYMS[token])}">{escape(token)}</sub>'
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", token):
        return f'<say-as interpret-as="date">{escape(token)}</say-as>'
    match = re.fullmatch(r"(\d+(?:[,.]\d+)?)\s*(%|ALL|EUR|USD)", token)
    if match:
        value, unit = match.groups()
        unit_alias = {"%": "për qind", "ALL": "lekë", "EUR": "euro", "USD": "dollarë"}[unit]
        separator_alias = value.replace(",", " presje ").replace(".", " pikë ")
        return (f'<sub alias="{escape(separator_alias)} {unit_alias}">'
                f'{escape(token)}</sub>')
    return escape(token)


def canonicalize(approved_text: str, voice: str = "sq-AL-AnilaNeural") -> str:
    """Wrap exact approved values in deterministic SSML without paraphrasing them."""
    pattern = re.compile(
        "|".join(re.escape(name) for name in sorted(BANK_PRONUNCIATION, key=len, reverse=True))
        + "|" + TOKEN_RE.pattern
    )
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(approved_text):
        pieces.append(escape(approved_text[cursor:match.start()]))
        pieces.append(_token_ssml(match.group(0)))
        cursor = match.end()
    pieces.append(escape(approved_text[cursor:]))
    body = "".join(pieces)
    return (f'<speak version="1.0" xml:lang="sq-AL"><voice name="{escape(voice)}">'
            f'{body}</voice></speak>')
