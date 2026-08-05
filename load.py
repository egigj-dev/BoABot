# load.py — parquet -> pgvector. Idempotent: drops and recreates the table.
# DSN  : local-only Postgres from docker-compose
# rows : (id, doc, article, status, section, url, text, '[v1,v2,...]') tuples
import pandas as pd, numpy as np, psycopg

DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
df = pd.read_parquet("embedded.parquet")

SCHEMA = """
DROP TABLE IF EXISTS chunks;
CREATE TABLE chunks (
  id text PRIMARY KEY, 
  doc text, 
  article text, 
  status text,
  section text, 
  url text, 
  text text, 
  embedding vector(1024)
);
CREATE INDEX ON chunks (status);
"""

rows = [
    (r.id, r.doc, r.article, r.status, r.section, r.url, r.text,
     "[" + ",".join(f"{x:.6f}" for x in np.frombuffer(r.embedding, dtype=np.float32)) + "]")
    for r in df.itertuples()
]

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute(SCHEMA)
    cur.executemany(
        "INSERT INTO chunks (id,doc,article,status,section,url,text,embedding) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)", rows)
    conn.commit()
    cur.execute("SELECT count(*), count(embedding) FROM chunks")
    print("loaded:", cur.fetchone())