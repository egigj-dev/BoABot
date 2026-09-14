#!/usr/bin/env python3
"""inspect_rates.py — read-only human-readable map of rate_tables.jsonl.

Prints, per category, a plain-text summary of what the corpus actually holds:
row count / segment / currency / source table, whether each row's text parses
as ``Bank: value`` pairs (flagging every row whose value labels are not known
bank names), which licensed banks appear / are absent, the distinct maturity
bands, and a one-line "answerable?" verdict (does the category carry
bank-attributed values for its metric?).

Read-only: never writes, cleans, or modifies the data. Stdlib only (json) —
runs anywhere without the DB, the server, or a provider key.

Conventions mirrored from core/comparison.py so ids line up with the running
system:
  * row _id is ``rate_%04d`` with index = 0-based line number in the file
    (comparison._rate_rows()).
  * a "value line" is text[1:] (first line = header) matching
    ``^\\s*([^:\\n]+?)\\s*:\\s*[-+]?\\d`` (comparison._BANK_ROW_RE).
  * the licensed-bank vocabulary mirrors core/institutions.py
    LICENSED_INSTITUTIONS (canonical names + aliases; fold()ed comparison).
    Keep in sync if the register changes.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path

# ---------------------------------------------------------------- mirrors ----
# core/institutions.py LICENSED_INSTITUTIONS — canonical names + aliases.
LICENSED_INSTITUTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Banka Amerikane e Investimeve Shqiperi", ("BAI", "AIB", "Banka Amerikane e Investimeve")),
    ("Banka Credins", ("BC", "Credins")),
    ("Banka e Bashkuar e Shqipërisë", ("BBSH", "Banka e Bashkuar")),
    ("Banka e Parë e Investimeve Albania", ("BPI", "Banka e Pare e Investimeve Albania")),
    ("Banka Intesa SanPaolo e Shqipërisë", ("BIS", "Intesa", "Intesa Sanpaolo")),
    ("Banka Jet", ("JET", "Jet")),
    ("Banka Kombëtare Tregtare", ("BKT",)),
    ("Banka OTP Albania", ("OTP",)),
    ("Banka Procredit", ("BPC", "ProCredit")),
    ("Banka Raiffeisen", ("BR", "Raiffeisen")),
    ("Banka Tirana", ("BT", "Tirana")),
    ("Banka Union", ("BU", "Union")),
    ("Dega e Bankës Turkiye Cumhuriyeti Ziraat Bankasi A.S., Albania", ("Ziraat",)),
)
# core/comparison.py _BANK_ROW_RE (value starts with optional sign + digit).
VALUE_LINE_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*[-+]?\d")
# Maturity bands as typed by _row_slots(): "N-M muaj" ranges and single terms
# like "1 mujor" / "12 mujore" (deposit table).
BAND_RANGE_RE = re.compile(r"\b(\d{1,3})\s*-\s*(\d{1,3})\s+(?:muaj|mujore|muajsh)\b", re.I)
BAND_SINGLE_RE = re.compile(r"\b(\d{1,3})\s*(?:-|–)?\s*(?:muaj|mujor|mujore|mujore?s?|muajsh)(?:\b|s)", re.I)


def fold(text: str) -> str:
    """Diacritic-insensitive lowercase (core.text_norm.fold equivalent)."""
    text = text.lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def _bank_forms() -> dict[str, str]:
    """folded form -> canonical display name for every licensed institution."""
    forms: dict[str, str] = {}
    for canonical, aliases in LICENSED_INSTITUTIONS:
        for value in (canonical, *aliases):
            forms.setdefault(fold(value), canonical)
    return forms


def _metric_of(row: dict) -> str | None:
    """Mirror of comparison._row_slots()'s metric inference from source/item."""
    source = fold(str(row.get("source") or ""))
    item = fold(str(row.get("item") or ""))
    if "penalitet" in item or "penalizues" in item:
        return "penalty"
    if "normat" in source or "interesit" in source:
        return "interest_rate"
    if "komision" in source:
        return "fee"
    return None


