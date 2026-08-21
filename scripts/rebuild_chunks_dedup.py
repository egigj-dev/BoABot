#!/usr/bin/env python3
"""Rebuild article chunks while removing overlapping source-fragment text.

This is intentionally a separate entry point from ``rebuild_chunks.py``.  It
uses the same grouping, ID, cap, embedding, backup, and reload safeguards, but
joins verified article fragments by their whitespace-insensitive overlap.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict

import rebuild_chunks as base


STUB_FLOOR = 12
DSN = os.environ.get("BOABOT_DSN", "postgresql://boa:boa@127.0.0.1:5433/boa")


def normalized_with_positions(text: str) -> tuple[str, list[int]]:
    """Return whitespace-free comparison text and positions in the real text.

    The comparison representation deliberately changes *only* whitespace.
    Punctuation, case, accents, and the output text itself remain untouched.
    """
    chars: list[str] = []
    positions: list[int] = []
    for position, char in enumerate(text):
        if not char.isspace():
            chars.append(char)
            positions.append(position)
    return "".join(chars), positions


def longest_suffix_prefix(acc_normalized: str, fragment_normalized: str) -> int:
    """Find the longest suffix of ``acc`` that is a prefix of ``fragment``.

    KMP computes this in linear time, avoiding quadratic behaviour for long
    legal articles while comparing whitespace-insensitive representations.
    """
    if not acc_normalized or not fragment_normalized:
        return 0
    combined = fragment_normalized + "\0" + acc_normalized
    prefix = [0] * len(combined)
    for index in range(1, len(combined)):
        candidate = prefix[index - 1]
        while candidate and combined[index] != combined[candidate]:
            candidate = prefix[candidate - 1]
        if combined[index] == combined[candidate]:
            candidate += 1
        prefix[index] = candidate
    return min(prefix[-1], len(fragment_normalized), len(acc_normalized))


def combined_text(rows: list[base.SourceRow]) -> tuple[str, list[tuple[str, int, int]], int]:
    """Join source fragments, dropping each whitespace-tolerant tail/head overlap.

    Spans include zero-length entries for wholly-overlapped fragments so every
    source ID still participates in output-ID selection and source mapping.
    """
    parts = [rows[0].text]
    spans: list[tuple[str, int, int]] = [(rows[0].id, 0, len(rows[0].text))]
    position = len(rows[0].text)
    acc_normalized, _ = normalized_with_positions(rows[0].text)
    removed = 0

    fragments = [base.body_text(row.text) for row in rows[1:]]
    normalized_fragments = [normalized_with_positions(fragment) for fragment in fragments]
    for index, (row, fragment, (fragment_normalized, positions)) in enumerate(
        zip(rows[1:], fragments, normalized_fragments, strict=True)
    ):
        overlap = longest_suffix_prefix(acc_normalized, fragment_normalized)
        start = position
        append = fragment
        if overlap:
            removed += overlap
            if overlap == len(fragment_normalized):
                append = ""
            else:
                # Keep whitespace following the last overlapped character; it
                # belongs to the new, non-overlapping tail in the real text.
                append = fragment[positions[overlap - 1] + 1:]
                tail_normalized, _ = normalized_with_positions(append)
                next_normalized = (
                    normalized_fragments[index + 1][0]
                    if index + 1 < len(normalized_fragments)
                    else ""
                )
                if (0 < len(tail_normalized) < STUB_FLOOR
                        and next_normalized.startswith(tail_normalized)):
                    removed += len(tail_normalized)
                    append = ""
        elif fragment:
            parts.append("\n\n")
            position += 2
            start = position

        if append:
            parts.append(append)
            position += len(append)
            acc_normalized += normalized_with_positions(append)[0]
        spans.append((row.id, start, position))
    return "".join(parts), spans, removed


def ids_for_piece(
    spans: list[tuple[str, int, int]], start: int, end: int
) -> list[str]:
    """Include ordinary spans and boundary-only spans from dropped fragments."""
    return [
        source_id
        for source_id, left, right in spans
        if (right > start and left < end) or (left == right and start <= left <= end)
    ]


def build_output(
    rows: list[base.SourceRow],
) -> tuple[list[base.OutputRow], dict[str, str], dict[str, int]]:
    """Keep the base rebuild policy, replacing only verified-fragment joining."""
    grouped: dict[tuple[str | None, str | None, str | None], list[base.SourceRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.doc, row.article, row.status)].append(row)

    output: list[base.OutputRow] = []
    mapping: dict[str, str] = {}
    stats: defaultdict[str, int] = defaultdict(int)
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
                        if item.doc == row.doc and item.article == row.article and item.status == row.status
                        and base.normalized_prefix(item.text) == prefix
                    )
                    stats["ambiguous_deduped"] += 1
                    continue
                seen.add(prefix)
                output.append(base.OutputRow(**row.__dict__))
                mapping[row.id] = row.id
                stats["ambiguous_retained"] += 1
            continue

        text, spans, removed = combined_text(source_rows)
        stats["overlap_chars_removed"] += removed
        if len(text) <= base.MAX_CHARS:
            pieces = [(0, len(text))]
            stats["merged_groups"] += 1
        else:
            pieces = base.split_boundaries(text)
            stats["split_groups"] += 1
            stats["split_output_chunks"] += len(pieces)

        used: set[str] = set()
        for start, end in pieces:
            source_ids = ids_for_piece(spans, start, end)
            new_id = base.select_id(source_ids, used)
            used.add(new_id)
            output.append(base.OutputRow(
                id=new_id, doc=first.doc, article=first.article, status=first.status,
                section=first.section, url=first.url, text=text[start:end].strip(), embedding=None,
            ))
            for source_id in source_ids:
                mapping.setdefault(source_id, new_id)

    if len(mapping) != len(rows):
        missing = sorted({row.id for row in rows} - set(mapping))
        raise RuntimeError(f"unmapped source IDs: {missing[:5]}")
    if len({row.id for row in output}) != len(output):
        raise RuntimeError("rebuilt output IDs are not unique")
    if any(len(row.text) > base.MAX_CHARS for row in output):
        raise RuntimeError("rebuilt chunk exceeds the character limit")
    return output, mapping, dict(stats)


def residual_repeat_chars(text: str, min_len: int = 50) -> int:
    """Count whitespace-normalized chars covered by a repeated substring.

    Sliding-window fingerprint over the whitespace-free comparison text:
    every position inside a substring of length >= ``min_len`` that occurs at
    least twice in the chunk is counted as residual duplication. Note this
    includes legitimate repeated legal phrasing (e.g. the recurring
    definitional clause "është mundësia që banka të pësojë humbje financiare
    si rezultat ...") that also appears in untouched single-fragment source
    rows; it is an honest upper bound, not a join-only figure.
    """
    normalized, _ = normalized_with_positions(text)
    n = len(normalized)
    if n < min_len * 2:
        return 0
    seen: dict[str, int] = {}
    covered: set[int] = set()
    for start in range(0, n - min_len + 1):
        window = normalized[start:start + min_len]
        if window in seen:
            covered.update(range(start, start + min_len))
            covered.update(range(seen[window], seen[window] + min_len))
        else:
            seen[window] = start
    return len(covered)


def duplicate_metrics(rows: list[base.OutputRow], overlap_chars_removed: int) -> dict[str, int | float]:
    """Measure residual duplication in rows needing a fresh embedding.

    ``overlap_chars_removed`` is the count of characters the overlap-aware
    join stripped at fragment boundaries (real, accumulated during
    ``combined_text``). ``dup_chars``/``dup_pct``/``chunks_with_duplication``
    are measured with :func:`residual_repeat_chars` over the final chunk
    texts — not asserted. ``overlap_chars_removed`` is tracked separately so
    the join-induced savings are not conflated with residual repeats.
    """
    changed = [row for row in rows if row.embedding is None]
    total_chars = sum(len(row.text) for row in changed)
    dup_counts = [residual_repeat_chars(row.text) for row in changed]
    dup_chars = sum(dup_counts)
    chunks_with_duplication = sum(1 for count in dup_counts if count > 0)
    return {
        "total_chars": total_chars,
        "dup_chars": dup_chars,
        "dup_pct": (dup_chars / total_chars * 100) if total_chars else 0.0,
        "chunks_with_duplication": chunks_with_duplication,
        "overlap_chars_removed": overlap_chars_removed,
    }


def chunk_overlap_metrics(rows: list[base.SourceRow]) -> tuple[int, int, float]:
    """Return the pre/post join-overlap count for one verified source group."""
    text, _spans, removed = combined_text(rows)
    return len(text), removed, (removed / len(text) * 100 if text else 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="validate and report without DB writes")
    parser.add_argument("--reuse-existing-backup", action="store_true",
                        help="use a verified pre-existing chunks_backup without overwriting it")
    parser.add_argument("--batch-size", type=int, default=4,
                        help="bge-m3 encoding batch size (default: 4 for CPU long-context safety)")
    parser.add_argument("--export-input", metavar="PATH",
                        help="write changed rows as JSONL for external embedding, then exit")
    parser.add_argument("--embeddings-file", metavar="PATH",
                        help="load JSONL embeddings for changed rows instead of CPU encoding")
    parser.add_argument("--stats", action="store_true",
                        help="print duplicate-character metrics (also printed for dry runs)")
    args = parser.parse_args()
    if args.export_input and args.embeddings_file:
        parser.error("--export-input and --embeddings-file cannot be used together")

    with base.psycopg.connect(DSN) as conn:
        source = base.fetch_rows(conn)
        rebuilt, mapping, stats = build_output(source)
        changed_rows = sum(row.embedding is None for row in rebuilt)
        metrics = duplicate_metrics(rebuilt, stats.get("overlap_chars_removed", 0))
        print(f"source_rows={len(source)} output_rows={len(rebuilt)} reduced_by={len(source) - len(rebuilt)}")
        print(" ".join(f"{key}={value}" for key, value in sorted(stats.items())))
        print(f"mapping_rows={len(mapping)} changed_rows={changed_rows}")
        print(
            "duplicate_metrics " + " ".join(
                f"{key}={value:.1f}" if key == "dup_pct" else f"{key}={value}"
                for key, value in metrics.items()
            )
        )
        if args.export_input:
            print(f"exported_rows={base.export_input(rebuilt, args.export_input)} path={args.export_input}")
            return
        if args.embeddings_file:
            base.load_embeddings(rebuilt, args.embeddings_file)
            print(f"embeddings=validated rows={changed_rows} path={args.embeddings_file}")
        if args.dry_run:
            return
        if args.batch_size < 1:
            raise ValueError("--batch-size must be positive")
        # The existing backup is rollback data and must never be overwritten.
        base.create_backup(conn, len(source), reuse_existing=True)
        if not args.embeddings_file:
            base.embed_changed(rebuilt, args.batch_size)
        base.reload(conn, rebuilt)
        print("backup=chunks_backup reload=complete")


if __name__ == "__main__":
    main()
