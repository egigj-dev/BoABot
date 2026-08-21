#!/usr/bin/env python3
"""Read-only, in-memory probe for a tighter overlap-aware chunking policy.

This is deliberately separate from the live rebuild entry points.  It reads
``chunks_backup`` as its source, builds candidate chunks only in memory, and
compares dense recall without inserting, updating, deleting, or creating any
database object.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import psycopg
import torch
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).parent))
import rebuild_chunks as base  # noqa: E402
import rebuild_chunks_dedup as dedup  # noqa: E402


DSN = os.environ.get("BOABOT_DSN", "postgresql://boa:boa@127.0.0.1:5433/boa")
HARD_CAP = base.MAX_CHARS
DEFAULT_MERGE_CAP = 3_000
DEFAULT_SUBCLAUSE_TRIGGER = 6
LIVE_STATUSES = {"canonical", "base"}
# Deliberately line-anchored: these are list items, not article references in
# prose.  The set includes Albanian letters used in lettered legal lists.
SUBCLAUSE = re.compile(r"^[ \t]*(?:\d{1,3}\.\s+|[a-zçë]\)\s+)", re.MULTILINE | re.IGNORECASE)


def fetch_rows(conn: psycopg.Connection, table: str) -> list[base.SourceRow]:
    if table not in {"chunks", "chunks_backup"}:
        raise ValueError(f"unsupported read-only table: {table}")
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, doc, article, status, section, url, text, embedding::text
                FROM {table} ORDER BY id"""
        )
        return [base.SourceRow(*row) for row in cur.fetchall()]


def subclause_starts(text: str) -> list[int]:
    return [match.start() for match in SUBCLAUSE.finditer(text) if match.start() > 0]


