#!/usr/bin/env python3
"""validate_eval.py — check integrity of retrieval eval sets.

Variables introduced:
  FILES     : list of (path, label) pairs for the two new eval sets
  LABEL     : short display name for each file
  BANK_LIKE : regex for lines that name a bank (excludes category headers)
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import psycopg

DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
FILES = [
    ("eval_generated.jsonl", "generated"),
    ("eval_handwritten.jsonl", "handwritten"),
]
RATE_TABLES_PATH = Path("rate_tables.jsonl")  # Required source for rate-question validation.
EXPECTED_RATE_TABLE_ROWS = 119  # Corpus row count after the corrected rate-table load.
# Lines that contain a bank name, not a category header
BANK_LIKE = re.compile(r"^Banka\s", re.IGNORECASE)


def _norm(text: str) -> str:
    """NFC-normalise and casefold for comparison."""
    return unicodedata.normalize("NFC", text).casefold()


def load_rate_texts() -> dict[str, str] | None:
    """Load the required rate-table texts, or report why validation cannot continue."""
    try:
        with RATE_TABLES_PATH.open(encoding="utf-8") as rate_file:
            rows = [json.loads(line) for line in rate_file]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL {RATE_TABLES_PATH}: unavailable or unreadable: {exc}")
        return None

    if len(rows) != EXPECTED_RATE_TABLE_ROWS:
        print(f"FAIL {RATE_TABLES_PATH}: expected {EXPECTED_RATE_TABLE_ROWS} rows, found {len(rows)}")
        return None
    try:
        return {f"rate_{i:04d}": row["text"] for i, row in enumerate(rows)}
    except (KeyError, TypeError) as exc:
        print(f"FAIL {RATE_TABLES_PATH}: malformed row data: {exc}")
        return None


def validate(path: str, label: str, rate_texts: dict[str, str]) -> int:
    """Return 0 on success, 1 on failure."""
    errors: list[str] = []

    if not Path(path).exists():
        errors.append(f"File not found: {path}")
        print(errors[-1])
        return 1

    entries = [json.loads(l) for l in open(path, encoding="utf-8")]

    if not entries:
        errors.append(f"{label}: empty file")
        print(errors[-1])
        return 1

    # Collect all gold_ids
    ids = [e["gold_id"] for e in entries]
    seen = set()
    for gid in ids:
        if gid in seen:
            errors.append(f"{label}: duplicate gold_id {gid}")
        seen.add(gid)

    # Load doc names from DB for reg dedup check
    conn = psycopg.connect(DSN)
    cur = conn.cursor()

    for e in entries:
        gid = e["gold_id"]
        # Existence
        cur.execute("SELECT status, doc FROM chunks WHERE id = %s", (gid,))
        row = cur.fetchone()
        if row is None:
            errors.append(f"{label}: gold_id {gid} not found in DB")
            continue

        status, doc = row

        # Status check
        if status not in ("canonical", "base"):
            errors.append(f"{label}: {gid} has status '{status}' (not canonical/base)")

        # Rate questions: check bank name presence
        if gid.startswith("rate_"):
            text = rate_texts.get(gid)
            if text is None:
                errors.append(f"{label}: {gid} has no matching row in {RATE_TABLES_PATH}")
                continue
            q_norm = _norm(e["question"])

            # Extract bank names from chunk text
            chunk_banks = []
            for line in text.split("\n"):
                if ":" not in line:
                    continue
                if line.startswith("Normat") or line.startswith("Rregullore"):
                    continue
                candidate = line.split(":")[0].strip()
                if _norm(candidate) in ("biznes i vogel", "kredi per shtepi/prona"):
                    continue
                chunk_banks.append(candidate)

            if chunk_banks:
                matched = any(
                    _norm(bank) in q_norm for bank in chunk_banks
                )
                if not matched:
                    errors.append(
                        f"{label}: {gid} — no bank name from question "
                        f"appears in chunk text; banks={chunk_banks[:3]}"
                    )

    # Handwritten-specific checks
    if label == "handwritten":
        # Exactly 20 rate, 20 reg
        rate_cnt = sum(1 for e in entries if e["gold_id"].startswith("rate_"))
        reg_cnt = sum(1 for e in entries if e["gold_id"].startswith("reg_"))
        if rate_cnt != 20:
            errors.append(f"{label}: expected 20 rate_ questions, got {rate_cnt}")
        if reg_cnt != 20:
            errors.append(f"{label}: expected 20 reg_ questions, got {reg_cnt}")

        # No more than 2 questions per source document
        doc_counts: dict[str, int] = {}
        for e in entries:
            if e["gold_id"].startswith("reg_"):
                cur.execute(
                    "SELECT doc FROM chunks WHERE id = %s", (e["gold_id"],)
                )
                row = cur.fetchone()
                if row:
                    doc_counts[row[0]] = doc_counts.get(row[0], 0) + 1
        for doc, cnt in doc_counts.items():
            if cnt > 2:
                errors.append(
                    f"{label}: doc '{doc[:60]}' has {cnt} questions (max 2)"
                )

    conn.close()

    if errors:
        print(f"FAIL {label}: {len(errors)} error(s)")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"PASS {label}: {len(entries)} questions, all checks ok")
    return 0


def main() -> None:
    rate_texts = load_rate_texts()
    if rate_texts is None:
        sys.exit(1)

    fails = 0
    for path, label in FILES:
        fails += validate(path, label, rate_texts)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()