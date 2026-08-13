#!/usr/bin/env python3
"""Read-only audit of temporal validity in the BoABot corpus."""

# Derived structures used below:
# - documents_by_title holds one normalized record per distinct DB `doc` value.
# - families holds proposed multi-document regulation families after normalization.
# - conflicts holds families containing both retrievable and non-retrievable statuses.
# - marker_coverage holds per-status title/text version-marker document counts.
# - collisions holds eval queries whose retrieved hits repeat a proposed family.
# - credit_registry_slice holds the frequency-derived credit-registry family subset.

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg

sys.dont_write_bytecode = True

from retrieve import retrieve, shutdown  # noqa: E402  (required retrieval path)


ROOT = Path(__file__).resolve().parent
DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
EVAL_FILES = ("eval_retrieval.jsonl", "eval_handwritten.jsonl")
LIVE_STATUSES = {"canonical", "base"}
NON_LIVE_STATUSES = {"amendment", "superseded"}
MARKERS = ("ndryshuar", "konsoliduar", "integruar")

STOPWORDS = {
    "dhe",
    "e",
    "i",
    "me",
    "mbi",
    "ne",
    "nje",
    "nga",
    "per",
    "se",
    "te",
}
DOCUMENT_WORDS = {
    "date",
    "dat",
    "doc",
    "dt",
    "nr",
    "rreg",
    "rregullore",
    "rregulloren",
    "rregullorja",
    "vendim",
    "vendimi",
    "versioni",
}

NORMALIZATION_SPEC = [
    "lowercase and Unicode NFKD diacritic stripping",
    "replace underscores/punctuation with spaces and remove a trailing .pdf suffix",
    "remove Albanian i/e ndryshuar, i/e konsoliduar, i/e integruar phrases and standalone version-marker equivalents",
    "remove labelled dates, four-digit years, decision/archive number tokens, and normalize whitespace",
    "remove generic regulation/agreement/amendment preambles to expose the subject",
    "join exact, whitespace-equivalent, subject-prefix, or informative-token-subset subjects",
    "fallback: join the same generic regulation number only when one subject is unusable and explicit years do not conflict",
]


def ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def informative_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in value.split()
        if token not in STOPWORDS and token not in DOCUMENT_WORDS and len(token) > 1
    )


