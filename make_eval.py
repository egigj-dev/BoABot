# Build retrieval eval: question -> known-correct chunk id.
# rates : corrected rate chunks, ids assigned by enumeration (must match the
#         order used at embedding time — same file, same order)
# BANKS : names sampled from chunk text so each question names a real bank
import json, random

rates = [json.loads(l) for l in open("rate_tables.jsonl", encoding="utf-8")]
for i, r in enumerate(rates):
    r["id"] = f"rate_{i:04d}"

BANKS = ["Banka Credins", "Banka Kombëtare Tregtare", "Banka Raiffeisen",
         "Banka OTP Albania", "Banka Tirana", "Banka Union"]

random.seed(0)
out = []
for c in random.sample(rates, min(40, len(rates))):
    present = [b for b in BANKS if b in c["text"]] or BANKS
    bank = random.choice(present)
    item, cat = c["item"].strip(), c["category"].strip()
    q = (f"Sa është norma për {cat.lower()} me {item} në {bank}?"
         if item.startswith("maturitet")
         else f"Sa është {item.lower()} për {cat.lower()} në {bank}?")
    out.append({"question": q, "gold_id": c["id"], "gold_url": c["url"]})

with open("eval_retrieval.jsonl", "w", encoding="utf-8") as f:
    for e in out:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

print(f"{len(out)} questions written")
for e in out[:5]:
    print("  ", e["question"])