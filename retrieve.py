# retrieve.py — the one function everything else calls.
# Same bge-m3 that produced the index; normalized so <=> is cosine distance.
import logging
import threading

import numpy as np
from psycopg_pool import ConnectionPool
from sentence_transformers import SentenceTransformer

DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
LIVE = ("canonical", "base")          # excludes amendment + superseded
_model = None
_pool = ConnectionPool(DSN, min_size=1, max_size=4, open=False, name="retrieval")
_pool_lock = threading.Lock()
_stats_lock = threading.Lock()
_reuse_hits = 0
_reuse_misses = 0
logger = logging.getLogger(__name__)

def model():
    global _model
    if _model is None:
        _model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    return _model


def pool():
    """Return the process-wide pool, opening its first connection exactly once."""
    if _pool.closed:
        with _pool_lock:
            if _pool.closed:
                _pool.open(wait=True)
    return _pool


def reset_embedding_stats():
    global _reuse_hits, _reuse_misses
    with _stats_lock:
        _reuse_hits = _reuse_misses = 0


def embedding_stats():
    with _stats_lock:
        return {"hits": _reuse_hits, "misses": _reuse_misses}


def _record_embedding_reuse(reused):
    global _reuse_hits, _reuse_misses
    with _stats_lock:
        if reused:
            _reuse_hits += 1
        else:
            _reuse_misses += 1


def warmup():
    """Load bge-m3, encode once, open the pool, and execute one pgvector query."""
    retrieve("ngrohje e sherbimit", k=1)
    reset_embedding_stats()


def shutdown():
    if not _pool.closed:
        _pool.close()


def retrieve(query: str, k: int = 5, statuses=LIVE, query_embedding=None):
    """Top-k chunks for a query. Returns [{id, doc, article, url, text, score}]."""
    reused = query_embedding is not None
    if reused:
        v = np.asarray(query_embedding, dtype=np.float32)
    else:
        v = model().encode([query], normalize_embeddings=True)[0]
    _record_embedding_reuse(reused)
    vs = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
    sql = """SELECT id, doc, article, url, text, 1 - (embedding <=> %s::vector) AS score
             FROM chunks WHERE status = ANY(%s)
             ORDER BY embedding <=> %s::vector LIMIT %s"""
    with pool().connection() as conn, conn.cursor() as cur:
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