def value_pairs(row: dict) -> list[tuple[str, str]]:
    """(label, value) pairs from text[1:]; first line is the header."""
    pairs: list[tuple[str, str]] = []
    for line in str(row.get("text") or "").splitlines()[1:]:
        match = VALUE_LINE_RE.match(line)
        if not match:
            continue
        label = match.group(1).strip()
        # Mirror render_rate_answer(): the value begins after the colon.
        value = (line[match.end(1) + 1:] if line[match.end(1):].startswith(":")
                 else line[match.end(1):]).strip()
        pairs.append((label, value))
    return pairs


def bands_of(row: dict) -> list[tuple[int, ...]]:
    item = str(row.get("item") or "")
    bands: list[tuple[int, ...]] = []
    for match in BAND_RANGE_RE.finditer(item):
        bands.append((int(match.group(1)), int(match.group(2))))
    range_ints = {value for band in bands for value in band}
    for match in BAND_SINGLE_RE.finditer(item):
        number = int(match.group(1))
        if number not in range_ints:
            bands.append((number,))
    seen: set[tuple[int, ...]] = set()
    return [band for band in bands if not (band in seen or seen.add(band))]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    rate_path = repo_root / "rate_tables.jsonl"
    if not rate_path.exists():
        print(f"rate table not found: {rate_path}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for index, line in enumerate(rate_path.open(encoding="utf-8")):
        if not line.strip():
            continue
        row = json.loads(line)
        row["_id"] = f"rate_{index:04d}"
        rows.append(row)

    bank_forms = _bank_forms()

    # Group preserving corpus order (mirrors CATEGORY_LABELS discovery order).
    categories: OrderedDict[str, list[dict]] = OrderedDict()
    for row in rows:
        categories.setdefault(str(row.get("category") or "(no category)"), []).append(row)

    flagged: list[tuple[str, str, str, list[tuple[str, str, bool, bool]]]] = []
    # (category, rid, item, [(line, label, value, is_bank, is_numeric)])

    verdicts: list[tuple[str, bool, str]] = []  # (category, answerable, note)

    for category, group in categories.items():
        print("=" * 78)
        print(f"CATEGORY: {category}    rows: {len(group)}")
        print("-" * 78)

        # 1. row count / segment / currency / source tables
        segments = Counter(str(r.get("customer_segment") or "?") for r in group)
        currencies = Counter(str(r.get("currency") or "?") for r in group)
        sources = Counter(str(r.get("source") or "?") for r in group)
        metrics = Counter((_metric_of(r) or "?") for r in group)
        print(f"  customer_segment : {', '.join(f'{k} ({v})' for k, v in segments.items())}")
        print(f"  currency         : {', '.join(f'{k} ({v})' for k, v in currencies.items())}")
        print(f"  source tables    : {', '.join(f'{k} ({v})' for k, v in sources.items())}")
        print(f"  metric (typed)   : {', '.join(f'{k} ({v})' for k, v in metrics.items() if k != '?') or '(none inferred)'}")

        # 2. value-label parse: every row's value labels must be known banks
        bad_rows: list[tuple[str, str, list[tuple[str, str, bool, bool]]]] = []
        for row in group:
            pairs = value_pairs(row)
            problems: list[tuple[str, str, bool, bool]] = []
            if not pairs:
                problems.append(("(no value lines)", "", False, False))
            for label, value in pairs:
                is_bank = fold(label) in bank_forms
                # numeric check: after optional sign, the value begins with a digit
                # (mirrors the parser admission rule); flag non-numeric tails.
                m = re.match(r"^[-+]?\d", value.lstrip())
                is_numeric = bool(m)
                if not is_bank or not is_numeric:
                    problems.append((label, value, is_bank, is_numeric))
            if problems:
                bad_rows.append((row["_id"], row.get("item"), problems))
        if bad_rows:
            print(f"  value-labels     : {len(group) - len(bad_rows)}/{len(group)} rows parse as Bank: value; "
                  f"{len(bad_rows)} FLAGGED — non-bank or non-numeric value labels:")
            for rid, item, problems in bad_rows:
                detail = "; ".join(
                    f"{label + ':' if label else ''} {value or ''} [{'BANK' if is_bank else 'non-bank'}"
                    f"{'' if is_numeric else ', non-numeric'}]"
                    for label, value, is_bank, is_numeric in problems
                )
                print(f"    FLAG {rid}  item={item!r}  {detail}")
        else:
            print(f"  value-labels     : {len(group)}/{len(group)} rows parse as Bank: value; 0 flagged")
        bad_rows = [(rid, str(item), problems) for rid, item, problems in bad_rows]
        flagged.extend((category, rid, item, problems) for rid, item, problems in bad_rows)

        # 3. banks present / absent
        present: set[str] = set()
        for row in group:
            for label, _value in value_pairs(row):
                canonical = bank_forms.get(fold(label))
                if canonical:
                    present.add(canonical)
        absent = [c for c, _a in LICENSED_INSTITUTIONS if c not in present]
        if present:
            print(f"  banks present    : {len(present)}/{len(LICENSED_INSTITUTIONS)}: "
                  f"{'; '.join(sorted(present))}")
        else:
            print(f"  banks present    : NONE (0/{len(LICENSED_INSTITUTIONS)})")
        if absent:
            print(f"  banks absent     : {len(absent)}/{len(LICENSED_INSTITUTIONS)}: "
                  f"{'; '.join(absent)}")

        # 4. maturity bands / terms, sorted (visible gaps)
        all_bands: set[tuple[int, ...]] = set()
        for row in group:
            all_bands.update(bands_of(row))
        if all_bands:
            sorted_bands = sorted(all_bands, key=lambda b: (b[0], b[-1]))
            rendered_bands = [
                f"{band[0]}-{band[1]} muaj" if len(band) == 2 else f"{band[0]} muaj"
                for band in sorted_bands
            ]
            print(f"  maturity bands   : {', '.join(rendered_bands)}")
        else:
            print(f"  maturity bands   : (none in item field)")

        # 5. answerable? verdict
        has_bank_attr = any(
            fold(label) in bank_forms for row in group for label, _v in value_pairs(row)
        )
        has_numeric = any(value_pairs(row) for row in group)
        typed_metrics = sorted({m for m in metrics if m != "?"})
        if has_bank_attr:
            answerable = True
            note = f"bank-attributed values for metric(s): {', '.join(typed_metrics) or '?'}"
        elif has_numeric:
            answerable = False
            note = ("numeric values exist but are labeled by product/size, "
                    "NOT by bank — per-bank questions cannot be answered")
        else:
            answerable = False
            note = "rows carry no numeric Bank: value pairs at all"
        verdicts.append((category, answerable, note))
        print(f"  ANSWERABLE?      : {'YES — ' + note if answerable else 'NO — ' + note}")

    # ---- summary ----
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("-" * 78)
    yes = [c for c, a, _n in verdicts if a]
    no = [c for c, a, _n in verdicts if not a]
    print(f"  answerable categories   : {len(yes)} ({', '.join(yes)})")
    print(f"  unanswerable categories : {len(no)} ({', '.join(no)})")
    print(f"  total rows              : {len(rows)}; flagged rows: {len(flagged)}")
    if flagged:
        print("\n  ALL FLAGGED ROWS (value labels that are not known bank names, or no value lines):")
        for category, rid, item, problems in flagged:
            short = "; ".join(
                f"{label + ':' if label else ''} {value or ''} [{'BANK' if is_bank else 'non-bank'}"
                f"{'' if is_numeric else ', non-numeric'}]"
                for label, value, is_bank, is_numeric in problems
            )
            print(f"    {rid}  {category}  item={item!r}  {short}")
    return 0


if __name__ == "__main__":
    sys.exit(main())