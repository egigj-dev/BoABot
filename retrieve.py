# retrieve.py — the one function everything else calls.
# Same bge-m3 that produced the index; normalized so <=> is cosine distance.
import psycopg, numpy as np
from sentence_transformers import SentenceTransformer

DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
LIVE = ("canonical", "base")          # excludes amendment + superseded
_model = None

def model():
    global _model
    if _model is None:
        _model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    return _model

def retrieve(query: str, k: int = 5, statuses=LIVE):
    """Top-k chunks for a query. Returns [{id, doc, article, url, text, score}]."""
    v = model().encode([query], normalize_embeddings=True)[0]
    vs = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
    sql = """SELECT id, doc, article, url, text, 1 - (embedding <=> %s::vector) AS score
             FROM chunks WHERE status = ANY(%s)
             ORDER BY embedding <=> %s::vector LIMIT %s"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, (vs, list(statuses), vs, k))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

if __name__ == "__main__":
    import time
    q = "Sa është komisioni për shlyerje të parakohshme të kredisë në Banka Credins?"
    t = time.time(); hits = retrieve(q); el = time.time() - t
    print(f"{el*1000:.0f}ms\n")
    for h in hits:
        print(f"  {h['score']:.3f}  {h['id']:10s} {h['doc'][:50]}")
        print(f"          {h['text'][:110].replace(chr(10), ' ')}\n")