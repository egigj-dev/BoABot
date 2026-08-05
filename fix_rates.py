import json, re

BAND = re.compile(r"^\d+\s*-\s*\d+$")
rates = [json.loads(l) for l in open("rate_tables.jsonl", encoding="utf-8")]

fixed, out = 0, []
for r in rates:
    if r["item"].strip().lower() == "muaj":      # bare column header, no data
        continue
    if BAND.match(r["item"].strip()):
        r["item"] = f"maturitet {r['item'].strip()} muaj"
        r["text"] = f"{r['source']} — {r['category']} — {r['item']}\n" + \
                    r["text"].split("\n", 1)[1]
        fixed += 1
    out.append(r)

with open("rate_tables.jsonl", "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{fixed} relabelled, {len(rates)-len(out)} dropped, {len(out)} remain")