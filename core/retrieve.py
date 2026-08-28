# retrieve.py — the one function everything else calls.
# Same bge-m3 that produced the index; normalized so <=> is cosine distance.
# Hybrid-only derived fields expose each retriever's rank/raw score plus the RRF
# score. They are diagnostic and deliberately absent from the default dense path.
import logging
import os
import threading

import numpy as np
from psycopg_pool import ConnectionPool
from sentence_transformers import SentenceTransformer

DSN = os.environ.get("BOABOT_DSN", "postgresql://127.0.0.1:5433/boa")
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
LIVE = ("canonical", "base")          # excludes amendment + superseded
CUSTOMER_SCOPES = ("public",)   # allowlist: unclassified never reaches a caller
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
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
    return _model


def pool():
    """Return the process-wide pool, opening its first connection exactly once."""
    if _pool.closed:
        with _pool_lock:
            if _pool.closed:
                _pool.open(wait=True)
    return _pool


def open_pool() -> None:
    """Open and validate the database pool during application startup."""
    pool()


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


def retrieve(query: str, k: int = 5, statuses=LIVE, query_embedding=None,
             embedded_query: str | None = None, mode: str = "dense",
             scopes=CUSTOMER_SCOPES):
    """Top-k dense or RRF-hybrid chunks.

    ``dense`` is the unchanged production path. ``hybrid`` is opt-in and returns
    reciprocal-rank-fusion scores, which are not cosine similarities and must not
    be passed to the existing relevance gate.
    """
    if mode not in {"dense", "hybrid"}:
        raise ValueError(f"unsupported retrieval mode: {mode!r}")
    reused = query_embedding is not None
    if reused:
        if embedded_query is None:
            raise ValueError("embedding source text is required")
        if query.encode("utf-8") != embedded_query.encode("utf-8"):
            raise ValueError("query embedding may only be reused for byte-identical text")
        v = np.asarray(query_embedding, dtype=np.float32)
    else:
        v = model().encode([query], normalize_embeddings=True)[0]
    _record_embedding_reuse(reused)
    vs = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
    sql = """SELECT id, doc, article, url, text,
                    1 - (embedding <=> %s::vector) AS dense_score
             FROM chunks WHERE status = ANY(%s) AND doc_scope = ANY(%s)
             ORDER BY embedding <=> %s::vector LIMIT %s"""
    if mode == "dense":
        with pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (vs, list(statuses), list(scopes), vs, k))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # PostgreSQL has no Albanian text-search configuration. The additive index
    # deliberately uses `simple`: useful for exact lexical evidence, but without
    # Albanian stemming. Query lexemes are ORed so question wording absent from a
    # passage cannot suppress all lexical candidates.
    lexical_sql = """WITH query AS (
                       SELECT to_tsquery(
                           'simple',
                           array_to_string(
                               tsvector_to_array(to_tsvector('simple', %s)), ' | '
                           )
                       ) AS terms
                     )
                     SELECT id, doc, article, url, text,
                            ts_rank_cd(text_search, query.terms) AS lexical_score
                     FROM chunks, query
                     WHERE status = ANY(%s) AND doc_scope = ANY(%s)
                           AND text_search @@ query.terms
                     ORDER BY lexical_score DESC, id ASC LIMIT %s"""
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (vs, list(statuses), list(scopes), vs, k))
        cols = [d[0] for d in cur.description]
        dense_hits = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(lexical_sql, (query, list(statuses), list(scopes), k))
        cols = [d[0] for d in cur.description]
        lexical_hits = [dict(zip(cols, r)) for r in cur.fetchall()]

    by_id = {hit["id"]: dict(hit) for hit in dense_hits}
    for hit in lexical_hits:
        by_id.setdefault(hit["id"], dict(hit))
    dense_ranks = {hit["id"]: rank for rank, hit in enumerate(dense_hits, 1)}
    lexical_ranks = {hit["id"]: rank for rank, hit in enumerate(lexical_hits, 1)}
    lexical_scores = {hit["id"]: hit["lexical_score"] for hit in lexical_hits}
    fused = []
    for chunk_id, hit in by_id.items():
        dense_rank = dense_ranks.get(chunk_id)
        lexical_rank = lexical_ranks.get(chunk_id)
        hit["lexical_score"] = lexical_scores.get(chunk_id)
        hit["dense_rank"] = dense_rank
        hit["lexical_rank"] = lexical_rank
        hit["retrieval_source"] = (
            "hybrid" if dense_rank is not None and lexical_rank is not None
            else "dense" if dense_rank is not None else "lexical"
        )
        hit["rrf_score"] = sum(
            1.0 / (60 + rank)
            for rank in (dense_rank, lexical_rank)
            if rank is not None
        )
        fused.append(hit)
    return sorted(
        fused,
        key=lambda hit: (
            -hit["rrf_score"],
            hit["dense_rank"] is None,
            hit["dense_rank"] if hit["dense_rank"] is not None else k + 1,
            hit["lexical_rank"] if hit["lexical_rank"] is not None else k + 1,
            hit["id"],
        ),
    )[:k]


def fetch_chunks_by_ids(chunk_ids, statuses=LIVE, scopes=CUSTOMER_SCOPES):
    """Fetch known chunk IDs without a second embedding call."""
    ids = tuple(dict.fromkeys(str(chunk_id) for chunk_id in chunk_ids if chunk_id))
    if not ids:
        return []
    sql = """SELECT id, doc, article, url, text
             FROM chunks WHERE status = ANY(%s) AND doc_scope = ANY(%s)
             AND id = ANY(%s)"""
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (list(statuses), list(scopes), list(ids)))
        cols = [description[0] for description in cur.description]
        by_id = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
    return [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]


def fetch_doc_article(doc_fragment: str, article: str, statuses=LIVE,
                      scopes=CUSTOMER_SCOPES):
    """Resolve an explicit document/article reference from chunk metadata."""
    sql = """SELECT id, doc, article, url, text
             FROM chunks
             WHERE status = ANY(%s) AND doc_scope = ANY(%s)
                   AND doc ILIKE %s AND article = %s
             ORDER BY id"""
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            list(statuses), list(scopes), f"%{doc_fragment}%", str(article),
        ))
        cols = [description[0] for description in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

if __name__ == "__main__":
    import time
    q = "Sa është komisioni për shlyerje të parakohshme të kredisë në Banka Credins?"
    t = time.time(); hits = retrieve(q); el = time.time() - t
    print(f"{el*1000:.0f}ms\n")
    for h in hits:
        print(f"  {h['dense_score']:.3f}  {h['id']:10s} {h['doc'][:50]}")
        print(f"          {h['text'][:110].replace(chr(10), ' ')}\n")
