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
_COMPARISON_TERMS = ("krahas", "me e ulet", "me te ulet", "me lire")
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


class RateParse(NamedTuple):
    status: Literal["not_rate", "resolved", "unsupported"]
    intent: RateIntent | None
    reason: Literal[
        "unknown_bank", "missing_product", "conflicting_slots",
        "missing_key", "",
    ]


class RowSlots(NamedTuple):
    product: Product | None
    metric: Metric | None
    fee_event: str | None
    value_type: Literal["min", "percent", "max", "value"] | None
    term_months: int | None
    amount_band: Literal["minimum", "maximum"] | None


# Bounded Albanian vocabularies: these are catalog slots, not semantic prompts.
PRODUCT_TERMS = {
    "consumer_credit_unsecured": (
        ("kredi", "kredia", "kredise"),
        ("konsumator", "konsumatore", "konsumtare", "pasiguruar"),
    ),
    "consumer_credit_mortgage": (
        ("kredi",), ("konsumator", "konsumatore"), ("hipotek",),
    ),
    "housing_credit": (("kredi",), ("shtepi", "prona", "hipotekare")),
    "deposit": (("depozit", "depozita", "depozitave"),),
    "debit_card": (("kart", "karte", "karta", "kartes"), ("debit", "debiti")),
    "credit_card": (("kart", "karte", "karta", "kartes"), ("kredit", "krediti")),
}
METRIC_TERMS = {
    "interest_rate": (
        "interes", "interesi", "interesit", "norme", "norma", "normat",
        "nei", "nominale",
    ),
    "fee": ("tarif", "tarifa", "tarifat", "komision", "komisione", "kosto"),
    "penalty": ("penalitet", "penalizues", "vonuar"),
}
FEE_EVENT_TERMS = {
    "administration": ("administrim", "administrimi", "administrimit"),
    "application": ("aplikim", "aplikimi", "aplikimit"),
    "disbursement": ("disbursim", "disbursimi", "disbursimit"),
    "maintenance": ("mirembajtje", "sherbim"),
    "early_repayment": ("shlyerje parakohshme", "parakoheshme"),
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
    (("kredi", "kredia", "kredise", "kredit"), "credit"),
    (("kart", "karte", "karta", "kartes"), "card"),
    (("depozit", "depozita", "depozitave"), "deposit"),
    (("shtepi", "prona", "hipotek"), "housing_credit"),
)

_BANK_WORD_RE = re.compile(r"\bbank(?:a|e|en|es|at)?\b", re.I)
_UNKNOWN_BANK_STOP = frozenset({
    "banke", "banka", "bankat", "bankes", "banken", "cdo", "secila",
    "te", "gjitha", "nga", "ne", "per", "dhe", "e", "shqiperi",
    "shqipari", "me", "nje",
})


def _has_term(text: str, term: str) -> bool:
    if term == "%":
        return "%" in text
    return re.search(rf"\b{re.escape(term)}\w*\b", text) is not None


def _matching_slots(text: str, vocabulary: dict) -> list[str]:
    return [name for name, terms in vocabulary.items()
            if any(_has_term(text, term) for term in terms)]


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
    range_match = re.search(r"(\d+)\s*-\s*(\d+)\s+muaj", item)
    term_match = re.search(r"(?:afat|maturitet)\s+(\d+)\s+muaj", item)
    if range_match:
        term_months = int(range_match.group(2))
    elif term_match:
        term_months = int(term_match.group(1))

    amount_band = None
    if "shum" in item and "minimal" in item:
        amount_band = "minimum"
    elif "shum" in item and "maksimal" in item:
        amount_band = "maximum"
    return RowSlots(product, metric, fee_event, value_type, term_months, amount_band)


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
    resolved: list[dict] = []
    for row in _rate_rows():
        slots = _row_slots(row)
        # product=None (metric-only comparison) and metric=None (product-only
        # comparison or availability) act as wildcards.
        if intent.product is not None and slots.product != intent.product:
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
        bank_lines = _selected_bank_lines(row, intent.banks)
        if not bank_lines:
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


# Catalog declines only when answering would require guessing which bank or
# slot the caller meant. Any other miss is coverage, not ambiguity.
CATALOG_DECLINE_REASONS = frozenset({"unknown_bank", "conflicting_slots"})

