"""The structured seam may generalize only what the user explicitly left broad; it may never generalize what the parser failed to understand."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Generic, Literal, NamedTuple, TypeAlias, TypeVar

from .text_norm import fold
from .trust import PRICE_INTENT, bank_names as trusted_bank_names, issuer_of

_RATE_TABLES_PATH = Path(__file__).resolve().parents[1] / "rate_tables.jsonl"
_BANK_ROW_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*[-+]?\d")
_PRICE_TERMS = (
    *PRICE_INTENT,
    "tarif", "komision", "interes", "penalitet", "norme", "norma", "kosto",
)
_COMPARISON_TERMS = (
    "krahas", "me e mire", "me te mire", "me e ulet", "me te ulet",
    "me lire", "me e lire",
)
_SUPERLATIVE_TERMS = (
    "me te mire", "me e mire", "me te ulet", "me e ulet", "me lire",
    "me e lire",
)
_QUERY_STOPWORDS = frozenset({
    "a", "bank", "banka", "banke", "bankat", "cila", "cilat", "dhe", "e",
    "eshte", "i", "ka", "krahaso", "me", "meje", "ne", "per", "sa", "te",
})
_LABEL_CONNECTORS = frozenset({"e", "i", "shqiperi", "shqiperise", "albania"})


class ComsIntent(NamedTuple):
    """A structured-comparison request and its canonical rate-table bank labels."""

    bank_names: tuple[str, ...]


Product: TypeAlias = Literal[
    "consumer_credit_unsecured", "consumer_credit_mortgage",
    "housing_credit", "deposit", "debit_card", "credit_card",
]
Metric: TypeAlias = Literal["interest_rate", "fee", "penalty"]
BusinessSize: TypeAlias = Literal["small", "medium", "large"]
RateComponent: TypeAlias = Literal["nominal_rate", "nei"]
T = TypeVar("T")


class SlotState(Enum):
    EXPLICIT = "explicit"
    INHERITED = "inherited"
    WILDCARD = "wildcard"
    MISSING = "missing"


@dataclass(frozen=True)
class ResolvedSlot(Generic[T]):
    value: T | None
    state: SlotState


class StructuredIntentStatus(Enum):
    FULL_STRUCTURED_INTENT = "full_structured_intent"
    UNREPRESENTED_SEMANTICS = "unrepresented_semantics"
    INSUFFICIENT_COMPARISON_DIMENSIONS = "insufficient_comparison_dimensions"


class CoverageCertification(NamedTuple):
    """Deterministic proof that a parsed intent covers the material query text."""

    status: StructuredIntentStatus
    consumed_phrases: tuple[str, ...] = ()
    unresolved_qualifiers: tuple[str, ...] = ()
    model_consumed_phrases: tuple[str, ...] = ()
    model_unresolved_qualifiers: tuple[str, ...] = ()


class RateIntent(NamedTuple):
    """Fully typed key used by both routing and exact row resolution."""

    bank_scope: Literal["named", "all"]
    banks: tuple[str, ...]
    product: Product | None
    metric: Metric | None
    fee_event: str | None
    value_type: Literal["min", "percent", "max", "value"] | None
    term_months: int | None
    amount_band: Literal["minimum", "maximum"] | None
    breadth: Literal["leaf", "product_metric"]
    family: str | None = None
    availability: bool = False
    currency: Literal["ALL", "EUR", "USD"] | None = None
    customer_segment: Literal["individual", "business"] | None = None
    wildcard_slots: frozenset[str] = frozenset()
    # Business-rate family (the BoA "Normat nominale dhe NEI për bizneset"
    # table) — a rate-table family, NOT a banking product.
    business_size: BusinessSize | None = None
    rate_component: RateComponent | None = None
    maturity_band: tuple[int, int] | None = None


def _rate_intent_asdict(intent: RateIntent) -> dict:
    """Keep legacy serialized intents stable while exposing populated new slots."""
    values = dict(zip(intent._fields, intent))
    for key in ("currency", "customer_segment", "business_size",
                "rate_component", "maturity_band"):
        if values[key] is None:
            values.pop(key)
    if not values["wildcard_slots"]:
        values.pop("wildcard_slots")
    return values


RateIntent._asdict = _rate_intent_asdict  # type: ignore[method-assign]


class RateParse(NamedTuple):
    status: Literal["not_rate", "resolved", "unsupported"]
    intent: RateIntent | None
    reason: Literal[
        "unknown_bank", "missing_product", "conflicting_slots",
        "missing_key", "unrepresented_semantics",
        "comparison_dimensions_missing", "maturity_band_required", "",
    ]
    coverage: CoverageCertification | None = None


class RowSlots(NamedTuple):
    product: Product | None
    metric: Metric | None
    fee_event: str | None
    value_type: Literal["min", "percent", "max", "value"] | None
    term_months: int | None
    amount_band: Literal["minimum", "maximum"] | None
    business_size: BusinessSize | None = None
    rate_component: RateComponent | None = None
    maturity_band: tuple[int, int] | None = None


# Bounded Albanian vocabularies: these are catalog slots, not semantic prompts.
PRODUCT_TERMS = {
    "consumer_credit_unsecured": (
        ("kredi", "kredia", "kredie", "kredine", "kredise", "kredive"),
        ("konsumator", "konsumatore", "konsumtare", "pasiguruar", "pasiguruara"),
    ),
    "consumer_credit_mortgage": (
        ("kredi", "kredia", "kredie", "kredine", "kredise", "kredive"),
        ("konsumator", "konsumatore"), ("hipotek", "hipoteke", "hipotekes"),
    ),
    "housing_credit": (
        ("kredi", "kredia", "kredie", "kredine", "kredise", "kredive"),
        ("shtepi", "prona", "hipotekare"),
    ),
    "deposit": ((
        "depozit", "depozite", "depozita", "depoziten", "depozites",
        "depozitat", "depozitave",
    ),),
    "debit_card": (
        ("kart", "karte", "karta", "karten", "kartes", "kartat", "kartave"),
        ("debit", "debiti", "debitit"),
    ),
    "credit_card": (("kart", "karte", "karta", "kartes"), ("kredit", "krediti")),
}
METRIC_TERMS = {
    "interest_rate": (
        "interes", "interesi", "interesit", "norme", "norma", "normen",
        "normat", "normave",
        "nei", "nominale",
    ),
    "fee": (
        "tarif", "tarifa", "tarifen", "tarifat", "tarifes", "tarifave",
        "komision", "komisioni", "komisionit", "komisione", "komisionet",
        "komisioneve",
        "kosto", "kostos",
    ),
    "penalty": ("penalitet", "penalizues", "vonuar"),
}
CURRENCY_TERMS = {
    "ALL": ("lek", "leke"),
    "EUR": ("euro", "eur"),
    "USD": ("dollar", "dollare", "usd"),
}
CUSTOMER_SEGMENT_TERMS = {
    "individual": ("individ", "individe", "personal", "person fizik"),
    "business": ("biznes", "biznese", "kompani", "shoqeri", "person juridik"),
}
# Business-rate family vocabulary. The size term is a table-category slot
# (Biznes i vogel / i mesem / i madh), NOT a banking product; "mesatar" is the
# AMBIGUOUS-band trigger (rule 2: CLARIFY, never guess) and is deliberately not
# a size synonym of "i mesem".
BUSINESS_FAMILY = "business_rates"
BUSINESS_SIZE_TERMS: dict[BusinessSize, tuple[str, ...]] = {
    "small": (
        "biznes i vogel", "biznesi i vogel", "biznes te vogel",
        "biznesit te vogel", "biznesit i vogel",
    ),
    "medium": (
        "biznes i mesem", "biznesi i mesem", "biznes te mesem",
        "biznesit te mesem", "biznesit i mesem",
    ),
    "large": (
        "biznes i madh", "biznesi i madh", "biznes te madh",
        "biznesit te madh", "biznesit i madh",
    ),
}
RATE_COMPONENT_TERMS: dict[RateComponent, tuple[str, ...]] = {
    "nominal_rate": ("norma nominale", "normen nominale", "norme nominale",
                     "norme nominal", "norma nominal"),
    "nei": ("nei",),
}
_BUSINESS_SOURCE_MARK = "normat nominale dhe nei per bizneset"
_MATURITY_RANGE_RE = re.compile(r"\b(\d{1,3})\s*-\s*(\d{1,3})\s+(?:muaj|mujore|muajsh)\b")
_BUSINESS_VALUE_LINE_RE = re.compile(r"^[^:\n]+?:\s*([-+]?\d[\d .,']*)$")
FEE_EVENT_TERMS = {
    "administration": ("administrim", "administrimi", "administrimit"),
    "application": ("aplikim", "aplikimi", "aplikimit"),
    "disbursement": ("disbursim", "disbursimi", "disbursimit"),
    "maintenance": ("mirembajtje", "sherbim"),
    "early_repayment": (
        "shlyerje parakohshme", "shlyerje te parakohshme",
        "shlyerje e parakohshme", "parakohshme", "parakoheshme",
    ),
    "late_payment": ("shlyerje vonuar", "kestit", "vones"),
    "issuance": ("leshim", "leshimi", "leshimit"),
    "cash_withdrawal": ("terheqje", "cash", "terminal"),
    "pos_payment": ("pos", "pagesa"),
}
VALUE_TYPE_TERMS = {
    "min": ("min", "minimal", "minimale", "minimum"),
    "percent": ("%", "perqind", "perqindje"),
    "max": ("max", "maksimal", "maksimale", "maksimum"),
}
ALL_BANK_TERMS = (
    "secila banke", "cdo banke", "te gjitha bankat", "nga bankat",
    "banke per banke", "ne shqiperi",
    # Bounded inflection variants present in the evaluated transcripts.
    "secila banka", "cdo banka", "bankat ne shqiperi", "ne shqipari",
)
# ---- Yes/no availability ("a ofrojne X kredi konsumatore?") ----
# Bounded offer verbs; fold() strips diacritics (ofrojne -> ofrojne).
OFFER_VERBS = ("ofroj", "ofron", "ofronte", "ofruan")
# A family maps to every concrete product slot whose corpus rows count as
# "offering" that family. Bare "kredi" means any credit product.
PRODUCT_FAMILY: dict[str, frozenset[Product]] = {
    "credit": frozenset({
        "consumer_credit_unsecured", "consumer_credit_mortgage", "housing_credit",
    }),
    "consumer_credit": frozenset({"consumer_credit_unsecured", "consumer_credit_mortgage"}),
    "housing_credit": frozenset({"housing_credit"}),
    "card": frozenset({"debit_card", "credit_card"}),
    "deposit": frozenset({"deposit"}),
}
_FAMILY_OF: dict[Product, str] = {
    "consumer_credit_unsecured": "consumer_credit",
    "consumer_credit_mortgage": "consumer_credit",
    "housing_credit": "housing_credit",
    "deposit": "deposit",
    "debit_card": "card",
    "credit_card": "card",
}
_BARE_FAMILY_TERMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("kredi", "kredia", "kredie", "kredise", "kredive", "kredit"), "credit"),
    (("kart", "karte", "karta", "kartes"), "card"),
    (("depozit", "depozita", "depozitave"), "deposit"),
    (("shtepi", "prona", "hipotek"), "housing_credit"),
)

_BANK_WORD_RE = re.compile(r"\bbank(?:a|e|en|es|at)?\b", re.I)
_UNKNOWN_BANK_STOP = frozenset({
    "banke", "banka", "bankat", "bankes", "banken", "cdo", "secila",
    "te", "gjitha", "nga", "ne", "per", "dhe", "e", "shqiperi",
    "shqipari", "me", "nje", "tjera", "tjerat", "tjetra", "tjeter",
})
_CERTIFIABLE_BANK_ALIASES = {
    "aib": "Banka Amerikane e Investimeve Shqiperi",
    "bpi": "Banka e Parë e Investimeve Albania",
}

# Closed, precision-oriented forms used only for semantic certification. The
# recall matcher above intentionally remains permissive (`term\w*`), but a
# token is certifiably consumed only by an exact known form or phrase here.
_CERTIFIABLE_OFFER_FORMS = (
    "ofroj", "ofron", "ofrojne", "ofronte", "ofruan", "jep", "japin",
)
_CERTIFIABLE_COMPARISON_FORMS = (
    "krahaso", "krahasim", "krahasimi", "me e mire", "me te mire",
    "me e ulet", "me te ulet", "me lire", "me e lire",
)
_CERTIFIABLE_BREADTH_FORMS = ("te gjitha",)
_CERTIFIABLE_RESIDUE = _QUERY_STOPWORDS | frozenset({
    "bankat", "bankes", "banken", "cfar", "cfare", "cilen", "cilin", "cili",
    "dua", "di", "do", "edhe", "jane", "jo", "ju", "lutem", "ma", "mund", "nje", "po",
    "nga", "nese", "prej", "qe", "rreth", "se", "tek", "trego", "tregoni", "thjesht", "tyre",
    "maturitet", "maturiteti", "maturitetin", "maturitetesh", "maturitete",
    # Discourse/interest verbs (c23 lead "Me interesojn ...").
    "interesoj", "interesojn", "intereson", "interesojne", "interesohem",
    "jane", "eshte",
})
_COVERAGE_TOKEN_RE = re.compile(r"[^\W_]+|%", re.UNICODE)
_CERTIFIABLE_TERM_RE = re.compile(
    r"(?:\b(?:afat|maturitet)\s+)?\b(\d+)"
    r"(?:\s+(?:muaj|muajsh|muajve|mujore?s?|muajshe)"
    r"|\s*-\s*(?:mujore?s?|muajsh))\b"
)


def _has_term(text: str, term: str) -> bool:
    if term == "%":
        return "%" in text
    return re.search(rf"\b{re.escape(term)}\w*\b", text) is not None


def _matching_slots(text: str, vocabulary: dict) -> list[str]:
    return [name for name, terms in vocabulary.items()
            if any(_has_term(text, term) for term in terms)]


def _matching_exact_slots(text: str, vocabulary: dict) -> list[str]:
    return [
        name for name, terms in vocabulary.items()
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in terms)
    ]


def _conservative_value(text: str, vocabulary: dict) -> str | None:
    matches = _matching_exact_slots(text, vocabulary)
    return matches[0] if len(matches) == 1 else None


def _matching_products(text: str) -> list[Product]:
    matches: list[Product] = []
    for product, term_groups in PRODUCT_TERMS.items():
        if all(any(_has_term(text, term) for term in group) for group in term_groups):
            matches.append(product)  # type: ignore[arg-type]
    # "kredi konsumatore me hipoteke" is the mortgage product, not two products.
    if "consumer_credit_mortgage" in matches:
        matches = [item for item in matches if item != "consumer_credit_unsecured"]
    return matches


@lru_cache(maxsize=1)
def _rate_rows() -> tuple[dict, ...]:
    rows = []
    with _RATE_TABLES_PATH.open(encoding="utf-8") as rate_file:
        for index, line in enumerate(rate_file):
            row = json.loads(line)
            row.setdefault("customer_segment", None)
            # Currency-specific pages will carry their own materialized value.
            row.setdefault("currency", None)
            row["_id"] = f"rate_{index:04d}"
            rows.append(row)
    return tuple(rows)


def _business_size_of(folded_question: str) -> BusinessSize | None:
    for size, phrases in BUSINESS_SIZE_TERMS.items():
        if any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded_question)
               for phrase in phrases):
            return size
    return None


def _maturity_band_of(folded_question: str) -> tuple[int, int] | None:
    match = _MATURITY_RANGE_RE.search(folded_question)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


def _rate_component_of(folded_question: str) -> RateComponent | None:
    for component, phrases in RATE_COMPONENT_TERMS.items():
        if any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded_question)
               for phrase in phrases):
            return component
    return None


def _is_business_rate_ask(folded_question: str) -> bool:
    """A business-rate ask names the business segment plus a rate metric.

    The business table is NOT a banking product: bare "biznes" with a rate
    word (normë/NEI) selects the family even without a size adjective, so a
    missing band reliably triggers the band CLARIFY (rule 3).
    """
    if "biznes" not in folded_question:
        return False
    return any(term in folded_question for term in METRIC_TERMS["interest_rate"])


def _source_bank_labels() -> tuple[str, ...]:
    """Return rate-row labels admitted by the canonical trust.bank_names catalog."""
    known = trusted_bank_names()
    labels: dict[str, str] = {}
    for row in _rate_rows():
        for line in str(row.get("text") or "").splitlines()[1:]:
            match = _BANK_ROW_RE.match(line)
            if not match:
                continue
            label = match.group(1).strip()
            folded_label = fold(label)
            if any(re.search(rf"\b{re.escape(name)}\b", folded_label) for name in known):
                labels.setdefault(folded_label, label)
    return tuple(labels.values())


@lru_cache(maxsize=1)
def _bank_aliases() -> tuple[tuple[str, str], ...]:
    """Build unambiguous aliases from trusted tokens and labels in the JSONL."""
    known = trusted_bank_names()
    aliases: dict[str, set[str]] = {}
    for label in _source_bank_labels():
        folded_label = fold(label)
        words = re.findall(r"[^\W_]+", folded_label, flags=re.UNICODE)
        candidates = {folded_label, " ".join(
            word for word in words if word not in _LABEL_CONNECTORS and word != "banka"
        )}
        candidates.update(
            name for name in known
            if re.search(rf"\b{re.escape(name)}\b", folded_label)
        )
        acronym_words = [word for word in words if word not in _LABEL_CONNECTORS]
        if len(acronym_words) >= 3:
            candidates.add("".join(word[0] for word in acronym_words))
        for alias in filter(None, candidates):
            aliases.setdefault(alias, set()).add(label)
    unambiguous = (
        (alias, next(iter(labels)))
        for alias, labels in aliases.items() if len(labels) == 1
    )
    return tuple(sorted(unambiguous, key=lambda item: (-len(item[0]), item[0])))


def _named_banks(folded_question: str) -> tuple[tuple[str, ...], list[tuple[int, int]]]:
    matched: list[str] = []
    spans: list[tuple[int, int]] = []
    for alias, label in _bank_aliases():
        for match in re.finditer(rf"\b{re.escape(alias)}\b", folded_question):
            if label not in matched:
                matched.append(label)
            spans.append(match.span())
    return tuple(matched), spans


def _explicit_unknown_bank(folded_question: str, known_banks: tuple[str, ...]) -> bool:
    """Detect explicit bank names without treating an implicit all-bank ask as unknown."""
    aliases = tuple(alias for alias, _label in _bank_aliases())

    # "Banka/Banken Xyzzy": a bounded phrase after the bank noun must either
    # contain a trusted alias or be an all-bank phrase.
    for match in _BANK_WORD_RE.finditer(folded_question):
        tail = folded_question[match.end():]
        tail = re.split(r"[,;?]|\b(?:per|me|ka|tarif\w*|komision\w*|norm\w*|interes\w*)\b",
                        tail, maxsplit=1)[0].strip()
        words = re.findall(r"[^\W_]+", tail, flags=re.UNICODE)[:5]
        candidates = [word for word in words if word not in _UNKNOWN_BANK_STOP]
        if not candidates:
            continue
        phrase = " ".join(words)
        if not any(re.search(rf"\b{re.escape(alias)}\b", phrase) for alias in aliases):
            return True

    # Comparison lists can omit the word "bankë": "BKT, Credins dhe Xyzzy".
    # Once at least one known bank establishes that this is a bank list, reject
    # every non-empty list element that contains no canonical alias.
    if known_banks and re.search(r"\bkrahas\w*\b", folded_question):
        clause = re.split(r"\bkrahas\w*\b", folded_question, maxsplit=1)[1]
        clause = re.split(
            r"\bper\b|\b(?:tarif|komision|interes|norm|penalitet)\w*\b",
            clause, maxsplit=1,
        )[0]
        for part in re.split(r",|\bdhe\b", clause):
            candidate = part.strip(" .:-")
            if not candidate:
                continue
            if any(re.search(rf"\b{re.escape(alias)}\b", candidate) for alias in aliases):
                continue
            words = [word for word in re.findall(r"[^\W_]+", candidate)
                     if word not in _UNKNOWN_BANK_STOP]
            if words:
                return True
    return False


def _bank_scope(folded_question: str) -> tuple[
        Literal["named", "all"], tuple[str, ...],
        Literal["unknown_bank", "conflicting_slots", ""]]:
    """Return canonical bank scope plus any terminal scope error."""
    named, _spans = _named_banks(folded_question)
    all_scope = any(term in folded_question for term in ALL_BANK_TERMS)
    unknown = _explicit_unknown_bank(folded_question, named)
    if unknown:
        return ("named" if named else "all"), (named or _source_bank_labels()), "unknown_bank"
    if named and all_scope:
        # A named subset and an all-bank selector are contradictory scopes.
        return "named", named, "conflicting_slots"
    if named:
        return "named", named, ""
    return "all", _source_bank_labels(), ""


def _deduplicated(items: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def _model_audit_values(raw: dict | None, key: str) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    values = raw.get(key)
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        return ()
    return _deduplicated(tuple(item.strip() for item in values))


def _slot_certifiable_phrases(intent: RateIntent) -> tuple[str, ...]:
    phrases: list[str] = []
    if intent.product is not None:
        for group in PRODUCT_TERMS[intent.product]:
            phrases.extend(group)
    elif intent.family:
        for terms, family in _BARE_FAMILY_TERMS:
            if family == intent.family:
                phrases.extend(terms)
        for product in PRODUCT_FAMILY.get(intent.family, ()):
            for group in PRODUCT_TERMS[product]:
                phrases.extend(group)

    if intent.metric is not None:
        phrases.extend(METRIC_TERMS[intent.metric])
    if intent.family == BUSINESS_FAMILY:
        if intent.business_size is not None:
            phrases.extend(BUSINESS_SIZE_TERMS[intent.business_size])
        if intent.rate_component is not None:
            phrases.extend(RATE_COMPONENT_TERMS[intent.rate_component])
        # The band literal is a certifiable source phrase ("13-24 muaj").
        if intent.maturity_band is not None:
            phrases.append(f"{intent.maturity_band[0]}-{intent.maturity_band[1]} muaj")
    if intent.currency is not None:
        phrases.extend(CURRENCY_TERMS[intent.currency])
    if intent.customer_segment is not None:
        phrases.extend(CUSTOMER_SEGMENT_TERMS[intent.customer_segment])
    if intent.fee_event is not None:
        phrases.extend(FEE_EVENT_TERMS.get(intent.fee_event, ()))
    if intent.value_type is not None:
        phrases.extend(VALUE_TYPE_TERMS.get(intent.value_type, ()))
    if intent.amount_band == "minimum":
        phrases.extend(("shume minimale", "shumen minimale", "shuma minimale"))
    elif intent.amount_band == "maximum":
        phrases.extend(("shume maksimale", "shumen maksimale", "shuma maksimale"))
    return _deduplicated(tuple(phrases))


def certify_semantic_coverage(
        question: str, intent: RateIntent, *, model_report: dict | None = None,
        ) -> CoverageCertification:
    """Certify material query semantics with exact, closed-form consumption.

    MATCHED remains recall-oriented and may come from `_has_term(term\\w*)`.
    CERTIFIABLY_CONSUMED requires exact known forms and phrases. Model-reported
    coverage is advisory. Deterministic certification controls whether the
    structured seam may assert an answer.
    """
    folded_question = fold(question)
    tokens = tuple(_COVERAGE_TOKEN_RE.finditer(folded_question))
    consumed_indexes: set[int] = set()
    consumed_phrases: list[str] = []

    def consume_span(start: int, end: int, phrase: str) -> None:
        matched = False
        for index, token in enumerate(tokens):
            if token.start() >= start and token.end() <= end:
                consumed_indexes.add(index)
                matched = True
        if matched:
            consumed_phrases.append(phrase)

    def consume_exact(phrases) -> None:
        for phrase in phrases:
            folded_phrase = fold(str(phrase)).strip()
            if not folded_phrase:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(folded_phrase)}(?!\w)")
            for match in pattern.finditer(folded_question):
                consume_span(match.start(), match.end(), match.group(0))

    consume_exact(_slot_certifiable_phrases(intent))
    consume_exact(_CERTIFIABLE_OFFER_FORMS)
    consume_exact(_CERTIFIABLE_COMPARISON_FORMS)
    consume_exact(_CERTIFIABLE_BREADTH_FORMS)
    consume_exact(ALL_BANK_TERMS)

    _named, bank_spans = _named_banks(folded_question)
    for start, end in bank_spans:
        consume_span(start, end, folded_question[start:end])

    for alias, bank in _CERTIFIABLE_BANK_ALIASES.items():
        if bank in intent.banks:
            consume_exact((alias,))

    if intent.term_months is not None:
        consumed_term = False
        for match in _CERTIFIABLE_TERM_RE.finditer(folded_question):
            if int(match.group(1)) == intent.term_months:
                consume_span(match.start(), match.end(), match.group(0))
                consumed_term = True
        # Bare numeric terms are admitted only when the caller-supplied intent
        # already carries that exact term (the elliptical merge is the only
        # parser path that creates such an intent).  This does not teach the
        # ordinary parser to guess what an otherwise unexplained number means.
        if not consumed_term:
            for match in re.finditer(r"\b\d+\b", folded_question):
                if int(match.group(0)) == intent.term_months:
                    consume_span(match.start(), match.end(), match.group(0))

    unresolved = _deduplicated(tuple(
        token.group(0) for index, token in enumerate(tokens)
        if index not in consumed_indexes
        and token.group(0) not in _CERTIFIABLE_RESIDUE
    ))
    status = (
        StructuredIntentStatus.UNREPRESENTED_SEMANTICS if unresolved
        else StructuredIntentStatus.FULL_STRUCTURED_INTENT
    )
    return CoverageCertification(
        status=status,
        consumed_phrases=_deduplicated(consumed_phrases),
        unresolved_qualifiers=unresolved,
        model_consumed_phrases=_model_audit_values(model_report, "consumed_phrases"),
        model_unresolved_qualifiers=_model_audit_values(
            model_report, "unresolved_qualifiers",
        ),
    )


_REQUIRED_COMPARISON_DIMENSIONS = {
    "interest_rate": ("currency", "term_months", "amount_band", "customer_segment"),
    "fee": ("fee_event", "customer_segment", "currency"),
    "penalty": ("fee_event", "customer_segment"),
}


def _superlative_ask(question: str) -> bool:
    folded_question = fold(question)
    return any(term in folded_question for term in _SUPERLATIVE_TERMS)


def _missing_comparison_dimensions(
        intent: RateIntent, rows: list[dict]) -> tuple[str, ...] | None:
    metrics = {row["_row_slots"].metric for row in rows}
    metric = intent.metric
    if metric is None:
        concrete_metrics = {item for item in metrics if item is not None}
        if len(concrete_metrics) != 1:
            return None
        metric = next(iter(concrete_metrics))
    required = _REQUIRED_COMPARISON_DIMENSIONS.get(metric)
    if required is None:
        return None

    missing: list[str] = []
    for dimension in required:
        if dimension == "amount_band":
            bands = {row["_row_slots"].amount_band for row in rows}
            if intent.amount_band is None and len(bands) > 1:
                missing.append(dimension)
        elif getattr(intent, dimension) is None:
            missing.append(dimension)
    return tuple(missing)


def _certified_rate_parse(
        question: str, intent: RateIntent, *, model_report: dict | None = None,
        require_resolution: bool = True,
        ) -> RateParse:
    coverage = certify_semantic_coverage(question, intent, model_report=model_report)
    if coverage.status is StructuredIntentStatus.UNREPRESENTED_SEMANTICS:
        return RateParse(
            "unsupported", intent, "unrepresented_semantics", coverage,
        )
    if intent.availability:
        return RateParse("resolved", intent, "", coverage)

    rows = resolve_rate_rows(intent)
    if not rows and (require_resolution or _superlative_ask(question)):
        return RateParse("unsupported", intent, "missing_key", coverage)
    if not rows:
        return RateParse("resolved", intent, "", coverage)
    if _superlative_ask(question):
        # Product-labeled NEI/credit rows are listable but cannot establish a
        # bank ranking. Keep them on the honest dense fall-through path.
        if not intent.banks or not all(row["_bank_lines"] for row in rows):
            return RateParse("unsupported", intent, "missing_key", coverage)
        missing = _missing_comparison_dimensions(intent, rows)
        if missing is None:
            return RateParse("unsupported", intent, "missing_key", coverage)
        if missing:
            comparison_coverage = coverage._replace(
                status=StructuredIntentStatus.INSUFFICIENT_COMPARISON_DIMENSIONS,
                unresolved_qualifiers=missing,
            )
            return RateParse(
                "unsupported", intent, "comparison_dimensions_missing",
                comparison_coverage,
            )
    return RateParse("resolved", intent, "", coverage)


def _row_slots(row: dict) -> RowSlots:
    """Type one corpus row solely from its trusted source/category/item fields."""
    source = fold(str(row.get("source") or ""))
    category = fold(str(row.get("category") or ""))
    item = fold(str(row.get("item") or ""))

    product: Product | None = None
    if "depozit" in source and "depozit" in category:
        product = "deposit"
    elif "kredi konsumatore me hipotek" in category:
        product = "consumer_credit_mortgage"
    elif "kredi konsumatore te pasiguruara" in category:
        product = "consumer_credit_unsecured"
    elif "kredi per shtepi" in category or "kredi per shtepi/prona" in category:
        product = "housing_credit"
    elif "karte debiti" in category:
        product = "debit_card"
    elif "karte krediti" in category:
        product = "credit_card"

    metric: Metric | None = None
    if "penalitet" in item or "penalizues" in item:
        metric = "penalty"
    elif "normat" in source or "interesit" in source:
        metric = "interest_rate"
    elif "komision" in source:
        metric = "fee"

    events = _matching_slots(item, FEE_EVENT_TERMS)
    fee_event = events[0] if len(events) == 1 else None
    if "shlyerje" in item and "parakoh" in item:
        fee_event = "early_repayment"
    elif ("shlyerje" in item and "vonuar" in item) or "kestit" in item:
        fee_event = "late_payment"

    value_type: Literal["min", "percent", "max", "value"] | None = None
    if metric in ("fee", "penalty"):
        if re.search(r"\bmin$", item):
            value_type = "min"
        elif "%" in str(row.get("item") or "") or "ne %" in item:
            value_type = "percent"
        elif re.search(r"\bmax$", item):
            value_type = "max"
        else:
            value_type = "value"

    term_months = None
    maturity_band: tuple[int, int] | None = None
    range_match = re.search(r"(\d+)\s*-\s*(\d+)\s+muaj", item)
    term_match = _CERTIFIABLE_TERM_RE.search(item)
    if range_match:
        term_months = int(range_match.group(2))
        maturity_band = (int(range_match.group(1)), int(range_match.group(2)))
    elif term_match:
        term_months = int(term_match.group(1))

    amount_band = None
    if "shum" in item and "minimal" in item:
        amount_band = "minimum"
    elif "shum" in item and "maksimal" in item:
        amount_band = "maximum"

    business_size: BusinessSize | None = None
    rate_component: RateComponent | None = None
    if _BUSINESS_SOURCE_MARK in source:
        for size, phrases in BUSINESS_SIZE_TERMS.items():
            if any(_has_term(category, phrase) for phrase in phrases):
                business_size = size
                break
        # The table header names both components; the scraped rows do not
        # attribute individual values to one column, so a row NEVER carries a
        # concrete rate_component (metric asks stay parse-only, never claimed).
    return RowSlots(product, metric, fee_event, value_type, term_months,
                    amount_band, business_size, rate_component, maturity_band)


def _selected_bank_lines(row: dict, banks: tuple[str, ...]) -> list[str]:
    selected = {fold(bank) for bank in banks}
    lines: list[str] = []
    for line in str(row.get("text") or "").splitlines()[1:]:
        match = _BANK_ROW_RE.match(line)
        if match and fold(match.group(1).strip()) in selected:
            lines.append(line)
    return lines


def resolve_rate_rows(intent: RateIntent) -> list[dict]:
    """Resolve a typed key by slot equality and preserve stable corpus order."""
    # Business-rate family: the BoA nominal/NEI business table. It has NO
    # product slot and NO bank lines (values are per-category aggregates);
    # resolution is by segment + size + maturity band (+ metric interest_rate).
    if intent.family == BUSINESS_FAMILY:
        resolved: list[dict] = []
        for row in _rate_rows():
            if str(row.get("customer_segment")) != "business":
                continue
            if _BUSINESS_SOURCE_MARK not in fold(str(row.get("source") or "")):
                continue
            slots = _row_slots(row)
            if slots.metric != "interest_rate":
                continue
            if intent.business_size is not None and slots.business_size != intent.business_size:
                continue
            if intent.maturity_band is not None and slots.maturity_band != intent.maturity_band:
                continue
            # Junk sub-header rows (category repeats, no numeric value) never
            # resolve — they carry no claimable figure.
            if not any(_BUSINESS_VALUE_LINE_RE.match(line)
                       for line in str(row.get("text") or "").splitlines()[1:]):
                continue
            copy = dict(row)
            copy["_bank_lines"] = ()
            copy["_row_slots"] = slots
            resolved.append(copy)
        return resolved

    product_wildcard = "product" in intent.wildcard_slots
    metric_wildcard = "metric" in intent.wildcard_slots
    family_products = PRODUCT_FAMILY.get(intent.family or "")
    if intent.product is None and not product_wildcard and not family_products:
        return []
    if intent.metric is None and not metric_wildcard:
        return []

    resolved: list[dict] = []
    for row in _rate_rows():
        slots = _row_slots(row)
        if intent.product is not None and slots.product != intent.product:
            continue
        if intent.product is None and family_products and slots.product not in family_products:
            continue
        if intent.metric is not None and slots.metric != intent.metric:
            continue
        if intent.fee_event is not None and slots.fee_event != intent.fee_event:
            continue
        if intent.value_type is not None and slots.value_type != intent.value_type:
            continue
        if intent.term_months is not None and slots.term_months != intent.term_months:
            continue
        if intent.amount_band is not None and slots.amount_band != intent.amount_band:
            continue
        if intent.currency is not None and row.get("currency") != intent.currency:
            continue
        if (intent.customer_segment is not None
                and row.get("customer_segment") != intent.customer_segment):
            continue
        bank_lines = _selected_bank_lines(row, intent.banks)
        family_listing = (
            intent.product is None and family_products is not None
            and intent.bank_scope == "all"
        )
        if not bank_lines and not family_listing:
            continue
        copy = dict(row)
        copy["_bank_lines"] = tuple(bank_lines)
        copy["_row_slots"] = slots
        resolved.append(copy)
    return resolved


def resolve_availability(intent: RateIntent) -> dict[str, bool]:
    """Return {canonical_bank_label: offers_family} for an availability ask.

    A bank offers a family if any corpus row whose product belongs to the
    family's product set carries a line for that bank. Purely corpus-driven —
    identical output for a re-issued intent.
    """
    if not intent.availability:
        return {}
    family = intent.family
    products = PRODUCT_FAMILY.get(family or "")
    if not products:
        return {}
    offer: dict[str, bool] = {bank: False for bank in intent.banks}
    for row in _rate_rows():
        slots = _row_slots(row)
        if slots.product not in products:
            continue
        bank_lines = _selected_bank_lines(row, intent.banks)
        for line in bank_lines:
            match = _BANK_ROW_RE.match(line)
            if not match:
                continue
            label = match.group(1).strip()
            canon = next((b for b in intent.banks if fold(b) == fold(label)), None)
            if canon:
                offer[canon] = True
    return offer


def _offer_verbs_present(folded_question: str) -> bool:
    return any(_has_term(folded_question, verb) for verb in OFFER_VERBS)


def _resolve_family(product_matches: list[Product], folded_question: str) -> str | None:
    """Canonical family for an availability ask: from a product match or a bare term."""
    if len(product_matches) == 1:
        family = _FAMILY_OF.get(product_matches[0])
        if family:
            return family
    for terms, family in _BARE_FAMILY_TERMS:
        if any(_has_term(folded_question, term) for term in terms):
            return family
    return None


_ELLIPTICAL_LEAD_RE = re.compile(
    r"^\s*(?:po|edhe|e\s+per|po\s+per|po\s+te)(?:\b|\s)", re.I,
)
_SENTENCE_VERB_RE = re.compile(
    r"\b(?:jane|eshte|ka|ofron|ofrojne|ofroni|ofrojme)\b", re.I,
)
_BARE_TERM_RE = re.compile(r"\b(\d+)\b")


def _elliptical_slot_values(question: str) -> dict[str, object]:
    """Extract only explicit closed-catalog slot values from a continuation."""
    folded_question = fold(question)
    product_matches = _matching_products(folded_question)
    metric_matches = _matching_slots(folded_question, METRIC_TERMS)
    banks, _spans = _named_banks(folded_question)
    term_match = _CERTIFIABLE_TERM_RE.search(folded_question)
    if term_match is None:
        term_match = _BARE_TERM_RE.search(folded_question)

    family = _resolve_family(product_matches, folded_question)
    values: dict[str, object] = {}
    if len(product_matches) == 1:
        values["product"] = product_matches[0]
        values["family"] = _FAMILY_OF.get(product_matches[0])
    elif not product_matches and family is not None:
        values["product"] = None
        values["family"] = family
    if len(metric_matches) == 1:
        values["metric"] = metric_matches[0]
    if banks:
        values["banks"] = banks
    if term_match is not None:
        values["term_months"] = int(term_match.group(1))
    return values


def is_elliptical_rate_turn(question: str) -> bool:
    """Return whether a turn has elliptical syntax plus an explicit rate slot."""
    folded_question = fold(question)
    has_bare_lead = _ELLIPTICAL_LEAD_RE.search(folded_question) is not None
    bare_noun_phrase = (
        _SENTENCE_VERB_RE.search(folded_question) is None
        and not any(_has_term(folded_question, term) for term in _PRICE_TERMS)
    )
    return bool((has_bare_lead or bare_noun_phrase)
                and _elliptical_slot_values(question))


def merge_elliptical(question: str, frame: RateIntent) -> RateIntent | None:
    """Merge explicit continuation slots into a preserved structured frame.

    The ordinary hybrid parser must first reject the turn as ``not_rate``.
    Every newly represented token is then certified against the merged frame;
    inherited fields never turn an unrepresented qualifier into a wildcard.
    """
    if parse_rate_intent_hybrid(question).status != "not_rate":
        return None
    if not is_elliptical_rate_turn(question):
        return None
    values = _elliptical_slot_values(question)
    updates: dict[str, object] = {}
    wildcard_slots = set(frame.wildcard_slots)

    if "product" in values:
        updates["product"] = values["product"]
        updates["family"] = values["family"]
        wildcard_slots.discard("product")
    if "metric" in values:
        updates["metric"] = values["metric"]
        wildcard_slots.discard("metric")
    if "banks" in values:
        updates["bank_scope"] = "named"
        updates["banks"] = values["banks"]
    if "term_months" in values:
        updates["term_months"] = values["term_months"]

    if not updates:
        return None
    updates["wildcard_slots"] = frozenset(wildcard_slots)
    leaf = bool(
        updates.get("term_months", frame.term_months) is not None
        or frame.fee_event or frame.value_type or frame.amount_band
    )
    updates["breadth"] = "leaf" if leaf else "product_metric"
    merged = frame._replace(**updates)
    coverage = certify_semantic_coverage(question, merged)
    if coverage.status is not StructuredIntentStatus.FULL_STRUCTURED_INTENT:
        return None
    return merged


# Catalog declines only when answering would require guessing which bank or
# slot the caller meant. Any other miss is coverage, not ambiguity.
CATALOG_DECLINE_REASONS = frozenset({
    "unknown_bank", "conflicting_slots", "comparison_dimensions_missing",
    "maturity_band_required",
})

_EXTRACT_PRODUCTS = frozenset(PRODUCT_TERMS)
_EXTRACT_METRICS = frozenset(METRIC_TERMS)
_EXTRACT_FAMILIES = frozenset(PRODUCT_FAMILY)
_EXTRACT_FEE_EVENTS = frozenset({
    "early_repayment", "late_payment", "administration",
})
_EXTRACT_VALUE_TYPES = frozenset({"min", "percent", "max", "value"})
_EXTRACT_AMOUNT_BANDS = frozenset({"minimum", "maximum"})
_EXTRACT_CURRENCIES = frozenset(CURRENCY_TERMS)
_EXTRACT_CUSTOMER_SEGMENTS = frozenset(CUSTOMER_SEGMENT_TERMS)
_EXTRACT_KINDS = frozenset({"availability", "value_comparison"})
_EXTRACT_DECLINES = frozenset({
    "unknown_bank", "missing_product", "conflicting_slots", "missing_key",
})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _EXTRACTOR_SYSTEM(catalog: tuple[str, ...]) -> str:
    """Build the Albanian closed-universe rate-slot extraction prompt."""
    banks = json.dumps(list(catalog), ensure_ascii=False)
    products = json.dumps(sorted(_EXTRACT_PRODUCTS), ensure_ascii=False)
    metrics = json.dumps(sorted(_EXTRACT_METRICS), ensure_ascii=False)
    families = json.dumps(sorted(_EXTRACT_FAMILIES), ensure_ascii=False)
    fee_events = json.dumps(sorted(_EXTRACT_FEE_EVENTS), ensure_ascii=False)
    value_types = json.dumps(sorted(_EXTRACT_VALUE_TYPES), ensure_ascii=False)
    amount_bands = json.dumps(sorted(_EXTRACT_AMOUNT_BANDS), ensure_ascii=False)
    currencies = json.dumps(sorted(_EXTRACT_CURRENCIES), ensure_ascii=False)
    customer_segments = json.dumps(
        sorted(_EXTRACT_CUSTOMER_SEGMENTS), ensure_ascii=False,
    )
    kinds = json.dumps(sorted(_EXTRACT_KINDS), ensure_ascii=False)
    declines = json.dumps(sorted(_EXTRACT_DECLINES), ensure_ascii=False)
    return f"""Ti je nxjerrësi semantik i fushave për tarifat dhe normat bankare.
