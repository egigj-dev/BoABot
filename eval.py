# eval.py — recall@k against the 40 generated questions.
# hit_at : {k: count} of questions whose gold chunk appeared in top-k
# misses : questions where gold never appeared, for error analysis
import json, time, statistics
from retrieve import retrieve

evals = [json.loads(l) for l in open("eval_retrieval.jsonl", encoding="utf-8")]
KS = (1, 3, 5, 10)
hit_at = {k: 0 for k in KS}
misses, lats = [], []

for e in evals:
    t = time.time()
    hits = retrieve(e["question"], k=max(KS))
    lats.append(time.time() - t)
    ids = [h["id"] for h in hits]
    rank = ids.index(e["gold_id"]) + 1 if e["gold_id"] in ids else None
    for k in KS:
        if rank and rank <= k:
            hit_at[k] += 1
    if not rank:
        misses.append((e["question"], ids[:3]))

n = len(evals)
for k in KS:
    print(f"recall@{k}: {hit_at[k]/n:.3f}  ({hit_at[k]}/{n})")
print(f"\nlatency: median {statistics.median(lats)*1000:.0f}ms  "
      f"p95 {sorted(lats)[int(.95*n)]*1000:.0f}ms")

print(f"\n{len(misses)} misses:")
for q, got in misses[:8]:
    print(f"  Q: {q[:80]}")
    print(f"     got: {got}\n")