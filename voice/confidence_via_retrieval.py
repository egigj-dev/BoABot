"""Opt-in retrieval confidence signals for ASR diagnostics.

Derived structures (built only when explicitly enabled):
- HypothesisScore records the dense top-hit score for every N-best hypothesis.
- EntityCatalog is rebuilt at runtime from rate_tables.jsonl; source code contains
  no institution-name allowlist.
- Unique aliases are corpus-derived tokens that identify exactly one catalog row.
  Unknown text is never classified as misrecognition or out-of-scope without
  additional evidence.

These mechanisms are independent and default off. Hybrid RRF scores are excluded
because MIN_RELEVANCE_SCORE is calibrated only as a dense cosine threshold.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from retrieve import retrieve
from trust import MIN_RELEVANCE_SCORE


RATE_LINE_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*[-+]?\d", re.MULTILINE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
BANK_WORD_RE = re.compile(r"\bbank[^\W\d_]*\b", re.IGNORECASE | re.UNICODE)
NON_ENTITY_FOLLOWERS = frozenset(
    {"cila", "cilen", "ime", "jone", "ka", "mund", "per", "qe", "te"}
)


@dataclass(frozen=True)
class HypothesisScore:
    hypothesis: str
    top_score: float | None
    top_hit_id: str | None


@dataclass(frozen=True)
class NBestRerankResult:
    enabled: bool
    chosen_hypothesis: str | None
    scores: tuple[HypothesisScore, ...]
    margin: float | None
    uncertain: bool
    reason: str


@dataclass(frozen=True)
class EntityMention:
    text: str
    known: bool
    canonical_name: str | None


@dataclass(frozen=True)
class EntityValidationResult:
    enabled: bool
    mentions: tuple[EntityMention, ...]
    unknown_entity: bool
    reason: str


@dataclass(frozen=True)
class EntityCatalog:
    names: tuple[str, ...]
    unique_aliases: tuple[tuple[str, str], ...]


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )


def rerank_nbest(
    hypotheses: Iterable[str],
    *,
    enabled: bool = False,
    retrieve_fn: Callable[..., list[dict[str, Any]]] = retrieve,
) -> NBestRerankResult:
    """Rank ASR hypotheses by dense top-hit cosine score when explicitly enabled."""
    if not enabled:
        return NBestRerankResult(False, None, (), None, True, "disabled")
    unique_hypotheses = sorted(
        {hypothesis.strip() for hypothesis in hypotheses if hypothesis.strip()},
        key=lambda hypothesis: (_fold(hypothesis), hypothesis),
    )
    scored = []
    for hypothesis in unique_hypotheses:
        hits = retrieve_fn(hypothesis, k=1, mode="dense")
        if hits:
            try:
                top_score = float(hits[0]["score"])
            except (KeyError, TypeError, ValueError):
                top_score = None
            top_hit_id = str(hits[0].get("id")) if hits[0].get("id") is not None else None
        else:
            top_score = None
            top_hit_id = None
        scored.append(HypothesisScore(hypothesis, top_score, top_hit_id))
    ordered = tuple(sorted(
        scored,
        key=lambda item: (
            -(item.top_score if item.top_score is not None else float("-inf")),
            _fold(item.hypothesis),
            item.hypothesis,
        ),
    ))
    if not ordered:
        return NBestRerankResult(True, None, (), None, True, "no_hypotheses")
    best_score = ordered[0].top_score
    second_score = ordered[1].top_score if len(ordered) > 1 else None
    margin = (
        best_score - second_score
        if best_score is not None and second_score is not None
        else None
    )
    uncertain = best_score is None or best_score < MIN_RELEVANCE_SCORE
    return NBestRerankResult(
        True,
        ordered[0].hypothesis,
        ordered,
        margin,
        uncertain,
        "below_min_relevance" if uncertain else "selected",
    )


def load_entity_catalog(rate_tables_path: str | Path | None = None) -> EntityCatalog:
    """Build the institution catalog from the rate corpus at runtime."""
    path = (
        Path(rate_tables_path)
        if rate_tables_path is not None
        else Path(__file__).resolve().parents[1] / "rate_tables.jsonl"
    )
    names = set()
    with path.open(encoding="utf-8") as rate_file:
        for raw_line in rate_file:
            if not raw_line.strip():
                continue
            row = json.loads(raw_line)
            for match in RATE_LINE_RE.finditer(str(row.get("text") or "")):
                label = match.group(1).strip()
                words = WORD_RE.findall(_fold(label))
                if words and words[0].startswith("bank"):
                    names.add(label)

    alias_owners: dict[str, set[str]] = {}
    for name in names:
        words = WORD_RE.findall(_fold(name))
        suffix = " ".join(words[1:])
        if suffix:
            alias_owners.setdefault(suffix, set()).add(name)
        if len(words) > 1 and len(words[1]) >= 3:
            alias_owners.setdefault(words[1], set()).add(name)
    unique_aliases = sorted(
        (
            (alias, next(iter(owners)))
            for alias, owners in alias_owners.items()
            if len(owners) == 1
        ),
        key=lambda item: (-len(item[0]), item[0], _fold(item[1])),
    )
    return EntityCatalog(
        tuple(sorted(names, key=lambda name: (_fold(name), name))),
        tuple(unique_aliases),
    )


def validate_entities(
    transcript: str,
    *,
    enabled: bool = False,
    rate_tables_path: str | Path | None = None,
) -> EntityValidationResult:
    """Flag corpus-unknown institution mentions without inferring their cause."""
    if not enabled:
        return EntityValidationResult(False, (), False, "disabled")
    catalog = load_entity_catalog(rate_tables_path)
    folded = _fold(transcript)
    padded = f" {folded} "
    known_by_name: dict[str, EntityMention] = {}
    for alias, canonical_name in catalog.unique_aliases:
        if f" {alias} " in padded:
            known_by_name[canonical_name] = EntityMention(
                alias, True, canonical_name
            )

    mentions = list(sorted(
        known_by_name.values(),
        key=lambda mention: (_fold(mention.canonical_name or ""), mention.text),
    ))
    unknown_mentions = []
    known_aliases = {alias for alias, _ in catalog.unique_aliases}
    for bank_match in BANK_WORD_RE.finditer(folded):
        tail = folded[bank_match.end():]
        following = WORD_RE.findall(tail)[:3]
        if not following or following[0] in NON_ENTITY_FOLLOWERS:
            continue
        candidate_words = []
        for word in following:
            if word in NON_ENTITY_FOLLOWERS and candidate_words:
                break
            candidate_words.append(word)
        candidate = " ".join(candidate_words)
        if not candidate:
            continue
        if any(alias == candidate or candidate.startswith(alias + " ")
               for alias in known_aliases):
            continue
        unknown_mentions.append(EntityMention(candidate, False, None))
    mentions.extend(sorted(set(unknown_mentions), key=lambda mention: mention.text))
    unknown = bool(unknown_mentions)
    return EntityValidationResult(
        True,
        tuple(mentions),
        unknown,
        "unknown entity" if unknown else "validated",
    )
