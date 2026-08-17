"""Corpus-derived phrase/entity adaptation for Schema 1 §5."""

from __future__ import annotations

import json
import re
from pathlib import Path

BANK_RE = re.compile(r"^(Banka[^:]{2,100}):", re.MULTILINE)
FIXED_TERMS = ("ALL", "EUR", "USD", "PIN", "CVV", "CVC", "OTP", "Banka e Shqipërisë")


def build_phrase_list(rate_tables: str | Path | None = None) -> tuple[str, ...]:
    """Derive names from versioned corpus data, never from evaluation fixtures."""
    path = Path(rate_tables) if rate_tables else Path(__file__).resolve().parents[1] / "rate_tables.jsonl"
    phrases = set(FIXED_TERMS)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for key in ("source", "category", "item"):
                value = str(row.get(key) or "").strip()
                if value:
                    phrases.add(value)
            phrases.update(match.group(1).strip() for match in BANK_RE.finditer(str(row.get("text") or "")))
    return tuple(sorted(phrases, key=str.casefold))
