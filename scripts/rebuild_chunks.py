#!/usr/bin/env python3
"""Rebuild verified article chunks from the live PostgreSQL source.

Only groups with one raw body heading that agrees with their ``article`` metadata
are coalesced.  Ambiguous labels are retained as individual fragments; exact
normalized-prefix duplicates there are removed.  The source table is backed up
before the transactional reload and unchanged rows retain their existing vector.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import psycopg
from sentence_transformers import SentenceTransformer


DSN = os.environ.get("BOABOT_DSN", "postgresql://127.0.0.1:5433/boa")
MAX_CHARS = 12_000
ARTICLE_HEADING = re.compile(r"^Neni\s+(\d+(?:/\d+)?)\b", re.MULTILINE | re.IGNORECASE)
SECTION_HEADING = re.compile(
    r"^(?:KREU|NËNKREU|ANEKSI|SHTOJCA|PJESA)\b|^[A-ZÇË][A-ZÇË \-]{7,}$",
    re.MULTILINE,
)
HEADER = re.compile(r"^[^\n]*\s—\sNeni\s+[^\n]+\n?")
NORMALIZE = re.compile(r"\W+", re.UNICODE)

# These preserve every handwritten fixture ID after its intentional pin repair.
PREFERRED_IDS = {
    "reg_00053", "reg_00087", "reg_00128", "reg_00203", "reg_00380",
    "reg_00446", "reg_00531", "reg_00538", "reg_00662", "reg_00990",
    "reg_01110", "reg_01238", "reg_01869", "reg_01891", "reg_02157",
    "reg_02543", "reg_03181", "reg_03472", "reg_03631", "reg_01310",
}


@dataclass(frozen=True)
class SourceRow:
    id: str
    doc: str | None
    article: str | None
    status: str | None
    section: str | None
    url: str | None
    text: str
    embedding: str


@dataclass
class OutputRow:
    id: str
    doc: str | None
    article: str | None
    status: str | None
    section: str | None
    url: str | None
    text: str
    embedding: str | None


def body_text(text: str) -> str:
    """Remove only the repeated extraction header from a continuation fragment."""
    return HEADER.sub("", text, count=1)


def raw_headings(rows: list[SourceRow]) -> set[str]:
    return {
        match.group(1)
        for row in rows
        for match in ARTICLE_HEADING.finditer(body_text(row.text))
    }


def verified_article(rows: list[SourceRow]) -> bool:
    article = rows[0].article
    headings = raw_headings(rows)
    return article is not None and headings == {article}


def normalized_prefix(text: str) -> str:
    return NORMALIZE.sub("", body_text(text).casefold())[:300]


def combined_text(rows: list[SourceRow]) -> tuple[str, list[tuple[str, int, int]]]:
    """Join fragments in source-ID order, recording source character spans."""
    parts: list[str] = []
    spans: list[tuple[str, int, int]] = []
    position = 0
    for index, row in enumerate(rows):
        part = row.text if index == 0 else body_text(row.text)
        if index:
            parts.append("\n\n")
            position += 2
        start = position
        parts.append(part)
        position += len(part)
        spans.append((row.id, start, position))
    return "".join(parts), spans


def split_boundaries(text: str) -> list[tuple[int, int]]:
    """Prefer extractable section starts, then a newline/space below MAX_CHARS."""
    starts = [match.start() for match in SECTION_HEADING.finditer(text)]
    pieces: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > MAX_CHARS:
        limit = start + MAX_CHARS
        headings = [point for point in starts if start + MAX_CHARS // 2 <= point <= limit]
        if headings:
            end = headings[-1]
        else:
            end = text.rfind("\n", start + MAX_CHARS // 2, limit + 1)
            if end < start + MAX_CHARS // 2:
                end = text.rfind(" ", start + MAX_CHARS // 2, limit + 1)
            if end < start + MAX_CHARS // 2:
                end = limit
        pieces.append((start, end))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    if start < len(text):
        pieces.append((start, len(text)))
    return pieces


def select_id(source_ids: list[str], used: set[str]) -> str:
    for source_id in source_ids:
        if source_id in PREFERRED_IDS and source_id not in used:
            return source_id
    for source_id in source_ids:
        if source_id not in used:
            return source_id
    raise RuntimeError("not enough source IDs to name rebuilt chunks")


def build_output(rows: list[SourceRow]) -> tuple[list[OutputRow], dict[str, str], dict[str, int]]:
    grouped: dict[tuple[str | None, str | None, str | None], list[SourceRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.doc, row.article, row.status)].append(row)

    output: list[OutputRow] = []
    mapping: dict[str, str] = {}
    stats = defaultdict(int)
    for source_rows in grouped.values():
        source_rows.sort(key=lambda row: row.id)
        first = source_rows[0]
        if len(source_rows) == 1:
            row = first
            output.append(OutputRow(**row.__dict__))
            mapping[row.id] = row.id
            stats["unchanged"] += 1
            continue

        if not verified_article(source_rows):
            seen: set[str] = set()
            for row in source_rows:
                prefix = normalized_prefix(row.text)
                if prefix in seen:
                    mapping[row.id] = next(
                        item.id for item in output
                        if item.doc == row.doc and item.article == row.article and item.status == row.status
                        and normalized_prefix(item.text) == prefix
                    )
                    stats["ambiguous_deduped"] += 1
                    continue
                seen.add(prefix)
                output.append(OutputRow(**row.__dict__))
                mapping[row.id] = row.id
                stats["ambiguous_retained"] += 1
            continue

        text, spans = combined_text(source_rows)
        if len(text) <= MAX_CHARS:
            pieces = [(0, len(text))]
            stats["merged_groups"] += 1
        else:
            pieces = split_boundaries(text)
            stats["split_groups"] += 1
            stats["split_output_chunks"] += len(pieces)

        used: set[str] = set()
        for start, end in pieces:
            source_ids = [source_id for source_id, left, right in spans if right > start and left < end]
            new_id = select_id(source_ids, used)
            used.add(new_id)
            output.append(OutputRow(
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
    if any(len(row.text) > MAX_CHARS for row in output):
        raise RuntimeError("rebuilt chunk exceeds the character limit")
    return output, mapping, dict(stats)


def embed_changed(rows: list[OutputRow], batch_size: int) -> None:
    changed = [row for row in rows if row.embedding is None]
    if not changed:
        return
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    vectors = model.encode(
        [row.text for row in changed],
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    for row, vector in zip(changed, vectors, strict=True):
        row.embedding = "[" + ",".join(f"{value:.6f}" for value in np.asarray(vector, dtype=np.float32)) + "]"


def export_input(rows: list[OutputRow], path: str) -> int:
    """Write the final text for only rows that require fresh embeddings."""
    changed = [row for row in rows if row.embedding is None]
    with open(path, "w", encoding="utf-8") as output:
        for row in changed:
            json.dump(
                {
                    "id": row.id,
                    "doc": row.doc,
                    "article": row.article,
                    "status": row.status,
                    "text": row.text,
                },
                output,
                ensure_ascii=False,
            )
            output.write("\n")
    return len(changed)


def load_embeddings(rows: list[OutputRow], path: str) -> None:
    """Assign supplied Colab vectors to changed rows using the normal DB format."""
    changed = {row.id: row for row in rows if row.embedding is None}
    loaded: dict[str, np.ndarray] = {}
    with open(path, encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                row_id = item["id"]
                vector = np.asarray(item["embedding"], dtype=np.float32)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid embeddings JSONL at line {line_number}: {error}") from error
            if row_id in loaded:
                raise ValueError(f"duplicate embedding for changed row id: {row_id}")
            if vector.shape != (1024,):
                raise ValueError(
                    f"embedding for {row_id} has shape {vector.shape}; expected 1024 floats"
                )
            loaded[row_id] = vector

    missing = sorted(set(changed) - set(loaded))
    if missing:
        raise ValueError(f"missing embeddings for changed row ids: {missing}")
    unexpected = sorted(set(loaded) - set(changed))
    if unexpected:
        raise ValueError(f"embeddings file has ids that are not changed rows: {unexpected}")
    for row_id, row in changed.items():
        row.embedding = "[" + ",".join(f"{value:.6f}" for value in loaded[row_id]) + "]"


def fetch_rows(conn: psycopg.Connection) -> list[SourceRow]:
    with conn.cursor() as cur:
        cur.execute("""SELECT id, doc, article, status, section, url, text, embedding::text
                       FROM chunks ORDER BY id""")
        return [SourceRow(*row) for row in cur.fetchall()]


def create_backup(conn: psycopg.Connection, expected_count: int, reuse_existing: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.chunks_backup')")
        exists = cur.fetchone()[0] is not None
        if exists and not reuse_existing:
            raise RuntimeError("chunks_backup already exists; refusing to overwrite rollback data")
        if not exists:
            cur.execute("CREATE TABLE chunks_backup AS SELECT * FROM chunks")
        cur.execute("SELECT count(*) FROM chunks_backup")
        backup_count = cur.fetchone()[0]
    conn.commit()
    if backup_count != expected_count:
        raise RuntimeError(f"backup count {backup_count} does not match source count {expected_count}")


def reload(conn: psycopg.Connection, rows: list[OutputRow]) -> None:
    values = [
        (row.id, row.doc, row.article, row.status, row.section, row.url, row.text, row.embedding)
        for row in rows
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM chunks")
        cur.executemany(
            """INSERT INTO chunks (id, doc, article, status, section, url, text, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)""",
            values,
        )
        cur.execute("ANALYZE chunks")
        cur.execute("SELECT count(*) FROM chunks")
        if cur.fetchone()[0] != len(rows):
            raise RuntimeError("reload count verification failed")


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
    args = parser.parse_args()
    if args.export_input and args.embeddings_file:
        parser.error("--export-input and --embeddings-file cannot be used together")
    with psycopg.connect(DSN) as conn:
        source = fetch_rows(conn)
        rebuilt, mapping, stats = build_output(source)
        changed_rows = sum(row.embedding is None for row in rebuilt)
        print(f"source_rows={len(source)} output_rows={len(rebuilt)} reduced_by={len(source) - len(rebuilt)}")
        print(" ".join(f"{key}={value}" for key, value in sorted(stats.items())))
        print(f"mapping_rows={len(mapping)} changed_rows={changed_rows}")
        if args.export_input:
            print(f"exported_rows={export_input(rebuilt, args.export_input)} path={args.export_input}")
            return
        if args.embeddings_file:
            load_embeddings(rebuilt, args.embeddings_file)
            print(f"embeddings=validated rows={changed_rows} path={args.embeddings_file}")
        if args.dry_run:
            return
        if args.batch_size < 1:
            raise ValueError("--batch-size must be positive")
        create_backup(conn, len(source), args.reuse_existing_backup)
        if args.embeddings_file:
            pass
        else:
            embed_changed(rebuilt, args.batch_size)
        reload(conn, rebuilt)
        print("backup=chunks_backup reload=complete")


if __name__ == "__main__":
    main()