def normalize_document(title: str) -> dict[str, Any]:
    """Normalize a title generically and retain an auditable transformation trace."""
    value = re.sub(r"(?i)\.pdf$", "", title)
    value = ascii_lower(value).replace("_", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    original_ascii = value
    rules: list[str] = ["lowercase + diacritic stripping + separator cleanup"]

    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", value)))
    regulation_number = None
    regulation_patterns = (
        r"\b(?:rregullore|rregullorja|rregulloren|rreg)\s+(?:nr\s+)?0*(\d+)\b",
        r"^nr\s+0*(\d+)\b.*\b(?:rregullore|rregullorja|rregulloren|rreg)\b",
    )
    for pattern in regulation_patterns:
        match = re.search(pattern, value)
        if match:
            regulation_number = str(int(match.group(1)))
            break

    substitutions = (
        (
            r"\b(?:i|e)\s+(?:ndryshuar|konsoliduar|integruar)\b",
            "version-marker phrase",
        ),
        (
            r"\b(?:ndryshuar|ndrysh|konsoliduar|integruar|amended|rishikuar|final|publikim)\b",
            "standalone version marker",
        ),
        (
            r"\b(?:date|dt|dat)\s+\d{1,2}(?:\s+\d{1,2}){1,2}\b",
            "labelled date",
        ),
        (r"\b(?:19|20)\d{2}\b", "four-digit year/date"),
        (r"\b\d+\b", "decision/archive number token"),
    )
    for pattern, rule in substitutions:
        changed = re.sub(pattern, " ", value)
        if changed != value:
            rules.append(rule)
            value = changed
    value = re.sub(r"\s+", " ", value).strip()

    preambles = (
        r"^vendimi?\s+(?:nr\s+)?per\s+(?:miratimin\s+e\s+(?:disa\s+)?ndryshimeve?|nje\s+ndryshim)\s+ne\s+rregulloren\s+(?:per|mbi)\s+",
        r"^rregullore(?:n|ja)?\s+(?:nr\s+)?(?:per|mbi)\s+",
        r"^rreg\s+(?:nr\s+)?(?:per|mbi)\s+",
        r"^nr\s+(?:rregullore(?:n|ja)?|rreg)\s+(?:per|mbi)\s+",
        r"^nr\s+per\s+",
        r"^ndryshim\s+ne\s+marreveshjen\s+(?:per|mbi)\s+",
        r"^marrevesh(?:je|ja|jen)\b.*?\s+(?:per|mbi)\s+",
    )
    subject = value
    for pattern in preambles:
        changed = re.sub(pattern, "", subject)
        if changed != subject:
            rules.append("generic legal-document preamble")
            subject = changed
            break
    subject = re.sub(r"\s+", " ", subject).strip()
    tokens = informative_tokens(subject)

    return {
        "normalized_name": subject,
        "comparison_key": re.sub(r"\s+", "", subject),
        "informative_tokens": tokens,
        "regulation_number": regulation_number,
        "explicit_years": years,
        "rules_applied": sorted(set(rules)),
        "token_source": original_ascii,
        "usable_subject": len(tokens) >= 2 and len("".join(tokens)) >= 10,
    }


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def grouping_rule(left: dict[str, Any], right: dict[str, Any]) -> str | None:
    left_norm, right_norm = left["normalization"], right["normalization"]
    if left_norm["usable_subject"] and right_norm["usable_subject"]:
        if left_norm["normalized_name"] == right_norm["normalized_name"]:
            return "exact normalized subject"
        left_key, right_key = left_norm["comparison_key"], right_norm["comparison_key"]
        if left_key == right_key:
            return "whitespace-equivalent normalized subject"
        shorter, longer = sorted((left_key, right_key), key=lambda item: (len(item), item))
        if len(shorter) >= 14 and longer.startswith(shorter):
            return "normalized subject prefix"
        left_tokens, right_tokens = set(left_norm["informative_tokens"]), set(
            right_norm["informative_tokens"]
        )
        shorter_tokens, longer_tokens = sorted(
            (left_tokens, right_tokens), key=lambda item: (len(item), sorted(item))
        )
        if len(shorter_tokens) >= 2 and shorter_tokens <= longer_tokens:
            return "informative-token subset"

    same_number = (
        left_norm["regulation_number"] is not None
        and left_norm["regulation_number"] == right_norm["regulation_number"]
    )
    one_subject_unusable = not (
        left_norm["usable_subject"] and right_norm["usable_subject"]
    )
    years_conflict = bool(
        set(left_norm["explicit_years"])
        and set(right_norm["explicit_years"])
        and set(left_norm["explicit_years"]).isdisjoint(right_norm["explicit_years"])
    )
    if same_number and one_subject_unusable and not years_conflict:
        return "regulation-number fallback with missing subject and no conflicting year"
    return None


def build_families(documents_by_title: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    titles = sorted(documents_by_title)
    union_find = UnionFind(titles)
    edge_rules: dict[tuple[str, str], str] = {}
    for index, left_title in enumerate(titles):
        for right_title in titles[index + 1 :]:
            rule = grouping_rule(
                documents_by_title[left_title], documents_by_title[right_title]
            )
            if rule:
                union_find.union(left_title, right_title)
                edge_rules[(left_title, right_title)] = rule

    components: dict[str, list[str]] = defaultdict(list)
    for title in titles:
        components[union_find.find(title)].append(title)

    proposed: list[dict[str, Any]] = []
    for member_titles in components.values():
        if len(member_titles) <= 1:
            continue
        member_titles.sort()
        rules = sorted(
            {
                rule
                for (left, right), rule in edge_rules.items()
                if left in member_titles and right in member_titles
            }
        )
        usable_names = sorted(
            {
                documents_by_title[title]["normalization"]["normalized_name"]
                for title in member_titles
                if documents_by_title[title]["normalization"]["usable_subject"]
            },
            key=lambda item: (len(item), item),
        )
        if usable_names:
            family_name = usable_names[0]
        else:
            numbers = sorted(
                {
                    documents_by_title[title]["normalization"]["regulation_number"]
                    for title in member_titles
                    if documents_by_title[title]["normalization"]["regulation_number"]
                }
            )
            family_name = "regulation " + (numbers[0] if numbers else member_titles[0])
        members = []
        for title in member_titles:
            record = documents_by_title[title]
            norm = record["normalization"]
            members.append(
                {
                    "doc": title,
                    "status": record["status"],
                    "chunk_count": record["chunk_count"],
                    "normalized_name": norm["normalized_name"],
                    "normalization_rules_applied": norm["rules_applied"],
                }
            )
        uncertain_rules = [
            rule
            for rule in rules
            if rule
            in {
                "informative-token subset",
                "regulation-number fallback with missing subject and no conflicting year",
            }
        ]
        proposed.append(
            {
                "normalized_name": family_name,
                "grouping_rules": rules,
                "uncertain": bool(uncertain_rules),
                "uncertainty_reasons": uncertain_rules,
                "members": members,
            }
        )

    proposed.sort(
        key=lambda family: (
            family["normalized_name"],
            [member["doc"] for member in family["members"]],
        )
    )
    for index, family in enumerate(proposed, start=1):
        family["family_id"] = f"family-{index:03d}"
    return proposed


def load_eval_queries() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queries: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []
    query_like_names = {"query", "question", "prompt", "input"}
    for filename in EVAL_FILES:
        path = ROOT / filename
        schema: dict[str, Any] = {"file": filename, "status": "skipped"}
        try:
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            schema["reason"] = f"could not parse JSONL: {exc}"
            schemas.append(schema)
            continue
        if not rows or not all(isinstance(row, dict) for row in rows):
            schema["reason"] = "expected non-empty JSONL objects"
            schemas.append(schema)
            continue
        common_string_fields = sorted(
            field
            for field in set.intersection(*(set(row) for row in rows))
            if all(isinstance(row[field], str) and row[field].strip() for row in rows)
        )
        candidates = [field for field in common_string_fields if field in query_like_names]
        schema["row_count"] = len(rows)
        schema["common_string_fields"] = common_string_fields
        if len(candidates) != 1:
            schema["reason"] = (
                "schema did not expose exactly one recognized query-like string field"
            )
            schemas.append(schema)
            continue
        query_field = candidates[0]
        schema.update({"status": "recognized", "query_field": query_field})
        schemas.append(schema)
        for line_number, row in enumerate(rows, start=1):
            queries.append(
                {
                    "source_file": filename,
                    "line_number": line_number,
                    "query": row[query_field].strip(),
                }
            )
    queries.sort(key=lambda item: (item["source_file"], item["line_number"], item["query"]))
    schemas.sort(key=lambda item: item["file"])
    return queries, schemas


def derive_credit_registry_pattern(
    documents_by_title: dict[str, dict[str, Any]], families: list[dict[str, Any]]
) -> dict[str, Any]:
    token_frequency: Counter[str] = Counter()
    doc_tokens: dict[str, set[str]] = {}
    for title, record in documents_by_title.items():
        tokens = set(record["normalization"]["token_source"].split())
        doc_tokens[title] = tokens
        token_frequency.update(tokens)

    # Only generic concept stems are fixed; actual corpus tokens and their shared
    # registry stem are selected from observed title-token frequencies.
    concept_prefixes = ("credit", "kredi")
    registry_concept_prefixes = ("registry", "regjistr")
    credit_candidates = sorted(
        (
            {
                "token": token,
                "document_frequency": count,
                "registry_cooccurrence_document_frequency": sum(
                    1
                    for tokens in doc_tokens.values()
                    if token in tokens
                    and any(
                        other.startswith(registry_concept_prefixes)
                        for other in tokens
                    )
                ),
            }
            for token, count in token_frequency.items()
            if token.startswith(concept_prefixes)
        ),
        key=lambda item: (
            -item["registry_cooccurrence_document_frequency"],
            -item["document_frequency"],
            item["token"],
        ),
    )
    result: dict[str, Any] = {
        "derivation_rule": (
            "rank observed title tokens beginning with a generic credit concept stem "
            "by document-frequency of co-occurrence with observed registry-concept "
            "tokens, then by overall document frequency; derive the registry match "
            "stem as the longest common prefix of those observed companion tokens"
        ),
        "credit_token_candidates": credit_candidates,
        "selected_credit_token": None,
        "selected_registry_companion_stem": None,
        "matching_family_ids": [],
        "conflict_family_ids": [],
        "conflict_family_count": 0,
        "note": None,
    }
    if not credit_candidates:
        result["note"] = "No observed credit-token candidate was found in document titles."
        return result

    credit_token = credit_candidates[0]["token"]
    companion_frequency: Counter[str] = Counter()
    for tokens in doc_tokens.values():
        if credit_token not in tokens:
            continue
        companions_in_doc = {
            token
            for token in tokens
            if token.startswith(registry_concept_prefixes)
        }
        companion_frequency.update(companions_in_doc)
    companions = sorted(
        (
            {"token": token, "document_frequency": count}
            for token, count in companion_frequency.items()
        ),
        key=lambda item: (-item["document_frequency"], item["token"]),
    )
    result["selected_credit_token"] = credit_token
    result["registry_companion_candidates"] = companions
    if not companions:
        result["note"] = "No co-occurring registry companion token was discoverable."
        return result

    observed_companions = [item["token"] for item in companions]
    registry_stem = observed_companions[0]
    for token in observed_companions[1:]:
        limit = min(len(registry_stem), len(token))
        index = 0
        while index < limit and registry_stem[index] == token[index]:
            index += 1
        registry_stem = registry_stem[:index]
    if len(registry_stem) < 4:
        result["note"] = "Observed registry companion tokens had no stable common stem."
        return result
    result["selected_registry_companion_stem"] = registry_stem
    matching_ids = []
    conflict_ids = []
    for family in families:
        tokens = family["normalized_name"].split()
        if credit_token in tokens and any(token.startswith(registry_stem) for token in tokens):
            matching_ids.append(family["family_id"])
            statuses = {member["status"] for member in family["members"]}
            if statuses & LIVE_STATUSES and statuses & NON_LIVE_STATUSES:
                conflict_ids.append(family["family_id"])
    result["matching_family_ids"] = sorted(matching_ids)
    result["conflict_family_ids"] = sorted(conflict_ids)
    result["conflict_family_count"] = len(conflict_ids)
    if not matching_ids:
        result["note"] = "No proposed family matched the frequency-derived pattern."
    return result


def markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Corpus temporal-validity audit",
        "",
        "> **Grouping caveat:** Every document family in this report is a normalization-based proposal, not ground truth. Human review is required.",
        "",
        "This is a read-only survey. Retrieval uses `retrieve.retrieve(query, k=5)` with its default statuses.",
        "",
        "## Headline numbers",
        "",
        f"- Proposed multi-document families: **{data['headline']['total_families']}**",
        f"- Live-vs-amended conflict families: **{data['headline']['conflict_families']}**",
        f"- Eval queries with intra-family collisions: **{data['headline']['queries_with_collisions']}** of **{data['retrieval_ambiguity']['total_queries_run']}**",
        f"- Collision rate: **{data['headline']['collision_rate_percent']:.2f}%**",
        "",
        "## 1. Status inventory",
        "",
        "| Status | Chunks | Distinct docs |",
        "|---|---:|---:|",
    ]
    for row in data["status_inventory"]:
        lines.append(f"| {row['status']} | {row['chunk_count']} | {row['distinct_doc_count']} |")

    lines.extend(
        [
            "",
            "## 2. Proposed document families",
            "",
            "Normalization rules:",
            "",
        ]
    )
    lines.extend(f"- {rule}" for rule in data["normalization"]["rules"])
    for family in data["families"]:
        uncertainty = " — **UNCERTAIN**" if family["uncertain"] else ""
        lines.extend(
            [
                "",
                f"### {family['family_id']}: {family['normalized_name']}{uncertainty}",
                "",
                f"Grouping rule(s): {', '.join(family['grouping_rules'])}.",
                "",
                "| Document | Status | Chunks | Member normalization |",
                "|---|---|---:|---|",
            ]
        )
        for member in family["members"]:
            doc = member["doc"].replace("|", "\\|")
            normalized = member["normalized_name"].replace("|", "\\|")
            lines.append(
                f"| `{doc}` | {member['status']} | {member['chunk_count']} | `{normalized}` |"
            )

    lines.extend(
        [
            "",
            "## 3. Live-vs-amended conflicts",
            "",
            f"Conflict family count: **{len(data['conflicts'])}**.",
            "",
        ]
    )
    if data["conflicts"]:
        for conflict in data["conflicts"]:
            statuses = ", ".join(conflict["statuses"])
            lines.append(
                f"- `{conflict['family_id']}` — {conflict['normalized_name']} ({statuses})"
            )
    else:
        lines.append("No conflict family was found.")

    lines.extend(
        [
            "",
            "## 4. Version-marker coverage",
            "",
            "Counts are distinct documents. `text:any` means at least one chunk for the document contains a marker.",
            "",
            "| Status | Docs | Title:any | Title:ndryshuar | Title:konsoliduar | Title:integruar | Text:any | Text:ndryshuar | Text:konsoliduar | Text:integruar |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for status, coverage in data["version_marker_coverage"]["by_status"].items():
        lines.append(
            f"| {status} | {coverage['document_count']} | {coverage['title']['any']} | "
            f"{coverage['title']['ndryshuar']} | {coverage['title']['konsoliduar']} | "
            f"{coverage['title']['integruar']} | {coverage['text']['any']} | "
            f"{coverage['text']['ndryshuar']} | {coverage['text']['konsoliduar']} | "
            f"{coverage['text']['integruar']} |"
        )
    canonical_suspicions = data["version_marker_coverage"][
        "canonical_without_marker_anywhere"
    ]
    base_suspicions = data["version_marker_coverage"]["base_with_marker_anywhere"]
    lines.extend(
        [
            "",
            f"Canonical docs with no consolidation marker anywhere: **{len(canonical_suspicions)}**.",
            "",
        ]
    )
    lines.extend(f"- `{doc}`" for doc in canonical_suspicions)
    if not canonical_suspicions:
        lines.append("- None")
    lines.extend(
        [
            "",
            f"Base docs carrying at least one consolidation marker: **{len(base_suspicions)}**.",
            "",
        ]
    )
    lines.extend(f"- `{doc}`" for doc in base_suspicions)
    if not base_suspicions:
        lines.append("- None")

    ambiguity = data["retrieval_ambiguity"]
    lines.extend(
        [
            "",
            "## 5. Retrieval ambiguity rate",
            "",
            f"Queries run: **{ambiguity['total_queries_run']}**. Queries with collisions: **{ambiguity['queries_with_collisions']}** (**{ambiguity['collision_rate_percent']:.2f}%**).",
            "",
            "Eval schema inspection:",
            "",
        ]
    )
    for schema in ambiguity["eval_file_schemas"]:
        if schema["status"] == "recognized":
            lines.append(
                f"- `{schema['file']}`: recognized `{schema['query_field']}`; {schema['row_count']} rows; common string fields: {', '.join(schema['common_string_fields'])}."
            )
        else:
            lines.append(f"- `{schema['file']}`: skipped — {schema['reason']}.")
    lines.extend(["", "Ten worst collision examples:", ""])
    if not ambiguity["worst_examples"]:
        lines.append("No collision examples were found.")
    for index, example in enumerate(ambiguity["worst_examples"], start=1):
        lines.extend(
            [
                f"{index}. **{example['query']}** (`{example['source_file']}:{example['line_number']}`)",
                "",
            ]
        )
        for family in example["colliding_families"]:
            lines.append(
                f"   - {family['family_id']} / {family['normalized_name']} — {family['hit_count']} hits"
            )
            for hit in family["hits"]:
                lines.append(
                    f"     - rank {hit['rank']}: `{hit['doc']}` / {hit['status']} / `{hit['id']}`"
                )
        lines.append("")

    credit = data["credit_registry_slice"]
    lines.extend(
        [
            "## 6. Credit-registry slice",
            "",
            f"Derivation: {credit['derivation_rule']}.",
            "",
            f"Selected observed credit token: `{credit['selected_credit_token']}`; selected companion stem: `{credit['selected_registry_companion_stem']}`.",
            "",
        ]
    )
    if credit["matching_family_ids"]:
        lines.append(
            "Matching proposed families: "
            + ", ".join(f"`{item}`" for item in credit["matching_family_ids"])
            + "."
        )
        lines.append("")
        lines.append(
            f"Credit-registry conflict families: **{credit['conflict_family_count']}**"
            + (
                " (" + ", ".join(credit["conflict_family_ids"]) + ")."
                if credit["conflict_family_ids"]
                else "."
            )
        )
    else:
        lines.append(credit["note"] or "No credit-registry family was found.")

    lines.extend(["", "## Uncertain proposed groupings", ""])
    if data["uncertain_families"]:
        for family in data["uncertain_families"]:
            lines.append(
                f"- `{family['family_id']}` — {family['normalized_name']}: {', '.join(family['reasons'])}."
            )
    else:
        lines.append("No grouping was marked uncertain by the deterministic rules.")

    lines.extend(["", "## Data observations that qualify the prompt framing", ""])
    lines.extend(f"- {item}" for item in data["framing_observations"])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    with psycopg.connect(DSN) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, count(*), count(DISTINCT doc) "
                "FROM chunks GROUP BY 1 ORDER BY 1"
            )
            status_inventory = [
                {
                    "status": status,
                    "chunk_count": chunk_count,
                    "distinct_doc_count": doc_count,
                }
                for status, chunk_count, doc_count in cursor.fetchall()
            ]
            cursor.execute(
                "SELECT doc, status, count(*), "
                "bool_or(position('ndryshuar' in lower(coalesce(text, ''))) > 0), "
                "bool_or(position('konsoliduar' in lower(coalesce(text, ''))) > 0), "
                "bool_or(position('integruar' in lower(coalesce(text, ''))) > 0) "
                "FROM chunks GROUP BY doc, status ORDER BY doc, status"
            )
            document_rows = cursor.fetchall()

    documents_by_title: dict[str, dict[str, Any]] = {}
    for doc, status, chunk_count, text_ndryshuar, text_konsoliduar, text_integruar in document_rows:
        if doc in documents_by_title:
            raise RuntimeError(
                f"Document {doc!r} has multiple statuses; retrieval results do not expose status, so the audit will not guess."
            )
        normalized_title = ascii_lower(doc)
        title_markers = {marker: marker in normalized_title for marker in MARKERS}
        text_markers = {
            "ndryshuar": bool(text_ndryshuar),
            "konsoliduar": bool(text_konsoliduar),
            "integruar": bool(text_integruar),
        }
        documents_by_title[doc] = {
            "doc": doc,
            "status": status,
            "chunk_count": chunk_count,
            "title_markers": title_markers,
            "text_markers": text_markers,
            "normalization": normalize_document(doc),
        }

    families = build_families(documents_by_title)
    family_by_id = {family["family_id"]: family for family in families}
    family_id_by_doc = {
        member["doc"]: family["family_id"]
        for family in families
        for member in family["members"]
    }

    conflicts = []
    for family in families:
        statuses = sorted({member["status"] for member in family["members"]})
        if set(statuses) & LIVE_STATUSES and set(statuses) & NON_LIVE_STATUSES:
            conflicts.append(
                {
                    "family_id": family["family_id"],
                    "normalized_name": family["normalized_name"],
                    "statuses": statuses,
                    "member_docs": sorted(member["doc"] for member in family["members"]),
                }
            )
    conflicts.sort(key=lambda item: item["family_id"])

    marker_coverage: dict[str, Any] = {"by_status": {}}
    statuses = [row["status"] for row in status_inventory]
    for status in statuses:
        records = sorted(
            (
                record
                for record in documents_by_title.values()
                if record["status"] == status
            ),
            key=lambda item: item["doc"],
        )
        title_counts = {
            marker: sum(record["title_markers"][marker] for record in records)
            for marker in MARKERS
        }
        text_counts = {
            marker: sum(record["text_markers"][marker] for record in records)
            for marker in MARKERS
        }
        title_counts["any"] = sum(any(record["title_markers"].values()) for record in records)
        text_counts["any"] = sum(any(record["text_markers"].values()) for record in records)
        marker_coverage["by_status"][status] = {
            "document_count": len(records),
            "title": dict(sorted(title_counts.items())),
            "text": dict(sorted(text_counts.items())),
        }
    marker_coverage["canonical_without_marker_anywhere"] = sorted(
        record["doc"]
        for record in documents_by_title.values()
        if record["status"] == "canonical"
        and not any(record["title_markers"].values())
        and not any(record["text_markers"].values())
    )
    marker_coverage["base_with_marker_anywhere"] = sorted(
        record["doc"]
        for record in documents_by_title.values()
        if record["status"] == "base"
        and (
            any(record["title_markers"].values())
            or any(record["text_markers"].values())
        )
    )

    queries, eval_schemas = load_eval_queries()
    collisions = []
    try:
        for query_record in queries:
            hits = retrieve(query_record["query"], k=5)
            hits_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for rank, hit in enumerate(hits, start=1):
                family_id = family_id_by_doc.get(hit["doc"])
                if family_id is None:
                    continue
                hits_by_family[family_id].append(
                    {
                        "rank": rank,
                        "id": hit["id"],
                        "doc": hit["doc"],
                        "status": documents_by_title[hit["doc"]]["status"],
                    }
                )
            colliding_families = []
            for family_id, family_hits in sorted(hits_by_family.items()):
                if len(family_hits) >= 2:
                    colliding_families.append(
                        {
                            "family_id": family_id,
                            "normalized_name": family_by_id[family_id]["normalized_name"],
                            "hit_count": len(family_hits),
                            "hits": family_hits,
                        }
                    )
            if colliding_families:
                collisions.append(
                    {
                        **query_record,
                        "max_family_hit_count": max(
                            family["hit_count"] for family in colliding_families
                        ),
                        "collision_hit_count": sum(
                            family["hit_count"] for family in colliding_families
                        ),
                        "colliding_families": colliding_families,
                    }
                )
    finally:
        shutdown()

    collisions.sort(
        key=lambda item: (
            -item["max_family_hit_count"],
            -item["collision_hit_count"],
            item["query"],
            item["source_file"],
            item["line_number"],
        )
    )
    total_queries = len(queries)
    collision_rate = round((100.0 * len(collisions) / total_queries), 2) if total_queries else 0.0

    credit_registry_slice = derive_credit_registry_pattern(documents_by_title, families)
    uncertain_families = [
        {
            "family_id": family["family_id"],
            "normalized_name": family["normalized_name"],
            "reasons": family["uncertainty_reasons"],
        }
        for family in families
        if family["uncertain"]
    ]

    framing_observations = []
    if marker_coverage["base_with_marker_anywhere"]:
        framing_observations.append(
            f"{len(marker_coverage['base_with_marker_anywhere'])} base-labelled documents carry a specified consolidation marker in their title or chunk text, so filename-derived base labels do not cleanly track marker evidence."
        )
    if marker_coverage["canonical_without_marker_anywhere"]:
        framing_observations.append(
            f"{len(marker_coverage['canonical_without_marker_anywhere'])} canonical-labelled documents have no specified consolidation marker in title or chunk text."
        )
    framing_observations.append(
        "The amendment/superseded status establishes an ingest label, not chronology by itself; conflict families identify risk candidates rather than proving that every live member is older."
    )

    data = {
        "audit_scope": {
            "database": DSN,
            "read_only": True,
            "family_grouping_is_proposal_not_ground_truth": True,
            "retrieval_call": "retrieve.retrieve(query, k=5) with default statuses",
        },
        "headline": {
            "total_families": len(families),
            "conflict_families": len(conflicts),
            "queries_with_collisions": len(collisions),
            "collision_rate_percent": collision_rate,
        },
        "status_inventory": status_inventory,
        "normalization": {"rules": NORMALIZATION_SPEC},
        "families": families,
        "conflicts": conflicts,
        "version_marker_coverage": marker_coverage,
        "retrieval_ambiguity": {
            "eval_file_schemas": eval_schemas,
            "total_queries_run": total_queries,
            "queries_with_collisions": len(collisions),
            "collision_rate_percent": collision_rate,
            "collisions": collisions,
            "worst_examples": collisions[:10],
        },
        "credit_registry_slice": credit_registry_slice,
        "uncertain_families": uncertain_families,
        "framing_observations": framing_observations,
    }

    (ROOT / "audit_temporal.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ROOT / "AUDIT_TEMPORAL.md").write_text(markdown_report(data), encoding="utf-8")
    print("Status inventory:")
    for row in status_inventory:
        print(
            f"  {row['status']}: chunks={row['chunk_count']}, "
            f"distinct_docs={row['distinct_doc_count']}"
        )
    print(
        f"Wrote audit_temporal.json and AUDIT_TEMPORAL.md: "
        f"families={len(families)}, conflicts={len(conflicts)}, "
        f"collisions={len(collisions)}/{total_queries} ({collision_rate:.2f}%)"
    )


if __name__ == "__main__":
    main()
