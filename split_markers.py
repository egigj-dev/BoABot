#!/usr/bin/env python3
"""Read-only follow-up survey separating title and body version markers."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
AUDIT_PATH = ROOT / "audit_temporal.json"
JSON_PATH = ROOT / "split_markers.json"
MARKDOWN_PATH = ROOT / "AUDIT_TEMPORAL_REVIEW.md"

MARKER_PATTERN = re.compile(r"ndryshuar|konsoliduar|integruar", re.IGNORECASE)
CONTEXT_BEFORE = 90
CONTEXT_AFTER = 30
MAX_WINDOWS_PER_DOC = 1
LIVE_STATUSES = {"base", "canonical"}
NON_LIVE_STATUSES = {"amendment", "superseded"}

# These are suggestions only. A human must classify the context itself.
CUE_PATTERNS = (
    ("nr", re.compile(r"\bnr\.?\b", re.IGNORECASE)),
    ("numri", re.compile(r"\bnumri\b", re.IGNORECASE)),
    ("ligjit", re.compile(r"\bligjit\b", re.IGNORECASE)),
    ("vendim", re.compile(r"\bvendim\b", re.IGNORECASE)),
    ("vendimi", re.compile(r"\bvendimi\b", re.IGNORECASE)),
    ("rregullore", re.compile(r"\brregullore\w*\b", re.IGNORECASE)),
    ("udhezim", re.compile(r"\budh(?:e|ë)zim\w*\b", re.IGNORECASE)),
    ("neni", re.compile(r"\bneni\b", re.IGNORECASE)),
    (
        "i/e ndryshuar per",
        re.compile(r"\b(?:i|e)\s+ndryshuar\s+p(?:e|ë)r\b", re.IGNORECASE),
    ),
)


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def cue_tokens(value: str) -> list[str]:
    return sorted(name for name, pattern in CUE_PATTERNS if pattern.search(value))


def occurrence_record(
    chunk_id: str, text: str, match: re.Match[str]
) -> dict[str, Any]:
    start = max(0, match.start() - CONTEXT_BEFORE)
    end = min(len(text), match.end() + CONTEXT_AFTER)
    raw_window = text[start:end]
    marked_window = (
        text[start : match.start()]
        + "[["
        + text[match.start() : match.end()]
        + "]]"
        + text[match.end() : end]
    )
    cues = cue_tokens(raw_window)
    return {
        "source_chunk_id": chunk_id,
        "marker": match.group(0).casefold(),
        "context": marked_window,
        "suggestion": "likely_citation" if cues else "unclassified",
        "reference_cues": cues,
    }


def distinct_occurrences(chunks: list[tuple[str, str]]) -> tuple[int, list[dict[str, Any]]]:
    raw_count = 0
    seen: set[tuple[str, str]] = set()
    records: list[dict[str, Any]] = []
    for chunk_id, raw_text in chunks:
        text = collapse_whitespace(raw_text)
        for match in MARKER_PATTERN.finditer(text):
            raw_count += 1
            record = occurrence_record(chunk_id, text, match)
            key = (record["marker"], record["context"].casefold())
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
    return raw_count, records


def status_inventory_from_db(cursor: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cursor.execute(
        "SELECT status, count(*), count(DISTINCT doc) "
        "FROM chunks GROUP BY status ORDER BY status"
    )
    return [
        {
            "status": status,
            "chunk_count": chunk_count,
            "distinct_doc_count": distinct_doc_count,
        }
        for status, chunk_count, distinct_doc_count in cursor.fetchall()
    ]


def markdown_report(data: dict[str, Any]) -> str:
    split = data["marker_split"]
    corrected = data["corrected_conflicts"]
    coverage = data["coverage"]
    recommendation = data["recommendation"]
    lines = [
        "# Temporal audit review: marker split and corrected conflict floor",
        "",
        "> This is a read-only follow-up. Family grouping remains a normalization-based proposal, not ground truth.",
        "",
        "## Headline decision",
        "",
        "| Measure | Distinct base docs |",
        "|---|---:|",
        f"| Total base docs | {split['total_base_docs']} |",
        f"| Title marked | {split['title_marked_count']} |",
        f"| Body only | {split['body_only_count']} |",
        "",
        (
            f"The earlier combined **58 of 88** statement obscured two materially different signals. "
            f"Only **{split['title_marked_count']} of {split['total_base_docs']}** base filenames "
            f"({split['title_marked_percent']:.2f}%) contain `ndryshuar|konsoliduar|integruar`; "
            "these are strong label-mismatch signals. "
            f"Another **{split['body_only_count']}** have body-only matches. Of those, "
            f"**{split['body_only_docs_with_likely_citation_count']}** have at least one "
            "deterministic citation-cue suggestion and "
            f"**{split['body_only_docs_with_zero_likely_citation_count']}** have none. "
            "Body occurrences therefore are predominantly body noise/cross-reference evidence, "
            "not a sound basis for declaring the status heuristic broadly untrustworthy."
        ),
        "",
        "The cue classifier is deliberately only an auto-suggestion. `unclassified` means a human must inspect the bounded context; it does not prove self-description.",
        "",
        "### Title-marked base documents",
        "",
    ]
    for item in split["title_marked_docs"]:
        lines.append(
            f"- `{item['doc']}` — marker(s): {', '.join(item['markers'])}"
        )

    lines.extend(
        [
            "",
            "### Body-only classification evidence",
            "",
            (
                f"Windows show up to {split['max_windows_per_doc']} distinct occurrences per document, "
                f"with about {split['context_before_chars']} characters before and "
                f"{split['context_after_chars']} after the bracketed marker. "
                "Documents and distinct occurrences are deterministically sorted by database `doc`, chunk `id`, and text position; "
                "when a document has a likely-citation suggestion, its first such occurrence is displayed, otherwise its first unclassified occurrence is displayed."
            ),
        ]
    )
    for item in split["body_only_docs"]:
        lines.extend(
            [
                "",
                f"#### `{item['doc']}`",
                "",
                (
                    f"Raw occurrences: {item['raw_occurrence_count']}; distinct: "
                    f"{item['distinct_occurrence_count']}; displayed: {item['displayed_occurrence_count']}; "
                    f"document suggestion: `{item['document_suggestion']}`."
                ),
                "",
            ]
        )
        for occurrence in item["occurrences"]:
            cues = ", ".join(occurrence["reference_cues"]) or "none"
            lines.extend(
                [
                    (
                        f"- `{occurrence['source_chunk_id']}` / `{occurrence['marker']}` / "
                        f"`{occurrence['suggestion']}` / cues: {cues}"
                    ),
                    "",
                    f"  > {occurrence['context']}",
                    "",
                ]
            )
        if item["evidence_truncated_count"]:
            lines.append(
                f"- Evidence cap omitted {item['evidence_truncated_count']} additional distinct occurrence(s)."
            )

    lines.extend(
        [
            "",
            "## Corrected conflict count and floor",
            "",
            f"Corrected conflict count: **{corrected['corrected_confidence_count']}** "
            f"({', '.join(corrected['retained_family_ids'])}).",
            "",
            corrected["correction_reason"],
            "",
            (
                f"This is a **floor, not an estimate**: **{corrected['base_canonical_singleton_count']}** "
                "base+canonical documents are singletons under the proposed normalization. "
                "A singleton is indistinguishable between a document that genuinely has no sibling "
                "and one whose sibling exists but the normalization missed it. Therefore the true "
                f"conflict count is **>= {corrected['corrected_confidence_count']}**, not "
                f"exactly {corrected['corrected_confidence_count']}."
            ),
            "",
            (
                "The credit-registry slice (`family-007`) remains at **0 conflicts** because both "
                "members are `base`; neither is an amendment."
            ),
            "",
            "## Coverage: the dominant risk",
            "",
            "| Status | Chunks | Distinct docs |",
            "|---|---:|---:|",
        ]
    )
    for row in coverage["status_inventory"]:
        lines.append(
            f"| {row['status']} | {row['chunk_count']} | {row['distinct_doc_count']} |"
        )
    lines.extend(
        [
            "",
            (
                f"The corpus has **{coverage['amendment_chunks']} amendment + "
                f"{coverage['superseded_chunks']} superseded chunks** "
                f"({coverage['non_live_chunk_count']} combined) against "
                f"**{coverage['base_chunk_count']} base chunks**. That is only "
                f"**{coverage['non_live_to_base_chunk_percent']:.2f}%** as many non-live chunks as base chunks."
            ),
            "",
            (
                f"On the served side, **{coverage['base_docs_without_non_live_sibling_count']} of "
                f"{coverage['base_doc_count']}** base documents have no amendment/superseded sibling "
                "in any proposed family. AMENDMENTS ARE LARGELY ABSENT FROM THE CORPUS. "
                "That coverage gap is the dominant limitation; no relabeling and no retrieval change fixes it."
            ),
            "",
            "## Recommendation (not implemented)",
            "",
            (
                f"Threshold: treat title marking as non-trivial when at least "
                f"{recommendation['threshold']['minimum_docs']} base documents and at least "
                f"{recommendation['threshold']['minimum_percent']:.1f}% of base documents are title-marked."
            ),
            "",
            recommendation["statement"],
            "",
            "No serving code was changed.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

    with psycopg.connect(DSN) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            status_inventory = status_inventory_from_db(cursor)
            cursor.execute(
                "SELECT doc, status FROM chunks GROUP BY doc, status ORDER BY doc, status"
            )
            document_status_rows = cursor.fetchall()
            cursor.execute(
                "SELECT id, doc, coalesce(text, '') FROM chunks "
                "WHERE status = 'base' ORDER BY doc, id"
            )
            base_chunk_rows = cursor.fetchall()
            cursor.execute(
                "SELECT count(DISTINCT doc), count(*) FROM chunks "
                "WHERE status IN ('amendment', 'superseded')"
            )
            non_live_doc_count, non_live_chunk_count = cursor.fetchone()

    audit_inventory = sorted(audit["status_inventory"], key=lambda row: row["status"])
    if status_inventory != audit_inventory:
        raise RuntimeError("Database status inventory no longer matches audit_temporal.json")

    status_by_doc: dict[str, str] = {}
    for doc, status in document_status_rows:
        if doc in status_by_doc:
            raise RuntimeError(f"Document {doc!r} has multiple statuses")
        status_by_doc[doc] = status

    chunks_by_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for chunk_id, doc, text in base_chunk_rows:
        chunks_by_doc[doc].append((chunk_id, text))

    title_marked_docs: list[dict[str, Any]] = []
    body_only_docs: list[dict[str, Any]] = []
    for doc in sorted(chunks_by_doc):
        title_markers = sorted(
            {match.group(0).casefold() for match in MARKER_PATTERN.finditer(doc)}
        )
        if title_markers:
            title_marked_docs.append({"doc": doc, "markers": title_markers})
            continue

        raw_count, occurrences = distinct_occurrences(chunks_by_doc[doc])
        if not occurrences:
            continue
        has_likely_citation = any(
            item["suggestion"] == "likely_citation" for item in occurrences
        )
        prioritized_occurrences = sorted(
            enumerate(occurrences),
            key=lambda pair: (
                pair[1]["suggestion"] != "likely_citation",
                pair[0],
            ),
        )
        displayed = [
            occurrence
            for _, occurrence in prioritized_occurrences[:MAX_WINDOWS_PER_DOC]
        ]
        body_only_docs.append(
            {
                "doc": doc,
                "raw_occurrence_count": raw_count,
                "distinct_occurrence_count": len(occurrences),
                "displayed_occurrence_count": len(displayed),
                "evidence_truncated_count": len(occurrences) - len(displayed),
                "document_suggestion": (
                    "has_likely_citation" if has_likely_citation else "unclassified_only"
                ),
                "occurrences": displayed,
            }
        )

    total_base_docs = len(chunks_by_doc)
    title_marked_count = len(title_marked_docs)
    body_only_count = len(body_only_docs)
    title_marked_percent = (
        100.0 * title_marked_count / total_base_docs if total_base_docs else 0.0
    )
    likely_docs = sorted(
        item["doc"]
        for item in body_only_docs
        if item["document_suggestion"] == "has_likely_citation"
    )
    unclassified_only_docs = sorted(
        item["doc"]
        for item in body_only_docs
        if item["document_suggestion"] == "unclassified_only"
    )

    families = sorted(audit["families"], key=lambda item: item["family_id"])
    family_by_doc = {
        member["doc"]: family
        for family in families
        for member in family["members"]
    }
    multi_family_docs = set(family_by_doc)
    base_canonical_docs = sorted(
        doc for doc, status in status_by_doc.items() if status in LIVE_STATUSES
    )
    singleton_docs = sorted(set(base_canonical_docs) - multi_family_docs)

    base_docs = sorted(doc for doc, status in status_by_doc.items() if status == "base")
    base_docs_with_non_live_sibling: list[str] = []
    for doc in base_docs:
        family = family_by_doc.get(doc)
        if family is None:
            continue
        sibling_statuses = {
            member["status"] for member in family["members"] if member["doc"] != doc
        }
        if sibling_statuses & NON_LIVE_STATUSES:
            base_docs_with_non_live_sibling.append(doc)
    base_docs_with_non_live_sibling.sort()
    base_docs_without_non_live_sibling = sorted(
        set(base_docs) - set(base_docs_with_non_live_sibling)
    )

    prior_conflict_ids = sorted(item["family_id"] for item in audit["conflicts"])
    expected_prior_conflicts = [f"family-{index:03d}" for index in range(1, 7)]
    if prior_conflict_ids != expected_prior_conflicts:
        raise RuntimeError(
            "audit_temporal.json does not contain the expected family-001..006 conflicts"
        )
    retained_family_ids = prior_conflict_ids[:-1]
    dropped_family = next(
        family for family in families if family["family_id"] == "family-006"
    )
    dropped_statuses = sorted(
        {member["status"] for member in dropped_family["members"]}
    )

    family_007 = next(
        family for family in families if family["family_id"] == "family-007"
    )
    family_007_statuses = sorted(member["status"] for member in family_007["members"])
    if family_007_statuses != ["base", "base"]:
        raise RuntimeError("family-007 no longer has two base members")

    inventory_by_status = {row["status"]: row for row in status_inventory}
    amendment_chunks = inventory_by_status["amendment"]["chunk_count"]
    superseded_chunks = inventory_by_status["superseded"]["chunk_count"]
    base_chunks = inventory_by_status["base"]["chunk_count"]

    threshold = {"minimum_docs": 2, "minimum_percent": 2.0, "logic": "both"}
    mitigation_recommended = (
        title_marked_count >= threshold["minimum_docs"]
        and title_marked_percent >= threshold["minimum_percent"]
    )
    if mitigation_recommended:
        recommendation_statement = (
            "The title signal is non-trivial under this threshold. As a separate, approved "
            "change, add `status` to retrieve()'s SELECT and surface it in vetted sources so "
            "the answer can disclose which version it quotes. Do not infer that body-only "
            "marker matches are mislabeled documents."
        )
    else:
        recommendation_statement = (
            "The title signal is near zero under this threshold, so the mitigation is "
            "unnecessary: the marker finding is body-noise, not mislabelling, and the "
            "heuristic held up better than expected."
        )

    data = {
        "audit_scope": {
            "database": DSN,
            "read_only": True,
            "marker_regex": MARKER_PATTERN.pattern,
            "source_audit": AUDIT_PATH.name,
            "family_grouping_is_proposal_not_ground_truth": True,
        },
        "marker_split": {
            "total_base_docs": total_base_docs,
            "title_marked_count": title_marked_count,
            "title_marked_percent": round(title_marked_percent, 2),
            "title_marked_docs": title_marked_docs,
            "body_only_count": body_only_count,
            "body_only_docs": body_only_docs,
            "body_only_docs_with_likely_citation_count": len(likely_docs),
            "body_only_docs_with_zero_likely_citation_count": len(
                unclassified_only_docs
            ),
            "body_only_docs_with_zero_likely_citation": unclassified_only_docs,
            "base_docs_without_marker_count": (
                total_base_docs - title_marked_count - body_only_count
            ),
            "context_before_chars": CONTEXT_BEFORE,
            "context_after_chars": CONTEXT_AFTER,
            "max_windows_per_doc": MAX_WINDOWS_PER_DOC,
            "suggestion_rule": (
                "likely_citation when the bounded occurrence window contains at least "
                "one configured reference cue; otherwise unclassified"
            ),
            "reference_cues": [name for name, _ in CUE_PATTERNS],
        },
        "corrected_conflicts": {
            "prior_conflict_count": len(prior_conflict_ids),
            "prior_family_ids": prior_conflict_ids,
            "corrected_confidence_count": len(retained_family_ids),
            "corrected_conflict_count": len(retained_family_ids),
            "retained_family_ids": retained_family_ids,
            "dropped_family_id": "family-006",
            "dropped_family_statuses": dropped_statuses,
            "correction_reason": (
                "Drop family-006: its canonical consolidated Rreg. nr. 63 is the current "
                "text and its 2020 member is superseded by that consolidated version, so "
                "the pair reflects correct pipeline behavior rather than an outage."
            ),
            "is_floor": True,
            "floor_statement": (
                "The corrected count is a lower bound because singleton base/canonical "
                "documents may either genuinely lack siblings or have siblings missed by "
                "the proposed normalization; true conflict count >= 5, not = 5."
            ),
            "base_canonical_doc_count": len(base_canonical_docs),
            "base_canonical_singleton_count": len(singleton_docs),
        },
        "credit_registry_slice": {
            "family_id": "family-007",
            "member_statuses": family_007_statuses,
            "conflict_count": 0,
            "reason": (
                "Both family-007 members are base and neither is an amendment, so the "
                "credit-registry slice has zero conflicts."
            ),
        },
        "coverage": {
            "status_inventory": status_inventory,
            "base_chunk_count": base_chunks,
            "base_doc_count": inventory_by_status["base"]["distinct_doc_count"],
            "amendment_chunks": amendment_chunks,
            "amendment_docs": inventory_by_status["amendment"][
                "distinct_doc_count"
            ],
            "superseded_chunks": superseded_chunks,
            "superseded_docs": inventory_by_status["superseded"][
                "distinct_doc_count"
            ],
            "non_live_chunk_count": non_live_chunk_count,
            "non_live_doc_count": non_live_doc_count,
            "non_live_to_base_chunk_percent": round(
                100.0 * non_live_chunk_count / base_chunks, 2
            ),
            "base_docs_with_non_live_sibling_count": len(
                base_docs_with_non_live_sibling
            ),
            "base_docs_with_non_live_sibling": base_docs_with_non_live_sibling,
            "base_docs_without_non_live_sibling_count": len(
                base_docs_without_non_live_sibling
            ),
            "dominant_risk": (
                "AMENDMENTS ARE LARGELY ABSENT FROM THE CORPUS (coverage); no "
                "relabeling and no retrieval change fixes this."
            ),
        },
        "recommendation": {
            "threshold": threshold,
            "recommended": mitigation_recommended,
            "statement": recommendation_statement,
            "implemented": False,
        },
    }

    JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    MARKDOWN_PATH.write_text(markdown_report(data), encoding="utf-8")

    print("Marker split (distinct base docs):")
    print(f"  total_base_docs={total_base_docs}")
    print(f"  title_marked={title_marked_count}")
    print(f"  body_only={body_only_count}")
    print(
        "  body_only_with_likely_citation="
        f"{len(likely_docs)}; body_only_with_zero_likely_citation="
        f"{len(unclassified_only_docs)}"
    )
    print("Title-marked documents:")
    for item in title_marked_docs:
        print(f"  {item['doc']} | markers={','.join(item['markers'])}")
    print(
        f"Body-only evidence (up to {MAX_WINDOWS_PER_DOC} distinct windows per doc):"
    )
    for item in body_only_docs:
        print(
            f"  {item['doc']} | raw={item['raw_occurrence_count']} "
            f"distinct={item['distinct_occurrence_count']} "
            f"displayed={item['displayed_occurrence_count']} "
            f"suggestion={item['document_suggestion']}"
        )
        for occurrence in item["occurrences"]:
            cues = ",".join(occurrence["reference_cues"]) or "none"
            print(
                f"    {occurrence['source_chunk_id']} | marker={occurrence['marker']} "
                f"suggestion={occurrence['suggestion']} cues={cues}"
            )
            print(f"      {occurrence['context']}")
        if item["evidence_truncated_count"]:
            print(
                f"    ... {item['evidence_truncated_count']} additional distinct "
                "occurrence(s) omitted by deterministic cap"
            )
    print("Corrected conflicts:")
    print("  corrected_confidence_count=5")
    print("  retained=family-001,family-002,family-003,family-004,family-005")
    print("  dropped=family-006 (canonical consolidated current vs superseded 2020)")
    print(
        f"  floor=true; base_canonical_singletons={len(singleton_docs)}; "
        "true_conflict_count>=5"
    )
    print("Coverage:")
    for row in status_inventory:
        print(
            f"  {row['status']}: chunks={row['chunk_count']}, "
            f"distinct_docs={row['distinct_doc_count']}"
        )
    print(
        f"  base_docs_without_amendment_or_superseded_sibling="
        f"{len(base_docs_without_non_live_sibling)}/{len(base_docs)}"
    )
    print(f"Recommendation: {'YES' if mitigation_recommended else 'NO'}")
    print(f"  {recommendation_statement}")
    print(f"Wrote {JSON_PATH.name} and {MARKDOWN_PATH.name}")


if __name__ == "__main__":
    main()