Kthe VETËM një objekt JSON, pa markdown dhe pa shpjegim. Për një pyetje që nuk
kërkon tarifë, normë, krahasim ose disponueshmëri produkti, kthe
{{"is_rate_ask":false}}. Përndryshe kthe gjithmonë të gjitha fushat e kësaj
skeme: is_rate_ask, kind, bank_scope, banks, product, metric, family,
availability, has_price_qualifier, fee_event, value_type, term_months,
amount_band, currency, customer_segment, decline_reason.

Përdor vetëm këto vlera të mbyllura:
- bankat e njohura (etiketat duhen kopjuar saktësisht): {banks}
- kind: {kinds}
- bank_scope: ["named", "all"]
- product: {products} ose null
- metric: {metrics} ose null
- family: {families} ose null
- fee_event: {fee_events} ose null
- value_type: {value_types} ose null
- amount_band: {amount_bands} ose null
- currency: {currencies} ose null
- customer_segment: {customer_segments} ose null
- decline_reason: {declines} ose null
- term_months: numër i plotë ose null; availability dhe has_price_qualifier:
  vetëm true ose false.
- consumed_phrases dhe unresolved_qualifiers mund të shtohen si lista me fraza; janë
  vetëm auditim ndihmës. Certifikimi determinist vendos nëse mbulimi është i
  plotë.

