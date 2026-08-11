"""Fail-closed post-``/turn`` answer verifier for Schema 1 §5 and Schema 2 §5."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CURRENCY_RE = r"(?:ALL|EUR|USD|lek(?:ë|e)?|euro|dollar(?:ë|e)?)"
NUMBER_RE = r"\d{1,3}(?:[ .]\d{3})*(?:[,.]\d+)?|\d+(?:[,.]\d+)?"
VALUE_RE = re.compile(rf"(?P<value>{NUMBER_RE})\s*(?P<unit>%|{CURRENCY_RE})?", re.IGNORECASE)
BANK_RE = re.compile(r"\b(?:Banka|Bankën|Bankës)\s+[A-ZËÇ][\wËÇëç.-]*(?:\s+[A-ZËÇ][\wËÇëç.-]*){0,5}")
DOCUMENT_RE = re.compile(r"\b(?:Rregullor(?:ja|es)|Ligj(?:i|it)|Udhëzim(?:i|it)|Regjistri)\s+(?:i\s+|e\s+)?[A-ZËÇ][\wËÇëç-]*(?:\s+[\wËÇëç-]+){0,5}")
LABEL_STOPWORDS = {
    "eshte", "jane", "per", "me", "ne", "nga", "dhe", "ose", "nje", "ky", "kjo",
    "vlera", "shuma", "norma", "komisioni", "tarifa", "cmimi", "i", "e", "te", "se",
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).split())


def _number(value: str) -> Decimal:
    compact = value.replace(" ", "")
    if "," in compact and "." in compact:
        decimal_mark = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands = "." if decimal_mark == "," else ","
        compact = compact.replace(thousands, "").replace(decimal_mark, ".")
    elif "," in compact:
        compact = compact.replace(",", ".")
    return Decimal(compact)


@dataclass(frozen=True, slots=True)
class Claim:
    value: Decimal
    unit: str
    label: str


@dataclass(frozen=True, slots=True)
class FidelityResult:
    approved: bool
    reason: str = ""
    claims: tuple[Claim, ...] = ()


class FidelityGuard:
    """Only suppresses output; it never approves evidence or changes relevance gates."""

    def verify_sources(self, sentence: str,
                       sources: Iterable[Mapping[str, object]]) -> FidelityResult:
        """Verify against optional vetted passage text carried by cited sources."""
        passages = (text for source in sources
                    if isinstance((text := source.get("passage_text")), str) and text)
        return self.verify(sentence, passages)

    def extract_claims(self, sentence: str) -> tuple[Claim, ...]:
        claims: list[Claim] = []
        for match in VALUE_RE.finditer(sentence):
            try:
                value = _number(match.group("value"))
            except InvalidOperation:
                continue
            unit = _fold(match.group("unit") or "number")
            left = sentence[max(0, match.start() - 90):match.start()]
            label_words = re.findall(r"[\wËÇëç-]+", left)[-8:]
            claims.append(Claim(value, unit, _fold(" ".join(label_words))))
        return tuple(claims)

    def extract_entities(self, sentence: str) -> tuple[str, ...]:
        found = [*BANK_RE.findall(sentence), *DOCUMENT_RE.findall(sentence)]
        return tuple(dict.fromkeys(_fold(item) for item in found))

    def verify(self, sentence: str, vetted_chunks: Iterable[str]) -> FidelityResult:
        """Require each value/unit and named entity in compatible vetted chunk context."""
        chunks = tuple(_fold(chunk) for chunk in vetted_chunks if chunk)
        claims = self.extract_claims(sentence)
        entities = self.extract_entities(sentence)
        if not claims and not entities:
            return FidelityResult(True)
        if not chunks:
            return FidelityResult(False, "factual sentence has no cited vetted chunk text", claims)
        for entity in entities:
            if not any(entity in chunk for chunk in chunks):
                return FidelityResult(False, f"entity absent from vetted chunks: {entity}", claims)
        for claim in claims:
            compatible = False
            sentence_label_tokens = set(claim.label.split()) - LABEL_STOPWORDS
            for chunk in chunks:
                chunk_claims = self.extract_claims(chunk)
                for evidence in chunk_claims:
                    same_unit = claim.unit == evidence.unit or "number" in {claim.unit, evidence.unit}
                    evidence_label_tokens = set(evidence.label.split()) - LABEL_STOPWORDS
                    label_compatible = (sentence_label_tokens == evidence_label_tokens
                                        if sentence_label_tokens and evidence_label_tokens
                                        else sentence_label_tokens == evidence_label_tokens)
                    if claim.value == evidence.value and same_unit and label_compatible:
                        compatible = True
                        break
                if compatible:
                    break
            if not compatible:
                return FidelityResult(False, f"value/unit/label mismatch: {claim.value} {claim.unit}", claims)
        return FidelityResult(True, claims=claims)