def force_cap(start: int, end: int, text: str, cap: int) -> list[tuple[int, int]]:
    """Use a newline/space fallback to keep an oversized atom below ``cap``."""
    pieces: list[tuple[int, int]] = []
    while end - start > cap:
        limit = start + cap
        split_at = text.rfind("\n", start + cap // 2, limit + 1)
        if split_at < start + cap // 2:
            split_at = text.rfind(" ", start + cap // 2, limit + 1)
        if split_at < start + cap // 2:
            split_at = limit
        pieces.append((start, split_at))
        start = split_at
        while start < end and text[start].isspace():
            start += 1
    if start < end:
        pieces.append((start, end))
    return pieces


def policy_pieces(text: str, merge_cap: int, trigger: int) -> tuple[list[tuple[int, int]], int]:
    """Pack whole sub-clauses up to merge_cap, retaining a 12k absolute cap.

    A group is considered sub-clause heavy when it has more than ``trigger``
    list-item boundaries.  Only heavy or over-cap groups are partitioned;
    ordinary short articles remain whole.  A single oversized sub-clause gets
    a newline/space fallback after semantic boundaries are exhausted.
    """
    starts = subclause_starts(text)
    if len(text) <= merge_cap and len(starts) <= trigger:
        return [(0, len(text))], len(starts)

    boundaries = [0, *starts, len(text)]
    pieces: list[tuple[int, int]] = []
    start_index = 0
    while start_index < len(boundaries) - 1:
        start = boundaries[start_index]
        end_index = start_index + 1
        while end_index < len(boundaries) and boundaries[end_index] - start <= merge_cap:
            end_index += 1
        # If the next sub-clause alone is wider than the merge cap, take that
        # atom and use the raw fallback only inside the oversized atom.
        if end_index == start_index + 1:
            end = boundaries[end_index]
            next_index = end_index
        else:
            end = boundaries[end_index - 1]
            next_index = end_index - 1
        pieces.extend(force_cap(start, end, text, merge_cap))
        start_index = next_index
    return pieces, len(starts)


def candidate_id(source_ids: list[str], used: set[str], group_seed: str, ordinal: int) -> str:
    for source_id in source_ids:
        if source_id not in used:
            return source_id
    # More candidate pieces than original fragment IDs is possible when a
    # single source fragment contains many clauses.  Derived IDs stay local to
    # the probe and cannot collide with the source ID vocabulary.
    number = ordinal
    while f"{group_seed}__probe_{number}" in used:
        number += 1
    return f"{group_seed}__probe_{number}"


def build_candidate(
    rows: list[base.SourceRow], merge_cap: int, trigger: int
) -> tuple[list[base.OutputRow], dict[str, str], dict[str, int]]:
    grouped: dict[tuple[str | None, str | None, str | None], list[base.SourceRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.doc, row.article, row.status)].append(row)

    output: list[base.OutputRow] = []
    mapping: dict[str, str] = {}
    stats: Counter[str] = Counter()
    for source_rows in grouped.values():
        source_rows.sort(key=lambda row: row.id)
        first = source_rows[0]
        if len(source_rows) == 1:
            output.append(base.OutputRow(**first.__dict__))
            mapping[first.id] = first.id
            stats["unchanged"] += 1
            continue

        if not base.verified_article(source_rows):
            seen: set[str] = set()
            for row in source_rows:
                prefix = base.normalized_prefix(row.text)
                if prefix in seen:
                    mapping[row.id] = next(
                        item.id for item in output
                        if item.doc == row.doc and item.article == row.article
                        and item.status == row.status and base.normalized_prefix(item.text) == prefix
                    )
                    stats["ambiguous_deduped"] += 1
                else:
                    seen.add(prefix)
                    output.append(base.OutputRow(**row.__dict__))
                    mapping[row.id] = row.id
                    stats["ambiguous_retained"] += 1
            continue

        text, spans, removed = dedup.combined_text(source_rows)
        pieces, clause_count = policy_pieces(text, merge_cap, trigger)
        stats["overlap_chars_removed"] += removed
        stats["subclauses_seen"] += clause_count
        if len(pieces) == 1:
            stats["merged_groups"] += 1
        else:
            stats["policy_split_groups"] += 1
            stats["policy_split_output_chunks"] += len(pieces)

        used: set[str] = set()
        for ordinal, (start, end) in enumerate(pieces, start=1):
            source_ids = dedup.ids_for_piece(spans, start, end)
            row_id = candidate_id(source_ids, used, first.id, ordinal)
            used.add(row_id)
            output.append(base.OutputRow(
                id=row_id, doc=first.doc, article=first.article, status=first.status,
                section=first.section, url=first.url, text=text[start:end].strip(), embedding=None,
            ))
            for source_id in source_ids:
                mapping.setdefault(source_id, row_id)

    source_ids = {row.id for row in rows}
    if set(mapping) != source_ids:
        raise RuntimeError(f"unmapped source IDs: {sorted(source_ids - set(mapping))[:5]}")
    ids = [row.id for row in output]
    if len(ids) != len(set(ids)):
        raise RuntimeError("candidate output IDs are not unique")
    if any(len(row.text) > HARD_CAP for row in output):
        raise RuntimeError("candidate chunk exceeds the hard character cap")
    return output, mapping, dict(stats)


def vector_from_db(value: str) -> np.ndarray:
    vector = np.fromstring(value.strip()[1:-1], dtype=np.float32, sep=",")
    if vector.shape != (1024,):
        raise ValueError(f"expected 1024-dimensional vector, got {vector.shape}")
    return vector


def reuse_exact_embeddings(
    candidate: list[base.OutputRow], known_rows: list[base.SourceRow]
) -> int:
    """Reuse a vector only when the candidate text is byte-for-byte known."""
    by_text = {row.text: row.embedding for row in known_rows}
    reused = 0
    for row in candidate:
        if row.embedding is None and row.text in by_text:
            row.embedding = by_text[row.text]
            reused += 1
    return reused


def vectors_for_candidate(
    rows: list[base.OutputRow], batch_size: int, model: SentenceTransformer
) -> np.ndarray:
    vectors: list[np.ndarray | None] = [None] * len(rows)
    changed_indexes = [index for index, row in enumerate(rows) if row.embedding is None]
    for index, row in enumerate(rows):
        if row.embedding is not None:
            vectors[index] = vector_from_db(row.embedding)
    if changed_indexes:
        # Similar-length batches avoid padding a 500-character item to the
        # length of a rare 12k fallback chunk.  Vectors are restored to their
        # original row positions immediately afterwards.
        changed_indexes.sort(key=lambda index: len(rows[index].text), reverse=True)
        encoded = model.encode(
            [rows[index].text for index in changed_indexes], batch_size=batch_size,
            normalize_embeddings=True, show_progress_bar=True,
        )
        for index, vector in zip(changed_indexes, encoded, strict=True):
            vectors[index] = np.asarray(vector, dtype=np.float32)
    return np.vstack(vectors).astype(np.float32, copy=False)


def load_cases(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def score_recall(
    cases: list[dict[str, str]], rows: list[base.OutputRow], mapping: dict[str, str],
    vectors: np.ndarray, batch_size: int, model: SentenceTransformer,
) -> tuple[dict[str, list[int]], dict[str, list[list[str]]]]:
    usable = [index for index, row in enumerate(rows) if row.status in LIVE_STATUSES]
    usable_vectors = vectors[usable]
    usable_rows = [rows[index] for index in usable]
    row_by_id = {row.id: row for row in rows}
    queries = model.encode(
        [case["question"] for case in cases], batch_size=batch_size,
        normalize_embeddings=True, show_progress_bar=True,
    )
    counts = {"exact": [0, 0, 0], "doc": [0, 0, 0], "article": [0, 0, 0]}
    rankings: dict[str, list[list[str]]] = {"ids": [], "mapped_gold": []}
    for case, query in zip(cases, queries, strict=True):
        mapped_gold = mapping[case["gold_id"]]
        gold = row_by_id[mapped_gold]
        scores = usable_vectors @ np.asarray(query, dtype=np.float32)
        top = np.argsort(-scores, kind="stable")[:10]
        hits = [usable_rows[index] for index in top]
        rankings["ids"].append([row.id for row in hits])
        rankings["mapped_gold"].append([mapped_gold])
        for metric, predicate in {
            "exact": lambda row: row.id == mapped_gold,
            "doc": lambda row: row.url == gold.url,
            "article": lambda row: row.url == gold.url and row.article == gold.article,
        }.items():
            for position, cutoff in enumerate((1, 5, 10)):
                if any(predicate(row) for row in hits[:cutoff]):
                    counts[metric][position] += 1
    return counts, rankings


def describe_distribution(rows: list[base.OutputRow]) -> str:
    lengths = [len(row.text) for row in rows]
    buckets = Counter()
    for length in lengths:
        buckets["<=1k" if length <= 1000 else "1-2k" if length <= 2000 else "2-3k" if length <= 3000
                else "3-6k" if length <= 6000 else "6-12k"] += 1
    return " ".join(f"{name}={buckets[name]}" for name in ("<=1k", "1-2k", "2-3k", "3-6k", "6-12k"))


def print_table(proposed: dict[str, list[int]], total: int) -> None:
    old = {"exact": [26, 33, 34], "doc": [37, 39, 40], "article": [33, 36, 36]}
    live = {"exact": [26, 33, 34], "doc": [36, 39, 40], "article": [31, 34, 35]}
    print("recall (measured old/live baselines supplied in brief; proposed measured here)")
    print("metric       CHUNKS_BACKUP       CURRENT live        PROPOSED")
    for metric in ("exact", "doc", "article"):
        as_text = lambda values: "  ".join(
            f"@{cutoff} {value}/{total}" for cutoff, value in zip((1, 5, 10), values, strict=True)
        )
        print(f"{metric:7}            {as_text(old[metric]):27} {as_text(live[metric]):27} {as_text(proposed[metric])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge-cap", type=int, default=DEFAULT_MERGE_CAP)
    parser.add_argument("--subclause-trigger", type=int, default=DEFAULT_SUBCLAUSE_TRIGGER)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--cpu-threads", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--eval", type=Path, default=Path("eval_handwritten_fixed.jsonl"))
    parser.add_argument(
        "--export-input", metavar="PATH", type=Path, default=None,
        help="Write ONLY the changed rows (embedding is None) as id+text JSONL "
             "for the Colab notebook, then exit (no CPU encode, no recall).",
    )
    parser.add_argument(
        "--embeddings-file", metavar="PATH", type=Path, default=None,
        help="Load Colab output JSONL (id + embedding list) into the changed "
             "rows, then run recall; combined with --apply, reload the DB.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually back up and reload `chunks` (requires --embeddings-file; "
             "without it, validate + recall remain read-only).",
    )
    args = parser.parse_args()
    if (not 1 <= args.merge_cap <= HARD_CAP or args.subclause_trigger < 0
            or args.batch_size < 1 or args.cpu_threads < 1):
        parser.error(f"merge cap must be 1..{HARD_CAP}; trigger, batch size, and CPU threads must be >= 1")
    if args.export_input is not None and args.embeddings_file is not None:
        parser.error("--export-input and --embeddings-file cannot be used together")
    if args.apply and args.embeddings_file is None:
        parser.error("--apply requires --embeddings-file")
    torch.set_num_threads(args.cpu_threads)

    # The server enforces read-only transactions; this program has no DDL/DML.
    with psycopg.connect(DSN, options="-c default_transaction_read_only=on") as conn:
        backup = fetch_rows(conn, "chunks_backup")
        live = fetch_rows(conn, "chunks")
        live_count = len(live)
    candidate, mapping, stats = build_candidate(backup, args.merge_cap, args.subclause_trigger)
    reused_exact = reuse_exact_embeddings(candidate, [*backup, *live])
    metrics = dedup.duplicate_metrics(candidate, stats.get("overlap_chars_removed", 0))
    print(f"policy merge_cap={args.merge_cap} hard_cap={HARD_CAP} subclause_trigger={args.subclause_trigger} cpu_threads={args.cpu_threads}")
    print(f"source_rows={len(backup)} output_rows={len(candidate)} changed_rows={sum(row.embedding is None for row in candidate)} exact_embeddings_reused={reused_exact}")
    print("group_stats " + " ".join(f"{key}={value}" for key, value in sorted(stats.items())))
    print(f"size_distribution {describe_distribution(candidate)}")
    print("duplicate_metrics " + " ".join(f"{key}={value:.1f}" if key == "dup_pct" else f"{key}={value}" for key, value in metrics.items()))
    print(f"invariants ids_unique=yes source_ids_mapped={len(mapping)} hard_cap_ok=yes")

    if args.export_input is not None:
        changed = [row for row in candidate if row.embedding is None]
        with open(args.export_input, "w", encoding="utf-8") as out:
            for row in changed:
                json.dump(
                    {"id": row.id, "doc": row.doc, "article": row.article,
                     "status": row.status, "text": row.text},
                    out, ensure_ascii=False,
                )
                out.write("\n")
        print(f"exported_input_rows={len(changed)} path={args.export_input}")
        return

    if args.embeddings_file is not None:
        base.load_embeddings(candidate, str(args.embeddings_file))
        print(f"embeddings=validated rows={sum(row.embedding is not None for row in candidate)} path={args.embeddings_file}")
        if any(row.embedding is None for row in candidate):
            raise SystemExit("still rows without embeddings after loading file")

    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    vectors = vectors_for_candidate(candidate, args.batch_size, model)
    cases = load_cases(args.eval)
    proposed, rankings = score_recall(cases, candidate, mapping, vectors, args.batch_size, model)
    print_table(proposed, len(cases))
    for source_id in ("reg_03631", "reg_02543", "reg_00203"):
        case_index = next(index for index, case in enumerate(cases) if case["gold_id"] == source_id)
        print(f"recovery {source_id} mapped={mapping[source_id]} top10={','.join(rankings['ids'][case_index])}")

    if args.apply:
        with psycopg.connect(DSN) as conn:
            base.create_backup(conn, len(backup), reuse_existing=True)
            base.reload(conn, candidate)
        print("backup=chunks_backup reload=complete applied=True")
    else:
        print(f"db_write_check chunks={live_count} chunks_backup={len(backup)} (read-only connection; no DDL/DML) applied=False")


if __name__ == "__main__":
    main()