Rregulla semantike:
1. Një emër banke jashtë listës NUK nënkupton të gjitha bankat: vendos
decline_reason="unknown_bank". Fjala banka/bankat pa emër do të thotë
bank_scope="all", banks=[].
2. "në Shqipëri", "nga secila bankë" dhe "çdo bankë" japin scope all vetëm
kur nuk ka bankë me emër. Kur ka BKT ose një bankë tjetër të njohur, ruaj scope
named dhe vetëm etiketat kanonike të bankave të përmendura.
3. Foljet e ofertës si ofroj/ofron/ofrojnë/ofronte/ofruan/japin japin
kind="availability" dhe availability=true. "shërben" nuk është folje oferte.
4. Folje oferte bashkë me interes, normë, komision, krahaso, "me interes të
ulët" ose "më të lartë/ulët" NUK është disponueshmëri e thjeshtë: vendos
kind="value_comparison", has_price_qualifier=true dhe availability=true.
5. Dallo debit_card nga credit_card. "kredi" vetëm ka family="credit";
"kredi konsumatore" family="consumer_credit"; "kredi për shtëpi"
family="housing_credit"; kartat family="card"; depozitat family="deposit".
6. Kur dy produkte të ndryshme qeverisin të njëjtën pyetje, mos zgjidh të
parin: decline_reason="conflicting_slots". Një krahasim vetëm me produkt lejon
metric=null; një krahasim vetëm me komisione lejon product=null.
7. Ruaj cilësuesit e fletës: afati 12 muaj -> term_months=12; komision
administrimi -> fee_event="administration"; shlyerje e parakohshme ose pagesë
e vonuar -> fee_event përkatës; minimum/maksimum/përqindje -> value_type dhe,
kur i referohet shumës, amount_band përkatës.
8. Currency lejohet vetëm nga fjalët: lek/leke/lekë -> "ALL"; euro/eur ->
"EUR"; dollar/dollare/dollarë/usd -> "USD". Customer_segment lejohet vetëm
nga: individ/individë/personal/person fizik -> "individual";
biznes/biznese/kompani/shoqëri/person juridik -> "business". Për çdo formulim
tjetër përdor null; mos hamendëso.