_EXTRACT_PRODUCTS = frozenset(PRODUCT_TERMS)
_EXTRACT_METRICS = frozenset(METRIC_TERMS)
_EXTRACT_FAMILIES = frozenset(PRODUCT_FAMILY)
_EXTRACT_FEE_EVENTS = frozenset({
    "early_repayment", "late_payment", "administration",
})
_EXTRACT_VALUE_TYPES = frozenset({"min", "percent", "max", "value"})
_EXTRACT_AMOUNT_BANDS = frozenset({"minimum", "maximum"})
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
    kinds = json.dumps(sorted(_EXTRACT_KINDS), ensure_ascii=False)
    declines = json.dumps(sorted(_EXTRACT_DECLINES), ensure_ascii=False)
    return f"""Ti je nxjerrësi semantik i fushave për tarifat dhe normat bankare.
Kthe VETËM një objekt JSON, pa markdown dhe pa shpjegim. Për një pyetje që nuk
kërkon tarifë, normë, krahasim ose disponueshmëri produkti, kthe
{{"is_rate_ask":false}}. Përndryshe kthe gjithmonë të gjitha fushat e kësaj
skeme: is_rate_ask, kind, bank_scope, banks, product, metric, family,
availability, has_price_qualifier, fee_event, value_type, term_months,
amount_band, decline_reason.

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
- decline_reason: {declines} ose null
- term_months: numër i plotë ose null; availability dhe has_price_qualifier:
  vetëm true ose false.

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
    del question
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
            availability=True,
        ), ""

    if kind != "value_comparison":
        return None, "missing_key"
    if product is None and metric is None:
        return None, "missing_product"
    leaf = bool(fee_event or value_type or term_months is not None or amount_band)
    return RateIntent(
        bank_scope=bank_scope, banks=banks, product=product, metric=metric,
        fee_event=fee_event, value_type=value_type, term_months=term_months,
        amount_band=amount_band, breadth="leaf" if leaf else "product_metric",
    ), ""


def parse_rate_intent(question: str) -> RateParse:
    """Parse a bounded Albanian rate ask and prove its key exists in the corpus."""
    folded_question = fold(question)
    product_matches = _matching_products(folded_question)
    metric_matches = _matching_slots(folded_question, METRIC_TERMS)
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
            availability=True,
        )
        return RateParse("resolved", intent, "")

    # ---- Value/comparison ask ----
    rate_like = bool(metric_matches) or (
        bool(product_matches) and any(term in folded_question for term in _COMPARISON_TERMS)
    )
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
            amount_band=None, breadth="product_metric",
        )
        if not resolve_rate_rows(intent):
            return RateParse("unsupported", intent, "missing_key")
        return RateParse("resolved", intent, "")
    # Metric-only comparison ("krahaso ... per komisione"): no product slot.
    if is_comparison and not product_matches and metric_matches:
        if len(metric_matches) != 1:
            return RateParse("unsupported", None, "conflicting_slots")
        intent = RateIntent(
            bank_scope=bank_scope, banks=banks, product=None,
            metric=metric_matches[0], fee_event=None, value_type=None,
            term_months=None, amount_band=None, breadth="product_metric",
        )
        if not resolve_rate_rows(intent):
            return RateParse("unsupported", intent, "missing_key")
        return RateParse("resolved", intent, "")
    if not product_matches:
        return RateParse("unsupported", None, "missing_product")
    if len(product_matches) != 1:
        return RateParse("unsupported", None, "conflicting_slots")
    if len(metric_matches) != 1:
        return RateParse("unsupported", None, "conflicting_slots")

    events = _matching_slots(folded_question, FEE_EVENT_TERMS)
    if "early_repayment" in events:
        events = [item for item in events if item != "late_payment"]
    value_types = _matching_slots(folded_question, VALUE_TYPE_TERMS)
    if len(events) > 1 or len(value_types) > 1:
        return RateParse("unsupported", None, "conflicting_slots")

    term_months = None
    term_match = re.search(r"(?:afat|maturitet)?\s*(\d+)\s+muaj", folded_question)
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
    )
    if not resolve_rate_rows(intent):
        return RateParse("unsupported", intent, "missing_key")
    return RateParse("resolved", intent, "")


def parse_rate_intent_hybrid(question: str) -> RateParse:
    """Use the lexical fast path, then one validated LLM extraction fallback."""
    lexical = parse_rate_intent(question)
    if lexical.status in ("resolved", "not_rate"):
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
        return RateParse("resolved", intent, "")
    if decline == "not_rate":
        return RateParse("not_rate", None, "")
    if decline not in _EXTRACT_DECLINES:
        return lexical
    return RateParse("unsupported", None, decline)


def structured_rate_hits(intent: RateIntent, k: int = 5) -> list[dict]:
    """Return every exact matching row; ``k`` is ignored for complete families."""
    del k
    if intent.availability:
        return structured_availability_hits(intent)
    hits: list[dict] = []
    for row in resolve_rate_rows(intent):
        slots: RowSlots = row["_row_slots"]
        bank_lines = row["_bank_lines"]
        header = str(row.get("text") or "").splitlines()[0]
        text = "\n".join((header, *bank_lines))
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
    lines_by_bank: dict[str, list[str]] = {bank: [] for bank in intent.banks}
    for hit in hits:
        article = str(hit.get("article") or "")
        for line in str(hit.get("text") or "").splitlines()[1:]:
            match = _BANK_ROW_RE.match(line)
            if not match:
                continue
            bank = match.group(1).strip()
            canonical = next((name for name in intent.banks if fold(name) == fold(bank)), None)
            if canonical is None:
                continue
            value = line[match.end(1) + 1:].strip()
            lines_by_bank[canonical].append(f"- {article}: {value}")
    rendered: list[str] = []
    for bank in intent.banks:
        items = lines_by_bank.get(bank) or []
        if items:
            rendered.extend((f"{bank}:", *items))
    return "\n".join(rendered)


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
