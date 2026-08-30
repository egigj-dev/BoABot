"""Fail-closed post-``/turn`` answer verifier for Schema 1 §5 and Schema 2 §5."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.text_norm import fold_ws

CURRENCY_RE = r"(?:ALL|EUR|USD|lek(?:ë|e)?|euro|dollar(?:ë|e)?)"
NUMBER_RE = r"\d{1,3}(?:[ .'’]\d{3})+(?:[,.]\d+)?|\d+(?:[,.]\d+)?"
VALUE_RE = re.compile(
    rf"(?P<value>{NUMBER_RE})\s*(?P<unit>%|p[eë]r\s+qind|{CURRENCY_RE})?",
    re.IGNORECASE,
)
# NB: the token class deliberately EXCLUDES '.' — bank names never contain
# dots in this corpus, and a sentence-final period ("...te Banka Union.") must
# not become part of the entity, or it can never match evidence "Banka Union:".
BANK_RE = re.compile(r"\b(?i:Banka|Bankën|Bankës)\s+[A-ZËÇ][\wËÇëç-]*(?:\s+[A-ZËÇ][\wËÇëç-]*){0,5}")
DOCUMENT_RE = re.compile(
    r"\b(?:"
    r"(?i:Rregullor(?:ja|es|en)|Ligj(?:i|it)|Udhëzim(?:i|it))\s+"
    r"(?:i\s+|e\s+)?(?!(?:Nr|Banka|Bankën|Bankës)\b)"
    r"[A-ZËÇ][\wËÇëç-]*(?:\s+[\wËÇëç-]+){0,5}"
    r"|(?i:Regjistr(?:i|it|in))\s+(?:i\s+|e\s+|të\s+)?[A-ZËÇ][\wËÇëç-]*)"
)
LABEL_STOPWORDS = {
    "aplikon", "dokumentit", "eshte", "individe", "individeve", "jane", "ka", "lidhur", "ofron", "per", "perket", "prej", "produkt", "produktin", "me",
    "gjendet", "korpus", "ne", "nga", "dhe", "nuk", "ose", "nje", "perqindja", "perqindje", "perqindjen", "por", "prane", "rastin", "sa", "sherbim", "sherbimin", "sipas", "vendos", "zbaton",
    "ky", "kjo", "regjistruar", "shuma", "i",
    "e", "te", "se",
}
ENTITY_HEAD_FORMS = {
    "banka": "banka",
    "banken": "banka",
    "bankes": "banka",
    "rregullorja": "rregullorja",
    "rregullores": "rregullorja",
    "rregulloren": "rregullorja",
    "ligji": "ligji",
    "ligjit": "ligji",
    "udhezimi": "udhezimi",
    "udhezimit": "udhezimi",
    "regjistri": "regjistri",
}
LABEL_TOKEN_FORMS = {
    "bankes": "banka",
    "banken": "banka",
    "kredine": "kredi",
    "kredise": "kredi",
    "komision": "komisioni",
    "komisione": "komisioni",
    "komisionet": "komisioni",
    "depozitave": "depozita",
    "depozitat": "depozita",
    "depoziten": "depozita",
    "depozites": "depozita",
    "biznese": "biznes",
    "biznesi": "biznes",
    "bizneseve": "biznes",
    "bizneset": "biznes",
    "banka": "bank",
    "bankave": "bank",
    "bankat": "bank",
    "banken": "bank",
    "bankes": "bank",
    "karte": "karte",
    "kartes": "karte",
    "kredi": "kredi",
    "krediti": "kredi",
    "kreditit": "kredi",
    "debiti": "debit",
    "debitit": "debit",
    "leshimi": "leshim",
    "leshimin": "leshim",
    "llogaria": "llogari",
    "llogarise": "llogari",
    "administrimi": "administrim",
    "administrimit": "administrim",
    "dhenies": "dhenia",
    "komisionit": "komisioni",
    "leshimit": "leshim",
    "maksimal": "max",
    "maksimale": "max",
    "maksimumi": "max",
    "minimal": "min",
    "minimale": "min",
    "minimumi": "min",
    "mujore": "muaj",
    "mujor": "muaj",
    "normat": "norma",
    "norme": "norma",
    "normen": "norma",
    "normes": "norma",
    "interesi": "interesit",
    "interesit": "interesit",
    "parakoheshme": "parakohshme",
    "pasiguruara": "pasiguruar",
    "pjeserisht": "pjesore",
    "totalisht": "totale",
    "tre": "3",
    "shlyerjen": "shlyerje",
}

LABEL_CONFLICT_FAMILIES = (
    frozenset({"karte", "kredi", "depozita", "llogari"}),
    frozenset({
        "administrim", "disbursim", "leshim", "mirembajtje", "shlyerje",
        "terheqje", "transferim", "ndryshim",
    }),
    frozenset({"min", "max"}),
    frozenset({"debit", "kredit"}),
    frozenset({"komisioni", "tarifa", "cmimi", "norma", "interesit"}),
)

# Bounded claim-frame words that appear in natural answer phrasing but carry no
# product/service meaning of their own. They may be present in a sentence claim
# without evidence-label coverage (the VALUE and BANK binding still gate).
# Deliberately small: a service word missing from the evidence still fails.
_GENERIC_CLAIM_TOKENS = frozenset({
    "komision", "komisioni", "komisionit", "komisione", "komisionet",
    "komisionesh", "tarif", "tarifa", "tarifat", "tarifes", "tarifave",
    "aplikon", "aplikojne", "aplikonte", "prej", "sipas", "tabelave",
    "tabela", "tabelat", "publikuara", "publikuar", "nje", "vjetor",
    "vjetore", "vjetori", "kushton", "kosto",
})


def _fold(text: str) -> str:
    return fold_ws(text)


def _entity_comparison_key(entity: str) -> str:
    """Fold only grammatical head forms; retain the complete distinguishing name."""
    words = _fold(entity).split()
    if words:
        words[0] = ENTITY_HEAD_FORMS.get(words[0], words[0])
    return " ".join(words)


def _folded_bank_keys(label: str) -> set[str]:
    """Extract a table-row bank identity from an already-folded claim label."""
    match = re.search(r"\bbank(?:a|en|es)\s+([\w.-]+(?:\s+[\w.-]+){0,5})$", label)
    return {f"banka {match.group(1)}"} if match else set()


def _label_tokens(label: str) -> set[str]:
    """Normalize only known Albanian inflections and table abbreviations."""
    return {
        normalized
        for word in re.findall(r"[\w]+", _fold(label))
        if (normalized := LABEL_TOKEN_FORMS.get(word, word)) not in LABEL_STOPWORDS
    }


def _number(value: str) -> Decimal:
    compact = value.replace(" ", "").replace("'", "").replace("’", "")
    if "," in compact and "." in compact:
        decimal_mark = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands = "." if decimal_mark == "," else ","
        compact = compact.replace(thousands, "").replace(decimal_mark, ".")
    elif "," in compact:
        compact = compact.replace(",", ".")
    elif "." in compact:
        groups = compact.split(".")
        # A single dot is ambiguous (1.000 can mean 1.0). Only an unambiguous
        # multi-group form is treated as thousands without source-locale context.
        if len(groups) >= 3 and all(len(group) == 3 for group in groups[1:]):
            compact = "".join(groups)
    return Decimal(compact)


def _has_label_conflict(sentence_tokens: set[str], evidence_tokens: set[str]) -> bool:
    for family in LABEL_CONFLICT_FAMILIES:
        sentence_values = sentence_tokens & family
        evidence_values = evidence_tokens & family
        if sentence_values and evidence_values and sentence_values.isdisjoint(evidence_values):
            return True
    return False


@dataclass(frozen=True, slots=True)
class Claim:
    value: Decimal
    unit: str
    label: str
    table_row: bool = False


@dataclass(frozen=True, slots=True)
class FidelityResult:
    approved: bool
    reason: str = ""
    claims: tuple[Claim, ...] = ()


class FidelityGuard:
    """Verify quantities and named entities; qualitative claims remain out of scope.

    The guard only suppresses output. It never approves evidence, changes a
    relevance gate, or claims to verify sentences with no numeric/entity claim.
    """

    def verify_sources(self, sentence: str,
                       sources: Iterable[Mapping[str, object]]) -> FidelityResult:
        """Verify against optional vetted passage text carried by cited sources."""
        passages = (text for source in sources
                    if isinstance((text := source.get("passage_text")), str) and text)
        return self.verify(sentence, passages)

    def extract_claims(self, sentence: str) -> tuple[Claim, ...]:
        claims: list[Claim] = []
        lines = sentence.splitlines() or [sentence]
        first_value_line = next(
            (index for index, line in enumerate(lines) if VALUE_RE.search(line)),
            len(lines),
        )
        first_table_row = next((
            index
            for index, line in enumerate(lines)
            if any(":" in line[:match.start()] for match in VALUE_RE.finditer(line))
        ), None)
        header_end = first_table_row if first_table_row is not None else first_value_line
        header = " ".join(
            line.strip() for line in lines[:header_end] if line.strip()
        )
        content_start = 0 if first_table_row is not None else first_value_line
        for line_index, line in enumerate(lines[content_start:], content_start):
            matches = tuple(VALUE_RE.finditer(line))
            for match in matches:
                try:
                    value = _number(match.group("value"))
                except InvalidOperation:
                    continue
                unit = _fold(match.group("unit") or "number")
                if unit == "per qind":
                    unit = "%"
                if header and first_table_row is not None and line_index >= first_table_row:
                    # Keep the table's product/service header and only the
                    # current row prefix. Never pull context from another row.
                    label_words = (
                        re.findall(r"[\wËÇëç-]+", header)[-24:]
                        + re.findall(r"[\wËÇëç-]+", line[:match.start()])[-8:]
                    )
                else:
                    # A generated sentence can contain both the quoted value
                    # and a qualifier such as a 36-month term. Give every
                    # claim the full non-numeric sentence context; using only
                    # the prefix made the first value lose decisive qualifiers
                    # that appeared later in the sentence.
                    context_parts: list[str] = []
                    cursor = 0
                    for numeric_match in matches:
                        context_parts.append(line[cursor:numeric_match.start()])
                        cursor = numeric_match.end()
                    context_parts.append(line[cursor:])
                    context = " ".join(context_parts)
                    label_words = re.findall(
                        r"[\wËÇëç-]+", context
                    )[-24:]
                is_table_row = bool(
                    first_table_row is not None
                    and line_index >= first_table_row
                    and ":" in line[:match.start()]
                )
                claims.append(Claim(
                    value, unit, _fold(" ".join(label_words)), is_table_row
                ))
        return tuple(claims)

    def extract_entities(self, sentence: str) -> tuple[str, ...]:
        found = [*BANK_RE.findall(sentence), *DOCUMENT_RE.findall(sentence)]
        return tuple(dict.fromkeys(found))

    def verify(self, sentence: str, vetted_chunks: Iterable[str]) -> FidelityResult:
        """Require each value/unit and named entity in compatible vetted chunk context."""
        raw_chunks = tuple(chunk for chunk in vetted_chunks if chunk)
        claims = self.extract_claims(sentence)
        entities = self.extract_entities(sentence)
        if not claims and not entities:
            return FidelityResult(True)
        if not raw_chunks:
            return FidelityResult(False, "factual sentence has no cited vetted chunk text", claims)
        evidence_entities = {
            _entity_comparison_key(entity)
            for chunk in raw_chunks
            for entity in self.extract_entities(chunk)
        }
        for entity in entities:
            if _entity_comparison_key(entity) not in evidence_entities:
                return FidelityResult(False, f"entity absent from vetted chunks: {entity}", claims)
        for claim in claims:
            compatible = False
            sentence_label_tokens = _label_tokens(claim.label)
            if not claim.label:
                return FidelityResult(
                    False, "numeric claim lacks a distinguishing label", claims
                )
            for chunk in raw_chunks:
                # Article/regulation identifiers are metadata-like claims. They
                # are verified by exact proximity to a document marker rather
                # than prose-label subset semantics, which cannot bridge OCR
                # titles such as "RREG 62" and natural "Rregullorja Nr. 62".
                if claim.unit == "number" and sentence_label_tokens & {
                    "neni", "nr", "rregullore", "rregullores", "rregulloren",
                }:
                    folded_chunk = _fold(chunk)
                    number_text = format(claim.value, "f").rstrip("0").rstrip(".")
                    reference_pattern = re.compile(
                        rf"(?:rreg|nr|neni).{{0,24}}\b{re.escape(number_text)}\b"
                        rf"|\b{re.escape(number_text)}\b.{{0,24}}(?:rreg|nr|neni)"
                    )
                    if reference_pattern.search(folded_chunk):
                        compatible = True
                        break
                chunk_claims = self.extract_claims(chunk)
                chunk_label_tokens = _label_tokens(chunk)
                sentence_bank_keys = {
                    _entity_comparison_key(entity)
                    for entity in BANK_RE.findall(sentence)
                }
                for evidence in chunk_claims:
                    same_unit = claim.unit == evidence.unit or "number" in {claim.unit, evidence.unit}
                    comparable_sentence_tokens = set(sentence_label_tokens)
                    # Entity identity is verified independently above, so bank
                    # tokens never participate in product/service label matching.
                    for entity in BANK_RE.findall(sentence):
                        comparable_sentence_tokens -= _label_tokens(entity)
                    # Generic claim-frame words (komision, tarif, prej, nje...)
                    # are uninterpretable as service labels; strip them before
                    # any subset comparison.
                    comparable_sentence_tokens -= _GENERIC_CLAIM_TOKENS
                    evidence_label_tokens = _label_tokens(evidence.label)
                    if evidence.table_row:
                        evidence_bank_keys = _folded_bank_keys(evidence.label)
                        entity_compatible = (
                            not sentence_bank_keys
                            or bool(sentence_bank_keys & evidence_bank_keys)
                        )
                        # Table rows carry only the bank label ("Banka Union: 2.00");
                        # the service wording lives in the column header, so
                        # sentence service tokens are checked against the whole
                        # chunk vocabulary (headers + rows) — the exact VALUE and
                        # BANK pair still gates each claim.
                        label_compatible = (
                            entity_compatible
                            and comparable_sentence_tokens <= (
                                evidence_label_tokens | chunk_label_tokens
                            )
                        )
                    else:
                        label_compatible = (
                            comparable_sentence_tokens <= chunk_label_tokens
                        )
                    if claim.value == evidence.value and same_unit and label_compatible:
                        compatible = True
                        break
                if compatible:
                    break
            if not compatible:
                return FidelityResult(False, f"value/unit/label mismatch: {claim.value} {claim.unit}", claims)
        return FidelityResult(True, claims=claims)