Shembuj të plotë:
Pyetje: cilat banka ofrojne kredi konsumatore?
JSON: {{"is_rate_ask":true,"kind":"availability","bank_scope":"all","banks":[],"product":"consumer_credit_unsecured","metric":null,"family":"consumer_credit","availability":true,"has_price_qualifier":false,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: cilat banka ofrojne kredi me interes te ulet?
JSON: {{"is_rate_ask":true,"kind":"value_comparison","bank_scope":"all","banks":[],"product":null,"metric":"interest_rate","family":"credit","availability":true,"has_price_qualifier":true,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: Tarifat e kartes se debitit te BKT ne Shqiperi?
JSON: {{"is_rate_ask":true,"kind":"value_comparison","bank_scope":"named","banks":["Banka Kombëtare Tregtare"],"product":"debit_card","metric":"fee","family":"card","availability":false,"has_price_qualifier":true,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: a ofron Banka AIB karte krediti?
JSON: {{"is_rate_ask":true,"kind":"availability","bank_scope":"named","banks":["Banka Amerikane e Investimeve Shqiperi"],"product":"credit_card","metric":null,"family":"card","availability":true,"has_price_qualifier":false,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: krahaso BKT, Credins dhe OTP per kredi konsumatore
JSON: {{"is_rate_ask":true,"kind":"value_comparison","bank_scope":"named","banks":["Banka Kombëtare Tregtare","Banka Credins","Banka OTP Albania"],"product":"consumer_credit_unsecured","metric":null,"family":null,"availability":false,"has_price_qualifier":false,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: krahaso BKT dhe Credins per komisione
JSON: {{"is_rate_ask":true,"kind":"value_comparison","bank_scope":"named","banks":["Banka Kombëtare Tregtare","Banka Credins"],"product":null,"metric":"fee","family":null,"availability":false,"has_price_qualifier":true,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":null}}
Pyetje: a ofron Banka Interes kredi?
JSON: {{"is_rate_ask":true,"kind":"availability","bank_scope":"named","banks":[],"product":null,"metric":null,"family":"credit","availability":true,"has_price_qualifier":false,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":"unknown_bank"}}
Pyetje: krahaso BKT dhe Credins per kredi konsumatore dhe kredi per shtepi
JSON: {{"is_rate_ask":true,"kind":"value_comparison","bank_scope":"named","banks":["Banka Kombëtare Tregtare","Banka Credins"],"product":null,"metric":null,"family":null,"availability":false,"has_price_qualifier":true,"fee_event":null,"value_type":null,"term_months":null,"amount_band":null,"decline_reason":"conflicting_slots"}}
"""


def _extract_rate_slots(question: str) -> dict | None:
    """Make one deterministic extractor call; return None on every failure."""
    try:
        from . import rag

        out = rag._post({
            "model": rag.MODEL,
            "messages": [
                {"role": "system", "content": _EXTRACTOR_SYSTEM(_source_bank_labels())},
                {"role": "user", "content": f"pyetja: {question}"},
            ],
            "temperature": 0,
        })
        text = (rag.completion_message(out).get("content") or "").strip()
        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1)
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _validated_enum(raw: dict, key: str, universe: frozenset[str]) -> str | None:
    """Return a closed-universe string value or null for an unknown value."""
    value = raw.get(key)
    return value if isinstance(value, str) and value in universe else None


def _validate_extracted(raw: dict, question: str) -> tuple[RateIntent | None, str]:
    """Validate an extracted candidate and build only closed-universe intents."""
    if not isinstance(raw, dict):
        return None, "missing_key"
    if raw.get("is_rate_ask") is False:
        return None, "not_rate"
    if raw.get("is_rate_ask") is not True:
        return None, "missing_key"

    decline = _validated_enum(raw, "decline_reason", _EXTRACT_DECLINES)
    if decline is not None:
        return None, decline

    catalog = _source_bank_labels()
    raw_banks = raw.get("banks")
    if not isinstance(raw_banks, list) or not all(isinstance(item, str) for item in raw_banks):
        return None, "unknown_bank"
    if any(bank not in catalog for bank in raw_banks):
        return None, "unknown_bank"
    bank_scope = raw.get("bank_scope")
    if bank_scope not in ("named", "all"):
        return None, "conflicting_slots"
    if bank_scope == "named":
        if not raw_banks:
            return None, "unknown_bank"
        banks = tuple(dict.fromkeys(raw_banks))
    else:
        banks = catalog

    kind = _validated_enum(raw, "kind", _EXTRACT_KINDS)
    product = _validated_enum(raw, "product", _EXTRACT_PRODUCTS)
    metric = _validated_enum(raw, "metric", _EXTRACT_METRICS)
    family = _validated_enum(raw, "family", _EXTRACT_FAMILIES)
    fee_event = _validated_enum(raw, "fee_event", _EXTRACT_FEE_EVENTS)
    value_type = _validated_enum(raw, "value_type", _EXTRACT_VALUE_TYPES)
    amount_band = _validated_enum(raw, "amount_band", _EXTRACT_AMOUNT_BANDS)
    folded_question = fold(question)
    currency = _conservative_value(folded_question, CURRENCY_TERMS)
    customer_segment = _conservative_value(
        folded_question, CUSTOMER_SEGMENT_TERMS,
    )
    raw_term = raw.get("term_months")
    term_months = raw_term if (
        isinstance(raw_term, int) and not isinstance(raw_term, bool) and raw_term > 0
    ) else None
    availability = raw.get("availability") is True
    has_price_qualifier = raw.get("has_price_qualifier") is True

    if availability and has_price_qualifier:
        if product is None or metric is None:
            return None, "missing_key"
        kind = "value_comparison"
        availability = False

    if kind == "availability":
        if family is None:
            return None, "missing_product"
        return RateIntent(
            bank_scope=bank_scope, banks=banks, product=product, metric=None,
            fee_event=None, value_type=None, term_months=None,
            amount_band=None, breadth="product_metric", family=family,
            availability=True, currency=currency,
            customer_segment=customer_segment,
        ), ""

    if kind != "value_comparison":
        return None, "missing_key"
    if product is None and metric is None:
        return None, "missing_product"
    is_comparison = any(term in folded_question for term in _COMPARISON_TERMS)
    explicit_breadth = (
        re.search(r"\bte\s+gjitha\b", folded_question) is not None
        and bool(_matching_products(folded_question)
                 or _matching_slots(folded_question, METRIC_TERMS))
    )
    wildcard_slots: set[str] = set()
    if product is None:
        if family is None and (is_comparison or explicit_breadth):
            wildcard_slots.add("product")
        elif family is None:
            return None, "missing_product"
    if metric is None:
        if is_comparison:
            wildcard_slots.add("metric")
        else:
            return None, "missing_key"
    leaf = bool(fee_event or value_type or term_months is not None or amount_band)
    return RateIntent(
        bank_scope=bank_scope, banks=banks, product=product, metric=metric,
        fee_event=fee_event, value_type=value_type, term_months=term_months,
        amount_band=amount_band, breadth="leaf" if leaf else "product_metric",
        family=family if product is None else None,
        currency=currency, customer_segment=customer_segment,
        wildcard_slots=frozenset(wildcard_slots),
    ), ""


def parse_rate_intent(question: str) -> RateParse:
    """Parse a bounded Albanian rate ask and prove its key exists in the corpus."""
    folded_question = fold(question)
    product_matches = _matching_products(folded_question)
    metric_matches = _matching_slots(folded_question, METRIC_TERMS)
    currency = _conservative_value(folded_question, CURRENCY_TERMS)
    customer_segment = _conservative_value(
        folded_question, CUSTOMER_SEGMENT_TERMS,
    )
    if "penalty" in metric_matches:
        # "interes/komision penalizues" names the penalty metric, not two
        # contradictory metrics.
        metric_matches = ["penalty"]

    # ---- Yes/no availability branch (offered/family ask, no metric needed) ----
    if _offer_verbs_present(folded_question):
        bank_scope, banks, bank_error = _bank_scope(folded_question)
        if bank_error:
            return RateParse("unsupported", None, bank_error)
        family = _resolve_family(product_matches, folded_question)
        if family is None:
            return RateParse("unsupported", None, "missing_product")
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=None, metric=None,
            fee_event=None, value_type=None, term_months=None,
            amount_band=None, breadth="product_metric", family=family,
            availability=True, currency=currency,
            customer_segment=customer_segment,
        )
        return _certified_rate_parse(question, intent)

    # ---- Business-rate family branch (BoA "Normat nominale dhe NEI për
    # bizneset"): segment=business + a rate metric, no product slot. -----
    if _is_business_rate_ask(folded_question) and not product_matches:
        return _business_rate_parse(
            question, folded_question,
            currency if currency in ("ALL", "EUR", "USD") else None,
            customer_segment if customer_segment in ("individual", "business") else None,
        )

    # ---- Value/comparison ask ----
    rate_like = bool(metric_matches) or (
        bool(product_matches) and any(term in folded_question for term in _COMPARISON_TERMS)
    ) or bool(product_matches and (currency is not None or customer_segment is not None))
    if not rate_like:
        return RateParse("not_rate", None, "")

    bank_scope, banks, bank_error = _bank_scope(folded_question)
    if bank_error:
        return RateParse("unsupported", None, bank_error)

    is_comparison = any(term in folded_question for term in _COMPARISON_TERMS)
    # Comparison with a product but no price/metric word resolves the whole
    # product family (metric=None == all metrics) rather than aborting.
    # E.g. "Krahaso BKT, Credins dhe OTP per kredi konsumatore" (no komision/
    # interes/norme) must compare that family, not `conflicting_slots`.
    if is_comparison and product_matches and not metric_matches:
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=product_matches[0],
            metric=None, fee_event=None, value_type=None, term_months=None,
            amount_band=None, breadth="product_metric", currency=currency,
            customer_segment=customer_segment,
            wildcard_slots=frozenset({"metric"}),
        )
        return _certified_rate_parse(question, intent)
    # Metric-only comparison ("krahaso ... per komisione"): no product slot.
    if is_comparison and not product_matches and metric_matches:
        if len(metric_matches) != 1:
            return RateParse("unsupported", None, "conflicting_slots")
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=None,
            metric=metric_matches[0], fee_event=None, value_type=None,
            term_months=None, amount_band=None, breadth="product_metric",
            currency=currency, customer_segment=customer_segment,
            wildcard_slots=frozenset({"product"}),
        )
        return _certified_rate_parse(question, intent)
    explicit_breadth = (
        re.search(r"\bte\s+gjitha\b", folded_question) is not None
        and bool(product_matches or metric_matches)
    )
    if explicit_breadth and not product_matches and len(metric_matches) == 1:
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=None,
            metric=metric_matches[0], fee_event=None, value_type=None,
            term_months=None, amount_band=None, breadth="product_metric",
            currency=currency, customer_segment=customer_segment,
            wildcard_slots=frozenset({"product"}),
        )
        return _certified_rate_parse(question, intent)
    if not product_matches:
        family = _resolve_family(product_matches, folded_question)
        if family is None:
            return RateParse("unsupported", None, "missing_product")
        if len(metric_matches) != 1:
            return RateParse("unsupported", None, "conflicting_slots")
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=None,
            metric=metric_matches[0], fee_event=None, value_type=None,
            term_months=None, amount_band=None, breadth="product_metric",
            family=family, currency=currency,
            customer_segment=customer_segment,
        )
        return _certified_rate_parse(question, intent)
    if len(product_matches) != 1:
        return RateParse("unsupported", None, "conflicting_slots")
    if not metric_matches and (currency is not None or customer_segment is not None):
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=product_matches[0],
            metric=None, fee_event=None, value_type=None, term_months=None,
            amount_band=None, breadth="product_metric", currency=currency,
            customer_segment=customer_segment,
        )
        return _certified_rate_parse(question, intent)
    if len(metric_matches) != 1:
        return RateParse("unsupported", None, "conflicting_slots")

    events = _matching_slots(folded_question, FEE_EVENT_TERMS)
    if "early_repayment" in events:
        events = [item for item in events if item != "late_payment"]
    value_types = _matching_slots(folded_question, VALUE_TYPE_TERMS)
    if len(events) > 1 or len(value_types) > 1:
        return RateParse("unsupported", None, "conflicting_slots")

    term_months = None
    term_match = _CERTIFIABLE_TERM_RE.search(folded_question)
    if term_match:
        term_months = int(term_match.group(1))
    amount_band = None
    if re.search(r"\bshum\w*\s+minimal\w*\b", folded_question):
        amount_band = "minimum"
    elif re.search(r"\bshum\w*\s+maksimal\w*\b", folded_question):
        amount_band = "maximum"

    value_type = value_types[0] if value_types else None
    if amount_band is not None and product_matches[0] == "deposit":
        value_type = None
    leaf = bool(events or value_type or term_months is not None or amount_band)
    intent = RateIntent(
        bank_scope=bank_scope,
        banks=banks,
        product=product_matches[0],
        metric=metric_matches[0],
        fee_event=events[0] if events else None,
        value_type=value_type,
        term_months=term_months,
        amount_band=amount_band,
        breadth="leaf" if leaf else "product_metric",
        currency=currency,
        customer_segment=customer_segment,
    )
    return _certified_rate_parse(question, intent)


def _business_rate_parse(
        question: str, folded_question: str,
        currency: Literal["ALL", "EUR", "USD"] | None,
        customer_segment: Literal["individual", "business"] | None,
        ) -> RateParse:
    """Business-rate family intent with the 4 decision rules.

    Rule 1: explicit source band -> deterministic ANSWER.
    Rule 2: "maturitet mesatar" -> CLARIFY (never guess a band).
    Rule 3: missing band -> CLARIFY unless explicit-all breadth.
    Rule 4: explicit "të gjitha" -> deterministic listing of all bands.
    Rule 5 (renderer): kredi is never introduced — the table has no
    product_family=credit; values are rendered as reported, unattributed.
    """
    business_size = _business_size_of(folded_question)
    rate_component = _rate_component_of(folded_question)
    maturity_band = _maturity_band_of(folded_question)
    explicit_all = re.search(r"\bte\s+gjitha\b", folded_question) is not None
    mesatar = "mesatar" in folded_question

    intent = RateIntent(
        bank_scope="all", banks=(), product=None, metric="interest_rate",
        fee_event=None, value_type=None, term_months=None, amount_band=None,
        breadth="product_metric", family=BUSINESS_FAMILY,
        currency=currency, customer_segment=customer_segment or "business",
        business_size=business_size, rate_component=rate_component,
        maturity_band=maturity_band,
    )
    missing_band = maturity_band is None and not explicit_all
    if mesatar or missing_band:
        coverage = CoverageCertification(
            status=StructuredIntentStatus.INSUFFICIENT_COMPARISON_DIMENSIONS,
            unresolved_qualifiers=("maturity_band",),
        )
        return RateParse(
            "unsupported", intent, "maturity_band_required", coverage,
        )
    return _certified_rate_parse(question, intent)


def parse_rate_intent_hybrid(question: str) -> RateParse:
    """Use the lexical fast path, then one validated LLM extraction fallback."""
    lexical = parse_rate_intent(question)
    if lexical.status in ("resolved", "not_rate"):
        return lexical
    if lexical.reason == "maturity_band_required":
        # Business-rate CLARIFY is terminal: the extractor's closed universe
        # has no business-rate family and would misread it as missing_product.
        return lexical

    # Import the serving gate lazily so this module remains independently
    # importable and the comparison/callcenter dependency does not become cyclic.
    from .callcenter import _structured_rate_enabled

    if not _structured_rate_enabled():
        return lexical
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return lexical
    raw = _extract_rate_slots(question)
    if raw is None or not isinstance(raw, dict):
        return lexical
    intent, decline = _validate_extracted(raw, question)
    if intent is not None:
        return _certified_rate_parse(
            question, intent, model_report=raw, require_resolution=False,
        )
    if decline == "not_rate":
        return RateParse("not_rate", None, "")
    if decline not in _EXTRACT_DECLINES:
        return lexical
    return RateParse("unsupported", None, decline)  # type: ignore[arg-type]  # decline value is validated against the closed reason set


def structured_rate_hits(intent: RateIntent, k: int = 5) -> list[dict]:
    """Return every exact matching row; ``k`` is ignored for complete families."""
    del k
    if intent.availability:
        return structured_availability_hits(intent)
    hits: list[dict] = []
    for row in resolve_rate_rows(intent):
        slots: RowSlots = row["_row_slots"]
        bank_lines = row["_bank_lines"]
        source_lines = str(row.get("text") or "").splitlines()
        header = source_lines[0]
        value_lines = bank_lines or tuple(source_lines[1:])
        text = "\n".join((header, *value_lines))
        hit_id = str(row["_id"])
        hits.append({
            "id": hit_id,
            "text": text,
            "doc": str(row.get("source") or "Tabela e tarifave të bankave"),
            "article": " — ".join(filter(None, (
                str(row.get("category") or ""), str(row.get("item") or ""),
            ))),
            "url": str(row.get("url") or ""),
            "issuer": issuer_of(hit_id, text),
            "retrieval_source": "structured_rate",
            "rate_resolution": intent._asdict(),
            "rate_row_slots": slots._asdict(),
        })
    return hits


def structured_availability_hits(intent: RateIntent) -> list[dict]:
    """One deterministic hit per requested bank for a yes/no availability ask."""
    offers = resolve_availability(intent)
    family = intent.family or ""
    hits: list[dict] = []
    for index, bank in enumerate(intent.banks):
        offers_family = bool(offers.get(bank, False))
        hit_id = f"avail_{intent.family}_{index:03d}"
        text = f"{bank}\n{family}: {'PO' if offers_family else 'JO'}"
        hits.append({
            "id": hit_id,
            "text": text,
            "doc": family,
            "article": family,
            "url": "",
            "issuer": issuer_of(hit_id, text),
            "retrieval_source": "structured_rate",
            "rate_resolution": intent._asdict(),
            "rate_row_slots": {"product": None, "offers_family": offers_family},
        })
    return hits


# Deterministic Albanian family labels for yes/no availability answers.
_FAMILY_LABELS = {
    "credit": "kredi",
    "consumer_credit": "kredi konsumatore",
    "housing_credit": "kredi për shtëpi",
    "card": "kartë",
    "deposit": "depozitë",
}


def render_availability_answer(intent: RateIntent) -> str:
    """Render a yes/no per-bank availability verdict straight from the corpus."""
    offers = resolve_availability(intent)
    family_label = _FAMILY_LABELS.get(intent.family or "", intent.family or "")
    lines: list[str] = []
    for bank in intent.banks:
        offer = offers.get(bank, False)
        if offer:
            lines.append(f"{bank}: ofron {family_label}.")
        else:
            lines.append(f"{bank}: nuk ka të dhëna për {family_label}.")
    return "\n".join(lines)


def render_rate_answer(intent: RateIntent, hits: list[dict]) -> str:
    """Render exact source labels and values without inferring any unit."""
    if intent.availability:
        return render_availability_answer(intent)
    if intent.family == BUSINESS_FAMILY:
        return _render_business_rate_answer(intent, hits)
    lines_by_bank: dict[str, list[str]] = {bank: [] for bank in intent.banks}
    product_lines: list[str] = []
    for hit in hits:
        article = str(hit.get("article") or "")
        for line in str(hit.get("text") or "").splitlines()[1:]:
            match = _BANK_ROW_RE.match(line)
            if not match:
                continue
            bank = match.group(1).strip()
            canonical = next((name for name in intent.banks if fold(name) == fold(bank)), None)
            value = line[match.end(1) + 1:].strip()
            if canonical is None:
                product_lines.append(f"- {article}: {value}")
                continue
            lines_by_bank[canonical].append(f"- {article}: {value}")
    rendered: list[str] = []
    for bank in intent.banks:
        items = lines_by_bank.get(bank) or []
        if items:
            rendered.extend((f"{bank}:", *items))
    rendered.extend(product_lines)
    return "\n".join(rendered)


def _render_business_rate_answer(intent: RateIntent, hits: list[dict]) -> str:
    """Render the business nominal/NEI table as reported (rule 5: no kredi).

    Every value is shown as the table reports it — the scraped rows do not
    attribute values to the nominal vs NEI column, so the renderer NEVER labels
    a value nominal/NEI (matching the C4 no-fold decision). Band ordering is
    deterministic by band start. Values dedupe (a repeated identical figure
    across scraped rows adds no information).
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for hit in hits:
        article = str(hit.get("article") or "")
        category, _sep, item = article.partition(" — ")
        for line in str(hit.get("text") or "").splitlines()[1:]:
            match = _BUSINESS_VALUE_LINE_RE.match(line.strip())
            if not match:
                continue
            value = match.group(1).strip()
            key = (category, item)
            values = grouped.setdefault(key, [])
            if value not in values:
                values.append(value)
            if key not in order:
                order.append(key)
    if not order:
        return ""
    label_fix = {" vogel": " vogël", " mesem": " mesëm"}
    lines: list[str] = []
    for key in order:
        category, item = key
        label = category
        for src, dst in label_fix.items():
            label = label.replace(src, dst)
        lines.append(f"{label} — {item}: {', '.join(grouped[key])}")
    return "\n".join(lines)


def comparison_intent(question: str) -> ComsIntent | None:
    """Detect a named-bank tariff, fee, interest, or penalty comparison ask."""
    folded_question = fold(question)
    matched: list[str] = []
    for alias, label in _bank_aliases():
        if re.search(rf"\b{re.escape(alias)}\b", folded_question) and label not in matched:
            matched.append(label)
    has_price_term = any(term in folded_question for term in _PRICE_TERMS)
    compares_multiple = (
        len(matched) >= 2
        and any(term in folded_question for term in _COMPARISON_TERMS)
    )
    return ComsIntent(tuple(matched)) if matched and (has_price_term or compares_multiple) else None


def _legacy_query_terms(query: str, selected_banks: tuple[str, ...]) -> set[str]:
    """[SUPERSEDED] Free-token terms retained for branch-history compatibility."""
    terms = set(re.findall(r"[^\W_]+", fold(query), flags=re.UNICODE))
    for bank in selected_banks:
        terms.difference_update(re.findall(r"[^\W_]+", fold(bank), flags=re.UNICODE))
    return {
        term for term in terms
        if len(term) >= 3 and term not in _QUERY_STOPWORDS
        and not any(anchor in term for anchor in ("tarif", "komision", "interes"))
    }


def _legacy_query_rate_tables(query: str, bank_names, k: int = 5) -> list[dict]:
    """[SUPERSEDED] Ranked resolver retained, but no longer used for serving."""
    aliases = dict(_bank_aliases())
    selected = tuple(dict.fromkeys(
        aliases.get(fold(str(name)))
        for name in bank_names if str(name).strip() and aliases.get(fold(str(name)))
    ))
    if not selected or k <= 0:
        return []
    selected_folded = {fold(name) for name in selected}
    terms = _legacy_query_terms(query, selected)
    ranked: list[tuple[int, int, int, dict]] = []
    for index, row in enumerate(_rate_rows()):
        lines = str(row.get("text") or "").splitlines()
        if not lines:
            continue
        bank_lines = []
        for line in lines[1:]:
            match = _BANK_ROW_RE.match(line)
            if match and fold(match.group(1).strip()) in selected_folded:
                bank_lines.append(line)
        if not bank_lines:
            continue
        folded_source = fold(str(row.get("source") or ""))
        folded_category = fold(str(row.get("category") or ""))
        folded_item = fold(str(row.get("item") or ""))
        score = sum(
            (term in folded_source)
            + 2 * (term in folded_category)
            + 3 * (term in folded_item)
            for term in terms
        )
        hit_text = "\n".join((lines[0], *bank_lines))
        hit_id = str(row["_id"])
        hit = {
            "id": hit_id,
            "text": hit_text,
            "doc": str(row.get("source") or "Tabela e tarifave të bankave"),
            "article": " — ".join(filter(None, (
                str(row.get("category") or ""), str(row.get("item") or ""),
            ))),
            "url": str(row.get("url") or ""),
            "issuer": issuer_of(hit_id, hit_text),
            "retrieval_source": "structured_rate",
        }
        ranked.append((score, len(bank_lines), -index, hit))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [hit for _score, _coverage, _index, hit in ranked[:k]]


def query_rate_tables(query: str, bank_names, k: int = 5) -> list[dict]:
    """Backward-compatible wrapper over typed slot-equality resolution."""
    if k <= 0:
        return []
    parsed = parse_rate_intent(query)
    if parsed.status != "resolved" or parsed.intent is None:
        return []
    aliases = dict(_bank_aliases())
    selected = tuple(dict.fromkeys(
        aliases.get(fold(str(name)))
        for name in bank_names if str(name).strip() and aliases.get(fold(str(name)))
    ))
    if not selected:
        return []
    intent = parsed.intent._replace(bank_scope="named", banks=selected)
    # Compatibility callers historically requested a top-k slice. Serving uses
    # structured_rate_hits() directly and therefore never truncates a family.
    return structured_rate_hits(intent, k=k)[:k]
