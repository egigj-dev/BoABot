-- Derived structure: text_search is generated solely from chunks.text using
-- PostgreSQL's `simple` configuration, and the GIN index accelerates @@ lookup.
-- PostgreSQL has no dedicated Albanian text-search configuration; consequently
-- this opt-in lexical path provides exact lexeme matching without Albanian stemming.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', COALESCE(text, ''))) STORED;

CREATE INDEX IF NOT EXISTS chunks_text_search_idx
    ON chunks USING GIN (text_search);
