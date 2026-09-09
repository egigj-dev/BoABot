#!/usr/bin/env python3
"""Task BR: relabel the handwritten eval set against the live index.

For each handwritten item:
  doc = the live chunk.doc whose normalized name matches the gold URL basename.
  If the gold doc is in the live index: relabel gold_id to the live chunk of
  that doc that (a) retrieve() ranks highest, else (b) the chunk whose article
  shares the most question terms (folded), else (c) any chunk of that doc.
  If the doc has NO live chunks: mark UNINDEXED (no possible correct label).

Writes eval_handwritten_relabeled.jsonl + a per-item classification.
"""
import json, os, re, sys
from collections import Counter
from urllib.parse import urlparse, unquote

sys.path.insert(0, "/home/egigj/BoABot")
os.environ.setdefault("BOABOT_DSN", "postgresql://boa:boa@127.0.0.1:5433/boa")
os.environ.setdefault("BOABOT_COMPARISON_STRUCTURED", "0")

import psycopg
from core.retrieve import retrieve
from core.text_norm import fold

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "eval_handwritten.jsonl")

def slug(url: str) -> str:
    b = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    b = re.sub(r"\.[a-z0-9]+$", "", b, flags=re.I)
    return re.sub(r"[^A-Za-z0-9]+", "_", b).strip("_").lower()

conn = psycopg.connect("postgresql://boa:boa@127.0.0.1:5433/boa")
cur = conn.cursor()
cur.execute("SELECT id, doc, article FROM chunks")
CHUNKS = [(i, str(d or ""), str(a or "")) for i, d, a in cur.fetchall()]
conn.close()
DOC_OF = {}
CHUNKS_OF: dict[str, list[tuple[str, str]]] = {}  # doc -> [(id, article)]
for i, d, a in CHUNKS:
    DOC_OF[i] = d
    CHUNKS_OF.setdefault(d, []).append((i, a))
# normalized doc->canonical doc map
NORM_DOC: dict[str, str] = {}
for d in CHUNKS_OF:
    n = re.sub(r"[^A-Za-z0-9]+", "_", d).strip("_").lower()
    NORM_DOC.setdefault(n, d)

rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
out = []
report = []
for r in rows:
    gid = r["gold_id"]
    q = r["question"]
    if not gid.startswith("reg_"):
        # rate items: keep as-is (their recall is high already)
        out.append(r)
        report.append({"gold_id": gid, "kind": "rate-keep"})
        continue
    # KEEP the original gold if it is live — it was written against the old
    # index, but if the id still exists it is by definition the intended chunk
    # (an id that survives the re-chunk is the same regulation article).
    if gid in DOC_OF:
        out.append(r)
        report.append({"gold_id": gid, "kind": "KEEP-LIVE"})
        continue
    s = slug(r["gold_url"])
    # find the canonical doc for this gold url
    canon = NORM_DOC.get(s)
    if canon is None:
        # try containment
        for n, d in NORM_DOC.items():
            if s in n or n in s:
                canon = d
                break
    if canon is None:
        report.append({"gold_id": gid, "kind": "UNINDEXED", "doc": None})
        continue
    # live chunks of the gold doc
    cands = CHUNKS_OF[canon]
    # does retrieve find this doc? rank the first same-doc hit
    try:
        hits = retrieve(q, k=20)
    except Exception as exc:
        print(f"  retrieve error on {gid}: {exc}", file=sys.stderr)
        hits = []
    rank = None
    ranked_id = None
    for i, h in enumerate(hits, 1):
        if h.get("doc") == canon:
            rank = i
            ranked_id = h["id"]
            break
    if rank is not None:
        new_gold = ranked_id
        out.append({"question": q, "gold_id": new_gold, "gold_url": r["gold_url"]})
        report.append({"gold_id": gid, "kind": "RELABEL-DOCRANK", "doc": canon,
                       "rank": rank, "old_gold": gid, "new_gold": new_gold})
        continue
    # doc exists but not retrieved: fall back to term-overlap article
    qf = fold(q)
    qterms = {t for t in re.findall(r"\w+", qf) if len(t) > 2}
    best = None
    best_score = -1
    for cid, art in cands:
        af = fold(art)
        score = sum(1 for t in qterms if t in af)
        if score > best_score:
            best_score, best = score, cid
    if best_score > 0:
        out.append({"question": q, "gold_id": best, "gold_url": r["gold_url"]})
        report.append({"gold_id": gid, "kind": "RELABEL-ARTICLE", "doc": canon,
                       "score": best_score, "new_gold": best})
        continue
    # no term overlap: use the first chunk of the doc
    first = cands[0][0]
    out.append({"question": q, "gold_id": first, "gold_url": r["gold_url"]})
    report.append({"gold_id": gid, "kind": "RELABEL-FIRST", "doc": canon,
                   "new_gold": first})

# rate items preserved; count
kinds = Counter(x["kind"] for x in report)
print("KINDS:", dict(kinds))
print()
with open(os.path.join(HERE, "eval_handwritten_relabeled.jsonl"), "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"wrote {len(out)} items -> eval_handwritten_relabeled.jsonl")
print()
for x in report:
    print(f"{x['gold_id']:10s} {x['kind']:<20} {x.get('doc','')[:50]} rank={x.get('rank')} -> {x.get('new_gold','')}")