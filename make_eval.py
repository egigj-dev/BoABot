# make_eval.py — generate retrieval eval questions from rate chunks.
# Derives bank names from chunk text rather than a hardcoded list.
# Chunks whose bank name cannot be identified are skipped and resampled.
# Changed: BANKS_FROM_TEXT, skip-on-no-bank, output -> eval_generated.jsonl
import json, random, re

rates = [json.loads(l) for l in open("rate_tables.jsonl", encoding="utf-8")]
for i, r in enumerate(rates):
    r["id"] = f"rate_{i:04d}"

# Extract bank names from each chunk's text lines, filtering out category headers
_NON_BANK = {"biznes i vogel", "kredi per shtepi/prona"}

def _bank_names(text: str) -> list[str]:
    """Return bank names found in the chunk's data lines."""
    names = []
    for line in text.split("\n"):
        if ":" not in line:
            continue
        if line.startswith("Normat") or line.startswith("Rregullore"):
            continue
        candidate = line.split(":")[0].strip()
        if candidate.casefold() in _NON_BANK:
            continue
        names.append(candidate)
    return names

random.seed(0)
out = []
skipped = 0

# We know there are 119 rate chunks; we need 40 that name a known bank.
# Sample with replacement-safe logic: iterate shuffled indices.
indices = list(range(len(rates)))
random.shuffle(indices)

for idx in indices:
    if len(out) >= 40:
        break
    c = rates[idx]
    banks = _bank_names(c["text"])
    if not banks:
        skipped += 1
        continue
    bank = random.choice(banks)
    item, cat = c["item"].strip(), c["category"].strip()
    q = (f"Sa eshte norma per {cat.lower()} me {item} ne {bank}?"
         if item.lower().startswith("maturitet")
         else f"Sa eshte {item.lower()} per {cat.lower()} ne {bank}?")
    out.append({"question": q, "gold_id": c["id"], "gold_url": c["url"]})

with open("eval_generated.jsonl", "w", encoding="utf-8") as f:
    for e in out:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

print(f"{len(out)} questions written, {skipped} chunks skipped (unidentifiable bank)")
for e in out[:5]:
    print("  ", e["question"])