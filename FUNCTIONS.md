# BoABot functions and execution flow

This is the function-level companion to `EXPLAINED.md`. That document supplies the newcomer map; this one follows the current checkout closely enough to debug it. It covers all 19 active Python files, the four historical `.orig` snapshots, `db/docker-compose.yml`, and both notebooks cell by cell. Counts and architectural statements below describe the files as they exist now, not the older design preserved in the snapshots.

Two reading rules matter:

1. Several files (`main.py`, `load.py`, `fix_rates.py`, and most notebook cells) do their work at module or cell scope. They have no `def` to call; importing or running them executes the statements immediately.
2. The production entry point is not `main.py`. It is the object `api:app`, normally imported by Uvicorn. `main.py` only inspects `embedded.parquet`.

## 1. Main call graph / who calls what

### Startup and shutdown

```text
uvicorn imports api:app
  -> api imports rag
       -> rag reads DEEPSEEK_API_KEY immediately
  -> api imports callcenter
       -> callcenter imports handoff_probe.json immediately
       -> callcenter imports retrieve.model
  -> FastAPI startup: api.warm_dependencies()
       -> retrieve.warmup()
            -> retrieve.retrieve("ngrohje e sherbimit", k=1)
                 -> retrieve.model().encode(...)
                 -> retrieve.pool().connection()
                 -> PostgreSQL/pgvector query
            -> retrieve.reset_embedding_stats()

process shutdown
  -> api.close_dependencies()
       -> retrieve.embedding_stats()
       -> retrieve.shutdown()
```

Import can therefore fail before FastAPI starts if the OpenRouter key is absent, `handoff_probe.json` is missing or malformed, or a required Python package is unavailable. Database/model failures normally surface in startup warmup.

### One `/turn` request

```text
POST /turn JSON
  -> Pydantic TurnReq.clean_turn_question()
  -> api.turn()
       -> StreamingResponse(api.generate_turn(req))
            -> callcenter.sessions.get(session_id)
                 -> SessionStore._evict()
            -> callcenter.decide(question, last_answer, history)
                 -> trust.input_gate()
                      -> trust._looks_like_base64()
                      -> trust._looks_like_instruction_override()
                           -> trust._fold()
                 -> callcenter._is_repeat()
                 -> trust.is_business_deposit_question()
                      -> trust._fold()
                 -> callcenter._redact_pii()
                 -> callcenter._encode_question()
                      -> retrieve.model().encode(...)
                 -> callcenter._probe_score()
            -> early policy outcome? record + token + done, then stop
            -> rag.needs_rewrite()
            -> if needed, rag.rewrite()
                 -> rag._post()
                 -> rag.completion_message()
            -> emit `tool`/search-progress SSE
            -> rag.retrieve_evidence()
                 -> trust.is_business_deposit_question()
                 -> retrieve.retrieve()
                      -> optionally reuse call-center embedding
                      -> retrieve._record_embedding_reuse()
                      -> retrieve.pool()
                      -> PostgreSQL/pgvector query
                 -> trust.trusted_hits()
                      -> trust._fold()
            -> refusal? record + token + done, then stop
            -> api.source() for each hit
            -> rag.grounded_messages()
            -> api.stream_answer()
                 -> OpenRouter streaming HTTP request
            -> SessionStore.record()
            -> api.turn_done()
```

Any `RAGError` or unexpected exception after policy routing is caught by `generate_turn()`, recorded as the fixed handoff response, and streamed with `outcome="handoff"`. The client receives a safe product outcome rather than an exception traceback.

## 2. Configuration, database, artifact inspection, and loading

### `config.py`

This file has no functions or classes. Its only executable assignment is:

```python
DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"
```

It centralizes the intended development connection string in principle, but most runtime and evaluation modules duplicate the literal instead of importing it. Changing `config.DSN` alone will therefore not move the application: `retrieve.py`, `load.py`, `eval.py`, and `validate_eval.py` retain their own values. The loopback address and port align with the Compose mapping below; the credentials are development credentials.

### `db/docker-compose.yml`

This declarative file contains no functions. The single service is named `db`, uses `pgvector/pgvector:pg16`, and forces the container name `boabot-postgres`. Its inline environment mapping creates database `boa`, user `boa`, and password `boa`. The port line:

```yaml
ports: ["127.0.0.1:5433:5432"]
```

exposes PostgreSQL only on host loopback port 5433 and forwards it to the normal container port 5432. `./pgdata` is mounted at PostgreSQL's data directory, so state persists relative to the Compose project/file location. `restart: unless-stopped` asks Docker to bring it back after daemon/host restarts unless an operator explicitly stopped it. This image includes pgvector binaries, but the application still assumes the `vector` extension/type has been enabled in the database; Compose does not run `CREATE EXTENSION`.

### `main.py`

There are no functions. Every line runs at module import or script execution:

1. `import pandas as pd, numpy as np` loads the Parquet and vector-inspection dependencies.
2. `pd.read_parquet("embedded.parquet")` reads the whole artifact from the current working directory. A missing file, absent Parquet engine, or malformed file aborts immediately.
3. It prints the DataFrame shape and column names.
4. `np.frombuffer(df.embedding.iloc[0], dtype=np.float32)` interprets the first embedding cell as raw float32 bytes without copying it.
5. It prints the vector shape and norm rounded to four decimals; the intended sanity values are `(1024,)` and `1.0`.
6. It prints status counts with `df.status.value_counts()`.

The script returns nothing and has no `if __name__ == "__main__"` guard. Importing `main` is therefore equivalent to running its inspection. Empty DataFrames fail at `.iloc[0]`; non-byte or wrong-endian embeddings can produce misleading dimensions/norms. It does not inspect duplicate IDs, null metadata, every vector, or database contents.

### `load.py`

This is also entirely module-level. It is a destructive loader, not a library API.

The file reads `embedded.parquet` into memory, defines a multi-statement `SCHEMA`, and constructs `rows` eagerly. For each `itertuples()` row, it copies seven metadata fields and serializes raw embedding bytes into pgvector's text syntax:

```python
"[" + ",".join(f"{x:.6f}" for x in
    np.frombuffer(r.embedding, dtype=np.float32)) + "]"
```

The six-decimal formatting reduces precision slightly and creates a large Python list before any insert begins. The database context then:

1. executes `DROP TABLE IF EXISTS chunks`;
2. creates `chunks` with a text primary key and `embedding vector(1024)`;
3. creates a B-tree index on `status`;
4. bulk-inserts all tuples with `executemany`, explicitly casting the last parameter to `vector`;
5. commits;
6. queries both total rows and non-null embeddings and prints `loaded: (...)`.

The connection and cursor context managers close resources even on failure, but the explicit commit makes the replacement durable. Because DDL and inserts occur in one psycopg transaction, a normal exception before commit should roll the transaction back; nevertheless, the intent is unambiguously to replace the table. There is no HNSW/IVFFlat index, schema migration, batching, row-shape validation, extension creation, or recovery logic. Running/importing from the wrong working directory can read the wrong artifact or fail. The returned SQL count is printed, not returned to a caller.

## 3. Corpus preparation before database loading

### `fix_rates.py`

This file defines no callable functions. It performs an in-place JSONL rewrite as soon as it runs/imports.

`BAND = re.compile(r"^\d+\s*-\s*\d+$")` recognizes labels consisting only of two integer endpoints and a hyphen. The script loads every line of `rate_tables.jsonl` with `json.loads`; blank or malformed lines fail the entire run. It then walks rows in original order:

- An item whose stripped lowercase value is exactly `muaj` is treated as a bare table header and dropped.
- A pure numeric band such as `13-24` becomes `maturitet 13-24 muaj`.
- For a relabeled row, the first line of `text` is rebuilt from `source`, `category`, and the new item; everything after the first newline is retained with `split("\n", 1)[1]`.
- Every non-dropped row is appended to `out`; `fixed` counts only relabels.

Finally it opens the same filename with mode `"w"`, writes the new JSONL, and prints relabeled/dropped/remaining counts. Important failure modes: a relabeled row with no newline raises `IndexError`; opening for write truncates the source before the loop of writes completes; no temporary file or backup makes an interrupted write recoverable. A second run is mostly idempotent because labels now contain words and the bare header rows are gone, but upstream scraper cells can regenerate the defect.

### `boa_scraper_v2 (2).ipynb`

This 62-cell Colab notebook is a stateful research record. Cells must be read in sequence, while remembering that later cells replace or retry earlier strategies. Paths point to `/content/boa_corpus`; many cells write files. Notebook outputs are historical observations, not returned values or tests.

#### Cells 0–5: intent, dependencies, and global state

- Cell 0 explains the v2 crawl corrections: use the crawler session for `robots.txt`, isolate `#MainContentWrapper`, and BFS from each seed. It also states the copyright/local-corpus constraint.
- Cell 1 installs Requests, Beautiful Soup, and lxml.
- Cell 2 is a heading.
- Cell 3 defines `BASE`, 13 `SEEDS`, `OUT`, the one-second delay, User-Agent, page cap, content selector, and optional Drive mode. Changing these globals changes all later functions because they close over notebook-global state.
- Cell 4 imports helpers, optionally mounts Drive and rewrites `OUT`, creates `pages/` and `docs/`, defines `MANIFEST`, and prints the destination.
- Cell 5 explains why the HTTP helper precedes robot gating.

#### Cell 6 — rate-limited HTTP

```python
def get(url, binary=False):
```

Purpose: provide the one network entry point for later crawl/extraction cells. It first calls `allowed(url)` even though `allowed` is defined in the later cell; this works only if cell 8 has been executed before the first call. If disallowed it prints and returns `None`. Otherwise it computes time remaining since the previous request using mutable one-element list `_last`, sleeps if necessary, records the start time, and performs `sess.get(..., timeout=45)`. Non-binary responses use `apparent_encoding` (or UTF-8) to protect Albanian `ë`/`ç`; binary document downloads skip decoding. Any exception—including HTTP errors from `raise_for_status()`—is swallowed after a diagnostic print and becomes `None`.

Callers (`fetch_doc`, `crawl_section`, diagnostics, and table harvesters) must explicitly handle `None`; several exploratory cells assume success and dereference `.text`, so this safety convention is not consistent. `_last` measures from request start, not response completion, and is not thread-safe, though the notebook is sequential.

#### Cell 8 — robot gate

After directly fetching `/robots.txt` with the shared session, this cell records its status. A 200 response is parsed into `rp`; a 404 is interpreted as no declared restrictions; every other status leaves crawling disabled.

```python
def allowed(url):
```

It returns `True` for the observed 404 case, `False` when no parser is available, otherwise delegates to `rp.can_fetch(UA, url)`. The subsequent loop prints each seed's status. It does not validate same-origin itself; later link discovery does. The policy intentionally fails closed for WAF/server errors but treats absence of a robot file as permission.

#### Cell 10 — extraction and manifest helpers

```python
def links(soup, page_url):
```

It resolves every anchor `href` against the current page, keeps only strings beginning with `BASE`, strips fragments, and removes duplicates while preserving encounter order via `dict.fromkeys`. String-prefix host filtering is simpler than comparing parsed hostnames and can theoretically accept a hostname beginning with the same text; in this controlled site crawl, later section-path checks narrow page traversal.

```python
def body_text(html):
```

It parses HTML with lxml, decomposes tags named in `NOISE`, selects `CONTENT_SELECTOR`, extracts the first `<h1>` as title, and returns `(title, text)`. If the selector is absent, `(node or soup)` falls back to the whole cleaned document. That fallback can reintroduce navigation boilerplate. Text uses newline separators and stripping; markup/table structure is lost.

```python
def key(url):
```

It SHA-1 hashes the URL bytes and returns the first 16 hex characters. Callers use this stable, filesystem-safe identifier for page/document filenames. Truncation makes collisions theoretically possible and there is no collision check.

```python
def load_manifest():
```

It returns an empty set if the manifest does not exist; otherwise it parses nonblank lines and returns their `url` values. The set powers resume behavior, but malformed/truncated JSONL aborts loading, and it cannot distinguish an on-disk file that was deleted after its URL was recorded.

```python
def record(row):
```

It appends one UTF-8 JSON object plus newline to `MANIFEST`. It returns `None`, performs no schema/dedup validation, and does not flush/fsync explicitly. All provenance depends on callers supplying the right `kind`, path, and counts.

#### Cell 12 — document download and BFS

```python
def fetch_doc(section, url, done):
```

It skips URLs already in the shared `done` set, fetches bytes, derives the URL path extension, writes content under `docs/{key}{ext}`, appends document metadata to the manifest, then adds the URL to `done`. It returns `None` in all paths. A crash between file write and manifest record leaves an orphan; a crash after manifest record but before `done.add` can duplicate only within unusual resumed state. Content type is not checked, so an HTML error page with a successful status could be saved using a document extension.

```python
def crawl_section(section, seed, done):
```

It parses the seed path and runs a list-backed breadth-first traversal. `seen` stops loops for the current run; `queue.pop(0)` makes the frontier O(n) per pop but the cap is only 300. Each unseen page is fetched. If not already in the persistent `done` set, body text is saved, a manifest page row is appended, and the page is marked done. Crucially, even an already-recorded page is fetched and its links traversed, allowing resume to discover descendants. Links under `/rc/doc/` with a known extension call `fetch_doc`; other links whose parsed paths start with the seed path enter the queue. The function returns `len(seen)`, which is visited URL count, not success or saved-page count. Query-string variants can count separately; redirect destinations are not canonicalized; `MAX_PAGES` can silently truncate a section.

Cells 13–20 run the crawl, summarize manifest counts, check tiny/non-diacritic pages and file extensions, and probe selectors on one detail page. Cell 14 contains a commented destructive `rm -rf`; enabling it discards the Colab corpus. Cell 18's “no ë” check is heuristic. Cell 20 assumes at least one detail link and successful responses, so `[0]`/`.text` can fail.

#### Cells 21–27 — corpus guidance and rate-table functions

Cells 21–24 set downstream recommendations, print per-section statistics/outbound links, and use `pandas.read_html` to diagnose table-bearing pages. Repeated `get(u).text` assumes the HTTP helper succeeded.

```python
def payload_table(url):
```

Cell 25 reads every HTML table from the fetched page and returns the DataFrame with greatest `.size` (rows × columns). “Widest/largest is payload” is a heuristic; it returns no metadata and raises if fetch/table parsing fails.

```python
def flatten(df):
```

It mutates and returns the same DataFrame. MultiIndex columns are joined after discarding components containing `Unnamed`; simple columns are stringified. Duplicate flattened names are possible. The function is diagnostic and the later final parser does not actually call it.

Cell 26 installs Playwright/Chromium.

```python
def build_names(dfs, codes):
```

Cell 27 searches all table columns for a legend column overlapping at least `max(3, len(codes)//2)` short codes. On a match it chooses, among every other column, the one with greatest average string length as the full-name column, then zips code/name values into a dictionary. It returns the first plausible legend or `{}`. NaNs are stringified, positional alignment is assumed, and the longest column is a heuristic.

```python
def parse_page(url, title):
```

It reads tables with `header=None`, selects the largest, treats its first row after column zero as bank codes, resolves names, and seeds `category` from the top-left cell. For each later row it:

1. recognizes a repeated code row as a category boundary;
2. treats column zero as the item label;
3. zips codes to non-null bank values, preferring full names;
4. skips blank/`nan` labels or rows with no pairs;
5. emits a self-contained dict with URL, source, category, item, and text.

The returned list is flattened across five pages and written to `rate_tables.jsonl`. The parser's structural assumption is what produced bare `Muaj`/numeric maturity labels later repaired by `fix_rates.py`. `pd.notna(v)` is tested on original cells but `vals` is otherwise unused. It does not attach an as-of date.

#### Cells 28–40 — accordion/FAQ experiments

Cells 28, 31, 34, 35, and 36 are competing harvest strategies. Cell 28 clicks JavaScript anchors then uses `inner_text`; cell 31 targets content anchors; cell 34 retries plain HTML; cells 35 and 36 are duplicate Playwright code using `text_content("#content")` so collapsed nodes remain visible. Cells 29–30 inspect request/selector behavior and leave a browser object open for diagnostics. Exceptions during clicks are mostly swallowed. Later cells overwrite the same page files, so the last successful strategy wins.

Cell 32 calls `record(...)` for each target without checking the existing manifest. Re-running can append duplicate URL rows even though the main crawler used `done` to avoid this.

Cells 38 and 40 are two FAQ parsing attempts. Cell 38 splits on repeated whitespace, remembers a block ending in `?`, and pairs it with the next non-question block; it writes `eval_faq.jsonl`. Cell 40 later flattens whitespace and splits after question marks, producing a new in-memory `pairs` list but does not rewrite the file. Cell 39 diagnoses merged blocks. No functions are defined; all variables are global notebook state.

Cell 41 creates an older retrieval eval: assigns rate IDs by order, samples 40 chunks with seed 0, chooses a known bank when possible but otherwise a random bank, and writes `eval_retrieval.jsonl`. That fallback is the source of known bank/gold inconsistencies. Cell 42 installs PyMuPDF.

#### Cells 43–53 — PDF extraction, chunking, and status functions

Cells 43–45 triage PDFs using sampled text density, inspect low-density documents for images/encryption, extract every page of documents above 100 sampled characters/page, and write `pdf_text.jsonl`. A sample threshold can misclassify mixed scanned/text PDFs. Full extraction concatenates page text and carries document metadata; extraction errors in cell 45 are not caught.

```python
def split_doc(d):
```

Cell 46 splits one PDF record. `NENI` matches a line consisting of `Neni/NENI` and a numeric article, optionally `/subarticle`. With fewer than three markers, it emits overlapping-ish windows: 1,500 characters every 1,300, with `article=None`. Otherwise it slices marker-to-next-marker, then sub-splits each body every 1,800 characters while retaining up to 2,000, creating 200-character overlaps. It returns dictionaries containing article and raw body. The caller prefixes document title and article, carries URL/section/doc metadata, and populates global `chunks`. Character slicing can split words; preamble before the first article is discarded for structured documents; only two case variants are recognized.

```python
def fold(s):
```

Cell 47 lowercases, NFD-decomposes, and removes characters whose Unicode category is `Mn`, making diacritic-insensitive filenames. It is used only for status heuristics. Consolidated/integrated names become `canonical`, names beginning `vendim` or containing `ndryshim` become `amendment`, everything else `base`.

Cell 48's ternary is deliberately forced by `if False else chunks`, so rate chunks are not merged. It writes regulation IDs by current order.

```python
def regnum(doc):
```

Cell 49's first version matches limited filename forms beginning `Rregullore` or `Udhezim` and returns captured digits or `None`. It is used to compare status sets.

```python
def regnum(doc):
```

Cell 51 redefines—therefore replaces—the same global name with a broader implementation. It turns underscores into spaces, tries a generic `nr` pattern and then regulation/instruction words, returning the first number or `None`. Cells 51–52 use this newer version. Any later call to `regnum` uses the second definition; the first remains relevant only to cell 49's already-computed state.

Cell 53 manually changes every chunk for one exact Regulation 63 filename to `superseded`, rewrites `chunks.jsonl`, and prints counts. If the upstream filename changes, the manual override silently affects zero rows.

#### Cells 54–61 — embedding and transfer

Cell 54 installs sentence-transformers. Cell 55 loads bge-m3 on CUDA, assigns enumeration-based rate IDs, combines regulation/rate metadata, encodes normalized vectors, stores each float32 row as bytes, and writes Parquet. It is a full embedding pass with many recorded progress/widget outputs.

Cell 56 is an alternative incremental path: re-embed only rates, remove old `rate_` rows, concatenate with retained regulations, and overwrite Parquet. It depends on `model`, NumPy, and pandas already existing from cell 55 and can carry stale regulation state.

Cells 57–58 validate row prefixes, duplicates, maturity labels, and combined ID uniqueness. Cell 59 installs `colab-ssh`; cell 60 starts a Cloudflare SSH tunnel using a literal example password. It returns whatever `launch_ssh_cloudflared` displays but defines no project function. It should not be run/reused as secret practice. Cells 22, 33, and 61 are empty.

### `boa_embed.ipynb`

This focused 11-cell notebook is the cleaner full-rebuild path and defines no functions or classes.

- Cell 0 states its purpose, full-rebuild rule, and ordering requirement.
- Cell 1 sets and prints `OUT`.
- Cell 2 installs sentence-transformers, NumPy, and pandas.
- Cell 3 introduces the gate.
- Cell 4 loads rate JSONL, prints total/maturity count and one sample, then asserts at least one `maturitet` label. The assertion proves only that some correction exists, not that every maturity/header row is correct.
- Cell 5 documents expected shape, but its `~4,152` prose is stale relative to the current 4,168 rows.
- Cell 6 loads bge-m3 on CUDA; freshly reads regulations and rates; assigns rate IDs by row order and common metadata; concatenates; asserts unique IDs; encodes all text with `normalize_embeddings=True`; constructs seven metadata columns; stores float32 bytes; and overwrites `embedded.parquet`. The imported `torch` is not referenced directly. There is no fixed model revision, so a changed upstream model under the same name could affect reproducibility.
- Cell 7 introduces verification.
- Cell 8 re-reads Parquet and prints row count, byte-derived dimensions, duplicate count, prefix counts, and norms for only the first five vectors. Dividing byte length by four assumes float32.
- Cell 9 gives manual transfer/load commands.
- Cell 10 invokes Colab's `files.download`; this is a browser download side effect, not a returned artifact path.

## 4. HTTP/API orchestration — `api.py`

At import time this module constructs `FastAPI`, installs fully open CORS middleware, and assigns the embedded HTML page. Importing `rag` also requires `DEEPSEEK_API_KEY`. The API code is synchronous: FastAPI may run endpoint functions in its worker threadpool, and the streaming generator performs blocking Requests/database/model work.

### Startup and shutdown hooks

```python
def warm_dependencies():
```

Registered with `@app.on_event("startup")`, it calls `retrieve.warmup()` and returns `None`. A failure prevents normal service startup. The older FastAPI event API is used rather than a lifespan context.

```python
def close_dependencies():
```

Registered for shutdown, it logs cumulative query-embedding reuse counts, calls `retrieve.shutdown()`, and returns `None`. Stats are process-local and reset after warmup. Shutdown logging can itself be absent depending on logging configuration.

### SSE and provider streaming

```python
def sse(obj):
```

It JSON-serializes any object with `ensure_ascii=False`, prefixes `data: `, and appends two newlines, returning one SSE event string. It does not set event IDs/types/retry fields; the event's logical type lives inside JSON. Non-JSON-serializable values raise here.

```python
def stream_answer(messages, session_id=None):
```

This generator builds the OpenRouter streaming payload using the module's hard-coded `MODEL`; an optional opaque session ID becomes OpenRouter's sticky-routing key. Inside a Requests response context it raises on non-2xx status, forces UTF-8, and scans decoded lines. Blank/non-`data:` lines are ignored, `[DONE]` ends iteration, and each JSON body must contain `choices[0].delta`. Nonempty `delta["content"]` is yielded exactly as received.

Malformed JSON/shape raises `RAGError("Model provider returned an invalid stream")`; Requests failures become `RAGError("Model provider stream failed")`. A valid event with empty `choices` is considered malformed here, unlike `bench_provider.stream_once`, which skips it. The function does not inspect provider `usage`, error objects, finish reasons, or partial text after a later error. Its return value is generator exhaustion; callers accumulate yielded strings.

```python
def source(hit: dict[str, Any]) -> dict[str, str]:
```

It projects one retrieved row onto `id`, `doc`, `article`, and `url`, converting missing/falsy values to empty strings. It intentionally drops passage text and score before browser delivery. The use of `or ""` also erases a legitimate numeric zero, though article/id values are normally strings. `generate_turn()` deduplicates the returned objects by ID.

### Simple routes

```python
def health():
```

`GET /health` returns `{"ok": True}` without checking the model, database, key, or provider. It is a process liveness check, not readiness.

```python
def index():
```

`GET /` wraps the constant `PAGE` in `HTMLResponse`. It performs no templating and returns the same demo client to everyone.

### Embedded browser functions in `PAGE`

These JavaScript functions are part of the served application even though Python's AST does not see them.

```javascript
function addMsg(cls, html)
```

Creates a `<div>`, gives it classes `msg` plus the caller class, assigns `innerHTML`, appends it, scrolls the chat, and returns the element. Callers must escape untrusted values before putting them in `html`; the bot shell is a fixed literal.

```javascript
function ask(q)
```

Copies a suggestion into the input and delegates to `send()`. It returns the promise only implicitly as `undefined` because it does not `return send()`.

```javascript
async function send()
```

This is the browser-side turn orchestrator. It trims input, exits on empty text, clears/disables controls, removes the initial placeholder, and renders the escaped user message. It creates bot status/content/source containers, then `fetch`es `/turn` with only question and current `sessionId`. It incrementally decodes bytes, keeps an incomplete-line buffer, and processes `data: ` lines as JSON.

- `tool` updates the search status.
- `token` appends text and rerenders the entire escaped accumulation plus a cursor.
- `error` marks failure and renders an error (current server path rarely emits this type).
- `done` stores the session ID, marks handoff status, removes the cursor, and renders deduplicated source links keyed by `doc + article`.

The `catch` renders network/parse errors; controls are re-enabled afterward. Gotchas: the final decoder is not flushed and any last buffered line without newline is ignored; JSON parse errors abort the entire loop; no `finally` means unusual exceptions before the end still reach the catch but the common re-enable statements follow it; requests cannot be canceled; multiple sends are prevented only by the button, not the Enter handler directly. Source URLs go through attribute escaping and links use `noopener noreferrer`.

```javascript
function escapeHTML(s)
```

Escapes ampersand, angle brackets, and double quote before inserting plain text into `innerHTML`. It does not escape apostrophes, which is acceptable for text-node HTML usage here.

```javascript
function escapeAttr(s)
```

Escapes ampersand, double quote, and angle brackets for double-quoted `href` attributes. It does not validate URL schemes; source URLs originate from the controlled corpus/database, so that trust boundary matters.

### Request model

```python
class TurnReq(BaseModel):
```

Pydantic creates the initializer, serializers, and validation machinery; no explicit `__init__` exists. `question` is 2–1,500 characters and `session_id` is optional up to 128 characters. Field length validation occurs along with the custom validator. Conversation history is deliberately absent, preventing a client from injecting arbitrary prior assistant/system messages.

```python
def clean_turn_question(cls, value: str) -> str:
```

This classmethod/field validator strips surrounding whitespace, rechecks that at least two characters remain, raises `ValueError` otherwise, and returns the cleaned string stored in `TurnReq`. Without the second check, a string of spaces could satisfy raw `min_length` then become empty. Internal whitespace is unchanged.

### Turn completion and main generator

```python
def turn_done(outcome: Outcome, session_id: str, sources=None,
              handoff=False, pii_redacted=False):
```

It constructs and returns one encoded SSE event containing type `done`, the enum's string value, session ID, `sources or []`, and both safety flags. Passing another falsey source container becomes a new empty list. It trusts `outcome` to be an `Outcome`; an arbitrary string lacks `.value`.

```python
def generate_turn(req: TurnReq):
```

This generator is the production control plane.

1. It gets/creates server state and calls `decide()` with raw cleaned request text, last answer, and history.
2. For any non-null policy outcome, it chooses `decision.question` or a safety placeholder, records that plus the fixed response, emits exactly one token event and one done event, and returns. PII turns therefore store a placeholder rather than raw input. Repeat turns are recorded as new history too.
3. Otherwise it conditionally rewrites the clean question. The search-progress event is yielded before retrieval, so clients see activity after rewrite but before database work.
4. It compares UTF-8 bytes of rewrite and original. If identical, it asserts the call-center vector exists and passes it plus its source text to `retrieve_evidence`; if changed, it passes no vector and retrieval embeds anew. Python string equality would be equivalent for normal strings, but byte comparison makes the intended invariant explicit.
5. A trust refusal is recorded, emitted, finalized as `UNSUPPORTED`, and stops before the model.
6. Accepted hits are projected through `source()` and stored in an insertion-ordered dict keyed by ID. `grounded_messages()` uses full hits, including text and score.
7. It consumes `stream_answer()`, both accumulating and forwarding tokens. On exhaustion it strips the concatenated answer. Whitespace-only/no content becomes the fixed no-evidence text, is emitted as a new token, and ends as `UNSUPPORTED`; otherwise outcome is `ANSWER`.
8. It records the original clean question and completed answer, then emits done with source metadata.

Both exception blocks deliberately collapse failure to human handoff. `RAGError` is logged as recoverable; every other exception is logged as unexpected. Each records and emits the same handoff message. Sources accumulated before an error are not returned. Generator exceptions after response headers have started cannot change the HTTP status, which is why the SSE contract must carry the outcome.

Subtleties: `sources` is initialized before the `try` but not used in error completion; synchronous session mutation is protected inside `SessionStore`, though two simultaneous requests for the same `Session` can still observe interleaved logical histories; a disconnected browser may stop generator consumption and thus interrupt later recording depending on server behavior.

```python
def turn(req: TurnReq):
```

The `POST /turn` endpoint returns `StreamingResponse(generate_turn(req), media_type="text/event-stream")`. FastAPI/Pydantic have already validated `req`. It sets no anti-buffering/cache headers and does not execute the generator before constructing the response. Callers use the streamed events, not a Python return body.

## 5. Call-center policy and sessions — `callcenter.py`

Importing this module has significant work: it instantiates the global session store, compiles regexes, reads `handoff_probe.json`, Base64-decodes and zlib-decompresses the frozen embedding matrix, converts labels to Boolean NumPy form, and validates `k`/dimension metadata. The decoded matrix is viewed as little-endian float32. A malformed artifact raises before any request can run.

### Outcome and data containers

```python
class Outcome(str, Enum):
```

The five members—`ANSWER`, `CLARIFY`, `UNSUPPORTED`, `HANDOFF`, and `REPEAT`—carry lowercase wire values. Inheriting from `str` makes values convenient for JSON/string comparison, while `.value` is used explicitly in `turn_done()`. No methods are defined.

```python
@dataclass(frozen=True)
class Decision:
```

This immutable routing result contains an optional terminal outcome, user-facing message, safe/clean question, `handoff` and `pii_redacted` flags, an optional normalized query vector, and optional semantic class margin. `outcome=None` means “continue to retrieval/model,” not indecision. Early outcomes often leave `question` empty, causing `generate_turn()` to store a placeholder. The NumPy array inside a frozen dataclass is itself still mutable; “frozen” prevents field reassignment, not mutation of referenced objects.

```python
@dataclass
class Session:
```

Holds the opaque ID, alternating message history, last answer, and last-update epoch seconds. It is mutable because the store updates it in place. There is no per-session lock or user identity.

```python
class SessionStore:
```

A bounded process-local dictionary protected by a reentrant lock. It is safe against basic concurrent dictionary mutation but is neither durable nor shared across worker processes.

```python
def __init__(self) -> None:
```

Creates an empty `dict[str, Session]` and `threading.RLock`; returns `None`. The global `sessions = SessionStore()` calls it at import.

```python
def get(self, requested_id: str | None) -> Session:
```

Captures `time.time()`, acquires the lock, and calls `_evict(now)` before lookup. An existing requested ID has `updated_at` refreshed and is returned by reference. Missing, expired, empty, or attacker-invented IDs cause a new UUID4 hex session to be created/stored/returned. It does not signal that a requested ID was replaced. Merely fetching a session extends its TTL even if the turn later fails.

```python
def record(self, session: Session, question: str, answer: str) -> None:
```

Under the lock it appends one user and one assistant dict, truncates to the last 12 messages, updates `last_answer`, and refreshes time. Because additions come in pairs and the cap is even, normal history remains paired. It assumes `session` is the same object stored in `_sessions`; it does not verify membership. `generate_turn()` passes policy placeholders for sensitive turns.

```python
def _evict(self, now: float) -> None:
```

This private method assumes its caller already holds the lock. It deletes sessions whose age is strictly greater than one hour. It then computes overflow beyond 1,000, sorts all remaining sessions by `updated_at`, and removes the oldest excess. Cleanup is lazy—only `get()` triggers it—and sorting is O(n log n). Exactly one-hour-old sessions survive until their age becomes greater.

### Routing helpers

```python
def _redact_pii(text: str) -> tuple[str, bool]:
```

Sequentially substitutes email, Albanian-looking phone, and 9–19-digit/account-like patterns with Albanian placeholders. It returns both transformed text and a changed flag. Because substitutions are sequential, the long-number regex sees phone placeholders rather than original matched phones. In current `decide()`, changed text is not sent onward: any detection immediately hands off, so redaction mainly proves detection and would support a future safe log. Pattern false positives/negatives are expected; IBANs with letters, unusual international phones, and short identifiers may pass.

```python
def _is_repeat(text: str) -> bool:
```

Case-folds the turn and checks whether any fixed Albanian/English substring occurs. It returns Boolean. Substring matching can classify a longer sentence containing “repeat” even if intent differs, and it does not strip diacritics beyond providing separate variants.

```python
def _encode_question(question: str) -> np.ndarray:
```

Calls the shared lazy `retrieve.model()`, encodes a one-element batch with normalization, selects its first vector, and returns a float32 NumPy array. This is the single serving entry point for call-center embeddings and supplies a vector that retrieval can reuse. Model errors propagate. It does not explicitly verify 1,024 dimensions or norm.

```python
def _probe_score(query_embedding: np.ndarray) -> float:
```

Matrix-multiplies all frozen normalized exemplars by the normalized query, yielding cosine similarities. If the single nearest exemplar is negative, it returns negative infinity immediately. Otherwise it finds the maximum positive and maximum negative similarities and returns their difference. Production thresholding thus requires both a positive nearest neighbor and sufficient positive-versus-negative margin. Shape mismatch raises NumPy errors; NaNs can make `argmax`/comparisons unintuitive. It returns a scalar used for diagnosis and routing.

```python
def decide(question: str, last_answer: str,
           history: list[dict[str, str]]) -> Decision:
```

The order is the policy:

1. `input_gate()` failure returns `UNSUPPORTED` with the unsafe-input message. No safe question/vector is included.
2. Repeat intent returns `REPEAT` and the previous answer, or `REPEAT_MESSAGE` if absent. This happens before PII/handoff checks, so a phrase that includes both a repeat marker and sensitive digits follows repeat handling.
3. `_SECRET_FAST_RE` catches explicit credential compromise/disclosure contexts within roughly 80 characters of PIN/CVV/CVC/OTP and returns handoff.
4. Business-deposit detection uses current text plus history and returns unsupported.
5. PII replacement/detection returns handoff with `pii_redacted=True`.
6. Fewer than two whitespace-delimited words returns clarify without loading/encoding the model.
7. All remaining turns are encoded once and scored. Score at or above the frozen threshold returns semantic handoff and preserves vector/score in the decision.
8. A non-handoff two-word turn returns clarify but also preserves vector/score.
9. Three or more words return `Decision(None, question=clean_question, ...)`, authorizing downstream RAG.

Only PII handling changes `clean_question`, and that branch is terminal, so successful `question` is effectively the original request already stripped by Pydantic. The router does not determine topical relevance: off-topic multiword text may continue until retrieval trust rejects it. Callers use `outcome`, message/flags, and reusable vector; only evaluation commonly inspects `handoff_score`.

## 6. Deterministic gates — `trust.py`

The constants provide three fixed Albanian messages and `MIN_RELEVANCE_SCORE = 0.50`. These functions are deterministic heuristics, not a complete security, PII, or legal-validity layer.

```python
@dataclass(frozen=True)
class GateResult:
```

Stores `allowed`, optional user-facing `message`, and machine-readable `reason`. Successful gates normally contain only `True`. Callers use `reason` in trap evaluation and `message` for refusal; there are no methods.

```python
def _fold(text: str) -> str:
```

Case-folds, NFKD-decomposes, and removes combining marks. It returns a diacritic-insensitive comparison string, allowing `ndërmarr` and `ndermarr` patterns to align. NFKD also compatibility-decomposes some characters; it does not remove punctuation or normalize whitespace.

```python
def _looks_like_base64(text: str) -> bool:
```

It strips the entire input, rejects strings shorter than 16, requires every character to be Base64 alphabet plus up to two trailing `=`, and rejects impossible length modulo four. It pads to a multiple of four, validates decode, rejects empty bytes, then considers it encoded text if at least 90% of decoded bytes map to printable/whitespace Unicode characters via `chr(byte)`. It returns Boolean and swallows only decode/value errors. Ordinary long alphanumeric strings can occasionally look like Base64; Base64 embedded in a sentence will not match because the entire string must match.

```python
def _looks_like_instruction_override(text: str) -> bool:
```

Folds text and searches a small tuple of English/Albanian regex patterns for ignore/disregard/system-prompt/jailbreak/reveal/bypass language. `any(...)` short-circuits on first match. It returns no matched pattern, so `input_gate()` can report only a broad reason. Obfuscation, synonyms, or word breaks can evade it; benign discussion of “system prompt” can false-positive.

```python
def input_gate(text: str) -> GateResult:
```

NFKC-normalizes input, then rejects Unicode categories beginning `C` except newline/tab. That includes control, format, surrogate, private-use, and unassigned characters, so invisible bidirectional/zero-width controls are blocked. It URL-decodes with `unquote`; if decoding changed text and the original has at least three percent signs, it returns `encoded_text`. Finally it rejects whole-string printable Base64 or instruction-override patterns in either normalized or decoded text. Otherwise it returns allowed.

Percent-decoding is inspected but never passed onward. One/two encoded sequences can pass unless the decoded result matches an override. Invalid `%` sequences simply remain unchanged. Newlines/tabs are allowed here and may later affect word splitting/prompts.

```python
def is_business_deposit_question(
    question: str, history: Iterable[dict[str, str]] = ()
) -> bool:
```

It folds the current question and concatenated history contents. `has_business` must occur in the current turn via one of six stems. `has_deposit` occurs if the current turn names deposits, or—only when business is current—history contains a deposit stem. It returns the conjunction. This catches “Po për biznese?” after deposit discussion while avoiding any historical business mention by itself. It trusts arbitrary iterable message dicts and defaults missing content to empty. It can classify regulation questions mentioning corporate deposits as the unsupported rate category because it does not distinguish intent beyond stems.

```python
def trusted_hits(query: str, hits: list[dict[str, Any]]) -> GateResult:
```

It rejects an empty list, then tries to cast the first hit's score to float. Missing/non-numeric values become `invalid_hits`; notably `OverflowError` is not caught. A score below 0.50 becomes `weak_retrieval`; equality passes, and NaN also bypasses the `<` comparison. It folds the query and, if any broad rate stem occurs, requires at least one hit ID beginning `rate_`, not necessarily the top hit. Failure becomes `wrong_chunk_family`; success returns allowed.

The function assumes hits are descending by score, as `retrieve()` guarantees. It does not check later scores, source freshness, live status (already filtered in SQL), duplicate evidence, or whether the rate hit actually contains the requested bank/value. The broad `norm`/`interes`/`karte` terms are the known regulation-trap tradeoff.

## 7. Embedding and pgvector search — `retrieve.py`

The module creates a closed `ConnectionPool` immediately but lazily opens it. Model, pool, and counters are process-global; only pool opening and counters have locks. `LIVE = ("canonical", "base")` is the production status default.

```python
def model():
```

Returns the cached SentenceTransformer. On first call it constructs `BAAI/bge-m3` on CPU and assigns `_model`. There is no lock around lazy model creation, so simultaneous first calls could theoretically instantiate twice. It does not pin a model revision. Subsequent callers (`callcenter`, `retrieve`, `eval_handoff`) share the object.

```python
def pool():
```

Checks `_pool.closed`; if true, uses double-checked locking around `_pool.open(wait=True)`, then returns the same pool. Opening waits for the configured minimum connection. A pool closed permanently at shutdown may not be designed to reopen depending on psycopg-pool lifecycle/version; normal use does not call retrieval after shutdown.

```python
def reset_embedding_stats():
```

Under `_stats_lock`, sets reuse hits and misses to zero in one chained assignment. It returns `None`. `warmup()` calls it so the artificial warmup miss is excluded.

```python
def embedding_stats():
```

Returns a new dictionary snapshot of both counters while holding the lock. Shutdown logs it; no endpoint exposes it.

```python
def _record_embedding_reuse(reused):
```

Increments the hit counter when truthy, otherwise the miss counter, under the lock. Type is intentionally loose. It records whether a vector was supplied, not whether it was correct or actually saved time.

```python
def warmup():
```

Calls `retrieve("ngrohje e sherbimit", k=1)`, thereby loading the model, embedding once, opening the pool, and executing SQL. It discards the hit list and resets counters. Any dependency error propagates to FastAPI startup.

```python
def shutdown():
```

Closes the global pool if it is not already closed and returns `None`. The model remains resident until process exit.

```python
def retrieve(query: str, k: int = 5, statuses=LIVE,
             query_embedding=None):
```

The sole online search function first decides reuse solely by `query_embedding is not None`. Supplied vectors are coerced to float32; otherwise bge-m3 encodes the query with normalization. It records the decision, serializes every component to six-decimal pgvector text, and executes a parameterized exact cosine query:

```sql
SELECT id, doc, article, url, text,
       1 - (embedding <=> query_vector) AS score
FROM chunks
WHERE status = ANY(statuses)
ORDER BY embedding <=> query_vector
LIMIT k
```

Within pooled connection/cursor contexts, it derives column names from `cur.description`, zips them to every result row, and returns a list of dictionaries. The returned fields deliberately omit status/section. The same vector string is passed twice because PostgreSQL uses it in select and order expressions.

No validation enforces positive integer `k`, nonempty statuses, 1,024 finite normalized values, or correspondence between `query` and a supplied vector. `rag.retrieve_evidence()` supplies the byte-identity assertion for production reuse, but direct callers can misuse it. SQL parameters prevent injection. Exact scan is intentional for the small corpus; only status has an index.

The `__main__` block is not a function: direct execution searches one hard-coded rate question, prints elapsed milliseconds, then each score/ID/truncated document/text. Import skips this block.

## 8. RAG and model helpers — `rag.py`

Module import reads `os.environ["DEEPSEEK_API_KEY"]`, so even functions that do not need generation cannot be imported without the key. The endpoint and model are hard-coded; `MAX_QUERY_CHARS` matches API question length. `SYSTEM`, `EVIDENCE_HEADER`, and `REWRITE` are prompt constants.

```python
class RAGError(RuntimeError):
```

A marker exception for failures that the API may safely convert to handoff. It adds no methods. The docstring still mentions tool calls because `tool_query()` remains from the prior architecture.

```python
def _post(payload):
```

Performs a non-streaming authenticated POST to OpenRouter with a 90-second timeout, raises for HTTP failure, and returns decoded JSON. Requests errors and JSON decoding `ValueError` become `RAGError("Model provider request failed")`. It does not use a persistent Session, set temperature, or validate response shape; `completion_message()` does that next. Callers are `rewrite()` and `ask()`.

```python
def completion_message(response):
```

Extracts `response["choices"][0]["message"]`, converting missing/wrong container shape to `RAGError`. It separately requires the result to be a dict, then returns that dict without validating role/content/tool calls. `rewrite()` and `ask()` use `.get("content")` afterward.

```python
def tool_query(tool_call):
```

This retained historical validator expects a tool-call dictionary whose function name is exactly `retrieve`; JSON-decodes its `arguments`; extracts `query`; requires a string; strips it; and enforces 1–1,500 characters. Structural/JSON/name errors become one `RAGError`, non-text and invalid-length errors have more specific messages. No current production function calls it; it is useful only to understand/validate old tool-calling payloads and `.orig` behavior.

```python
def rewrite(question, history):
```

Without history it returns the input verbatim and makes no provider call. Otherwise it filters history to user/assistant roles, keeps the last four messages, formats lines as `role: content`, appends the current question as another `user:` line, and calls the model with `REWRITE` as system instruction. It extracts content; a non-string falls back to the original, while a string is stripped, truncated to 1,500 characters, and falls back if empty.

It does not run `input_gate()` on the model-produced rewrite, and truncation is by Python characters, not bytes/tokens. History contents are server-owned in `/turn` but can be caller-provided through direct `ask()`. Rewriting uses the same answer model and can add latency/fail the turn.

```python
def needs_rewrite(question, history):
```

Returns `False` without history or without Unicode word matches. A leading word in `_ELLIPTICAL_LEADS` immediately returns `True`. Otherwise any capitalized word after the first or any digit is treated as a specific reference and returns `False`. Questions of at most four words return `True`. For five to seven words it rewrites only if none of the domain anchor substrings appear; longer turns return `False`.

This is a latency heuristic, not grammatical analysis. Sentence-internal capitalization can suppress useful rewrite; no-diacritic variants are partly enumerated; a domain anchor can appear inside another word. API calls it before `rewrite()`; `ask()` does likewise.

```python
def grounded_messages(question, history, hits):
```

Serializes full hit dictionaries to JSON with Albanian characters preserved and `default=str` for Decimal scores, then returns:

1. invariant system instruction;
2. dynamic evidence as a second system message;
3. `history or []` in existing order;
4. original user question.

The split leading message is intended for prefix caching. It returns a new outer list, but the history dicts are shared references. There is no context/token limit, hit-field projection, or escaping beyond JSON; the system prompt tells the model that evidence is reference rather than instructions.

```python
def retrieve_evidence(query, history=None, query_embedding=None,
                      embedded_query=None, k=5):
```

It first repeats the business-deposit gate against the possibly rewritten query plus history. When a vector is supplied, two assertions require source text and byte-identical UTF-8 between `query` and `embedded_query`; assertions can be disabled with Python `-O`, so they are developer invariants rather than a security boundary. It calls `retrieve()`, runs `trusted_hits()`, and returns either `(hits, "")` or `([], safe_message)`. It does not catch database/model errors; API converts them to handoff. `generate_turn()`, `ask()`, and quality evaluation call it.

```python
def ask(question, history=None):
```

This non-streaming convenience path runs input and business-deposit gates, conditionally rewrites, retrieves vetted evidence, builds grounded messages, requests one completion, appends the returned assistant message, and returns `(content, messages)`. Refusals return `(message, [])`. Model content may be missing/non-string; `.get(..., "")` can return `None` if the key exists with null. It does not call `callcenter.decide()`, use `SessionStore`, route PII/repeats/handoff, stream, or attach source metadata, so it is not behaviorally equivalent to `/turn`.

The direct-execution block calls `ask()` on one hard-coded commission question and prints the answer; import skips it.

## 9. Eval generation — `make_eval.py`

Most of this file runs at module scope. It loads every rate row and assigns `rate_0000` IDs by enumeration, exactly mirroring embedding. That means file order is part of the data contract.

```python
def _bank_names(text: str) -> list[str]:
```

It walks newline-delimited text, keeps lines containing a colon, skips header lines beginning exactly `Normat` or `Rregullore`, takes text before the first colon, excludes two known category labels case-insensitively, and returns candidates in encounter order. It neither deduplicates nor verifies a `Banka` prefix, so any other colon-prefixed label can be mistaken for a bank.

After the definition, the script seeds global RNG with zero, shuffles all indices, and walks them until 40 questions exist. Rows without a detected bank increment `skipped`; others choose a bank randomly. Maturity items get a “norma ... me maturitet” template, other rows name the item directly. It writes `eval_generated.jsonl`, prints counts, and prints five questions. It returns nothing and lacks a main guard: importing it overwrites the eval file. Determinism depends on unchanged input order/content and Python's random behavior.

## 10. Retrieval scoring — `eval.py`

Import initializes `_RATES` and `_CHUNK_META` if the JSONL files exist. Thus metadata reflects files at import time, not later changes. Database `DSN` is declared but never used; retrieval reaches the database through `retrieve.py`.

```python
def _bank_names(text: str) -> list[str]:
```

Same parsing idea as `make_eval`, with a local skip set. It returns candidate labels before colons and powers `_RATE_BANK_NAMES` plus per-gold bank checks. The two implementations can drift because they are duplicated.

```python
def _load_set(path: str) -> list[dict]:
```

If the path is absent, it prints a bracketed warning to stderr and returns `[]`. Otherwise it JSON-decodes every line and returns the list. It does not skip blanks or catch malformed JSON. `main()` interprets both missing and genuinely empty files as “no entries.”

```python
def _prefix(gid: str) -> str:
```

Returns the substring before the first underscore. It classifies metrics as `reg`, `rate`, or an unexpected prefix. IDs without underscores return the whole string.

```python
def _score(entries: list[dict]) -> dict:
```

This is the metric engine. Empty input returns `{}`. For each entry it reads required `gold_id`/`question`, optional `trap`, increments denominators, times `retrieve(question, k=10)`, and derives:

- exact gold rank from returned IDs;
- first same-document-and-article rank using `_CHUNK_META`;
- first same-document rank;
- per-k exact, regulation article, regulation document, and regulation exact counts for `k = 1,3,5,10`;
- trap recall and production-top-five `trusted_hits()` refusal/reason;
- exact-ID misses with top three candidates;
- whether a rate question names a known bank but none belonging to its gold row.

For a missing regulation gold metadata entry, both gold doc/article become empty strings. A retrieved hit with empty fields could then falsely match, although normal rows have docs. Exact hit-by-prefix is counted once if present anywhere in top 10, so the later prefix summary is not per-k.

Latency median uses `statistics.median`; p95 uses `sorted(lats)[int(0.95*n)]`, which is neither the shared nearest-rank helper nor safe at every interpretation (for N=40 it selects zero-based index 38, the 39th value). It returns raw counts, formatted recall strings, latency integers, misses, Counter reasons, and trap details. Callers pass that structure only to `_fmt()`/`main()`.

The rate-bank check first asks whether any known bank name appears in the question, then whether a bank from the gold chunk appears. If the question names no recognized bank, it does not count an error. Substring matching can confuse overlapping bank names.

```python
def _fmt(scores: list[tuple[str, str, dict]]) -> None:
```

It prints a wide comparison table. Empty scores print “No data to report.” It builds headers for article/exact/document regulation recall at every k, plus latency, misses, rate-bank issues, trap refusals, and trap recall. For each scored set it extracts already-formatted strings and prints a row. It computes `pref_info` but never uses it—a harmless dead local.

After the table it prints up to six exact-ID misses per set and all trap refusal reasons/questions. The heading says trap-refusal target zero, reflecting those trap rows' intended supported-regulation behavior. It returns `None` and performs no file write.

```python
def main() -> None:
```

Uses command-line paths when provided, otherwise the three `ALL_SETS`; maps default filenames to historical labels; loads/scores nonempty sets; calls `_fmt`; then prints a compact exact-top-10 prefix count. The inner `for k in KS: pass` has no effect and documents that per-k prefix data was not tracked. Direct execution invokes `main`; import only performs the JSONL metadata initialization.

## 11. Eval integrity — `validate_eval.py`

This checker compares eval JSONL, the 119-row rate file, and live PostgreSQL. `BANK_LIKE` is declared but never used; actual bank extraction duplicates the other scripts.

```python
def _norm(text: str) -> str:
```

Returns NFC-normalized, case-folded text. Unlike `trust._fold`, it retains diacritics; a question without diacritics may therefore fail to match a chunk bank label with diacritics. It is used only for rate-bank comparison/exclusions.

```python
def load_rate_texts() -> dict[str, str] | None:
```

Reads and JSON-decodes all rate rows in a context manager. File/JSON failure prints `FAIL` and returns `None`. It then enforces exactly 119 rows. Finally it enumerates IDs to each row's `text`; missing/wrong row data prints failure and returns `None`. Success returns the mapping. Blank lines count as JSON errors, and the exact count intentionally forces coordinated corpus/eval updates.

```python
def validate(path: str, label: str,
             rate_texts: dict[str, str]) -> int:
```

Returns shell-style 0 success/1 failure while printing details. It early-fails missing or empty files, but malformed JSON/missing `gold_id` is not caught. It finds duplicate IDs, then opens a direct psycopg connection/cursor and examines every entry:

1. Queries status/doc by ID; absent IDs add an error and skip remaining checks.
2. Requires status `canonical` or `base`.
3. For rate IDs, finds the corresponding source text, extracts colon-prefixed candidates with the familiar exclusions, and requires at least one candidate to occur in the normalized question when candidates exist.

For label exactly `handwritten`, it additionally requires 20 `rate_` and 20 `reg_` entries, queries each regulation doc again, counts questions per doc, and caps each at two. It closes the connection only after these loops; an unexpected exception can leak it until interpreter cleanup. There is no `try/finally`, and one SQL query per entry plus repeated regulation queries is deliberately simple rather than efficient.

It prints every accumulated error and returns 1, otherwise prints a pass line and returns 0. It does not compare `gold_url`, question uniqueness, trap count, article correctness, or JSONL IDs against Parquet directly.

```python
def main() -> None:
```

Loads required rates; on failure exits 1 immediately. It sums the two validation return codes and exits 1 if any failed, else 0. `sys.exit` means calling `main()` raises `SystemExit`; the direct-execution guard invokes it.

## 12. Deterministic call-policy eval — `eval_calls.py`

```python
def outcome(decision):
```

Returns `decision.outcome.value` for terminal decisions, otherwise literal `"model"`. In this evaluator, “model” means only that call-center routing allowed RAG to continue; no retrieval or model call follows.

```python
def main():
```

Reads `eval_calls.jsonl` line by line. For each scenario it resets history/last answer, then calls `decide()` on every turn. It appends the route label, fabricates either `decision.message` or a supported-answer placeholder, appends safe question/assistant messages, and updates last answer. The simulation is close to `SessionStore.record()` but does not cap history at 12 and uses `[turn i mbrojtur]` rather than API's slightly different safety placeholder.

After all cases it compares the entire actual route sequence to expected. Any failures are printed and cause `SystemExit(1)`; otherwise it prints `total/total`. Semantic routes still load/encode bge-m3. It never tests `query_embedding`, score, SSE, trust evidence, or session eviction. The main guard runs it only on direct execution.

## 13. Handoff classifier analysis — `eval_handoff.py`

This script re-embeds all phrase rows and retrains/evaluates three methods. It imports private production constants by design so it can verify exact serving parity.

```python
def _group_key(text: str) -> str:
```

Lowercases, NFD-decomposes/removes diacritics, replaces punctuation with spaces while retaining Unicode word characters, collapses whitespace, and returns a family key. `_print_families()` pairs it with intent, so identical normalized text in different intents remains separate families.

```python
def _load() -> tuple[list[dict], dict, dict]:
```

Reads phrase-bank bytes, computes SHA-256, parses JSONL, reads both split JSON files, and rejects either split if its recorded source hash differs. It returns rows, old stratified split, grouped split. It does not validate indices/ranges/overlap beyond later reporting.

```python
def _fast(texts: list[str]) -> np.ndarray:
```

Applies production `_SECRET_FAST_RE` to each phrase and returns a Boolean NumPy array. These positives bypass every semantic threshold in evaluation just as in serving.

```python
def _predict(scores, threshold, indices, fast) -> np.ndarray:
```

Returns elementwise OR of fast-path decisions at selected indices and whether selected scores meet threshold. Output order follows `indices`, not global row IDs.

```python
def _metrics(scores, threshold, indices, labels, intents, fast) -> dict:
```

Calls `_predict`, subsets truth, and for each positive intent computes caught count, total, and recall. It then returns per-intent data, overall positive recall, negative false-positive rate/count/denominator, and prediction vector. It assumes every named intent has at least one selected row; otherwise division by zero occurs. `predictions[truth]` relies on truth being aligned to selected order, which it is.

```python
def _operating_point(scores, indices, labels, intents, fast)
        -> tuple[float, dict]:
```

Enumerates threshold infinity plus every unique selected score. For thresholds whose training FP rate is at most 2% (with tiny tolerance), it ranks candidates by overall recall, then weakest-intent recall, then lower threshold via `-threshold`. It returns chosen threshold and metrics, or raises if none. Infinity normally supplies a feasible fast-path-only point unless the regex itself exceeds the FP ceiling. Because it tunes on train only, test remains held out.

```python
def _kcenter_anchors(vectors, train, intents) -> np.ndarray:
```

For each positive intent it selects eight diverse train-only exemplars. It computes/normalizes the intent centroid, starts with the candidate most similar to centroid, then repeatedly measures each candidate's maximum similarity to selected anchors and chooses the least covered. Selected candidates are masked with infinity so they cannot repeat. It returns 40 row IDs in intent order. Fewer than eight candidates, zero centroid norm, or unexpected intents would break assumptions.

```python
def _method_b_scores(vectors, labels, train, query, k,
                     leave_self_out) -> np.ndarray:
```

For each query row it computes cosine similarities to train vectors. During train tuning it can set the query's own match to `-inf`. It sorts descending, requires a strict positive majority among top k, separately selects the k largest positive and negative similarities, and assigns their mean difference; failed majority gets `-inf`. It returns scores aligned to `query` positions, not global IDs. For production-selected `k=1`, this reduces to “nearest neighbor positive, then best-positive minus best-negative margin,” matching `_probe_score()`.

```python
def _train_methods(vectors, labels, intents, fast, split) -> dict:
```

Converts train/test indices and builds all three candidates:

- Method A: 40 k-center positive anchors; score is maximum similarity to any anchor; tune threshold on train and score test.
- Method B: try k=1 and k=3, create leave-self-out train scores, tune each, choose by overall then weakest-intent train recall, compute untouched test scores, and evaluate.
- Method C: fit logistic regressions for two class-weight choices × five C values, tune an operating threshold for each, choose by recall/weakest intent and then smaller coefficient norm, evaluate test.

It returns indices and nested method dictionaries containing thresholds, metrics, full score arrays/settings, plus selected k/classifier. Only Method B is exported to production. Tie-breaking Method B omits threshold/complexity beyond stable loop/max behavior, so exact ties choose the first maximum.

```python
def _print_families(rows, old_split, grouped_split) -> None:
```

Builds intent+normalized-text family membership, counts family sizes/intents, measures families spanning old train/test and affected old test rows, measures grouped leakage, and prints split strata. It reports rather than asserts zero grouped leaks.

```python
def _print_near_duplicates(rows, vectors, labels, train, test) -> None:
```

Computes test-to-train cosine matrix, masks cross-class pairs to `-inf`, finds each test row's nearest same-class train row, prints distribution points, then every row above 0.95 with both texts. If a class had no train example its nearest values would remain `-inf` and neighbor index zero would be misleading; split design prevents that.

```python
def _print_methods(results: dict) -> None:
```

Prints train/test recall per intent, overall positive recall, negative FP, and train-only setting for A/B/C. It consumes `_train_methods` output and returns nothing.

```python
def _print_direct_comparison(old: dict, grouped: dict) -> None:
```

Prints Method B test recall under old versus grouped splits for each intent, overall, and negative FP, including percentage-point delta. It exposes the leakage effect but performs no acceptance decision.

```python
def main() -> None:
```

The orchestration is deliberately strict:

1. Load/hash-check data and derive texts/intents/positive labels/regex fast flags.
2. Encode every phrase once with normalized bge-m3 vectors shared across methods.
3. Print leakage and near-duplicate diagnostics.
4. Train/evaluate old and grouped methods and print comparisons.
5. Among grouped methods satisfying test FP ≤2%, choose highest test overall recall. This uses held-out test to select the named winner, so it is an evaluation/model-selection set, not a perpetually untouched final test.
6. Require winner B and require `handoff_probe.json` method, k, margin, vectors, labels, and production threshold to match recomputation.
7. Monkey-patch `callcenter._encode_question` with a text-to-precomputed-vector lambda, call production `decide()` for grouped test rows, restore the original in `finally`, and require exact prediction equality.
8. Print residual false negatives/positives.

If no methods meet test FP, `max([])` raises. The embedding cache assumes every tested text exactly keys an original phrase. The `finally` is important: even a `decide()` error restores serving code in the current process. Direct execution invokes `main()`.

## 14. Full-path benchmark — `bench_turn.py`

The constants contain ten production-like questions and aligned history primers. `TOKEN_RE` is explicitly a Unicode lexical proxy, not a provider tokenizer.

```python
def percentile(values: list[float], fraction: float) -> float:
```

Sorts values and returns nearest-rank element at `ceil(fraction*n)-1`, bounded below at zero. It assumes nonempty values and does not validate fraction ≤1; empty input or large fraction raises `IndexError`.

```python
def request_turn(url: str, question: str,
                 session_id: str | None = None) -> dict:
```

Starts a high-resolution timer and initializes timing marks. It streams POST `/turn`, raises on HTTP failure, forces UTF-8, and parses `data:` lines. First valid SSE sets first-event time. The first `token` sets first-token time; all token text accumulates. The first sentence-ending regex match sets time and a provisional prefix. A `done` event stores done time/session and stops.

After closing the response, missing first-event/token/done raises “Incomplete SSE stream.” A nonempty punctuation-free answer uses done as first-sentence boundary. The function then recomputes the exact first-sentence prefix from the final answer, counts lexical tokens for answer/prefix, computes generation milliseconds from first token to done, and divides total proxy tokens by at least one millisecond. It returns the enriched marks dictionary.

It does not inspect done outcome, error events, handoff, or sources, so a policy message can count as successful latency. Sentence regex can stop at abbreviations. The timeout is 120 seconds even though preserved older runs report longer completions, implying measurement environment/version details should be checked before reproducing. Network time includes all local/server/provider work.

```python
def histogram(values: list[float]) -> None:
```

Bins values into `<500`, then fixed millisecond ranges through `>=20000`, scales bars to the peak count with width 30, and prints. Nonempty bins always get at least one `#`. Boundary values belong to the next bin because comparison is `<`.

```python
def main() -> None:
```

Parses N/history/URL/output, rejects N<1, and loops sequentially. With history, each measured request gets a fresh unmeasured primer whose returned session ID is used for the target; primer latency/cost is excluded but incurred. It prints each observation, then p50/p90/p95/p99/max for event/token/sentence/done, a histogram, throughput percentiles, sentence-token mean, and a decomposition made from independent medians. Independent median TTFT plus median generation need not equal median sentence time.

When `--output` is supplied it writes configuration and full per-request answers/timings as JSON, overwriting the path. No retry, warmup discard, randomization, concurrency, or model identity check exists. Direct execution invokes it.

## 15. Provider-only benchmark — `bench_provider.py`

This script intentionally imports no BoABot modules. `FIXTURES` freeze ten question/top-eight-ID results; corpus JSONL supplies the corresponding passage dictionaries. Its default combined layout is not the current production split layout unless `--layout split` is passed.

```python
def percentile(values: list[float], fraction: float) -> float:
```

Same nearest-rank implementation and empty/range gotchas as `bench_turn.percentile`.

```python
def load_chunks(path: Path) -> dict[str, dict[str, Any]]:
```

Builds the complete wanted ID set, scans regulation JSONL into a dictionary only when wanted, then scans sibling `rate_tables.jsonl`, assigning missing enumeration IDs, and adds wanted rates. It raises with sorted missing IDs if any fixture cannot be resolved; otherwise returns mapping. Duplicate IDs overwrite earlier rows silently. The passed path is expected to be `chunks.jsonl` because sibling resolution is fixed.

```python
def messages_for(question, ids, chunks, k, layout, prompt)
        -> list[dict[str, str]]:
```

Selects the first k frozen IDs, serializes full rows behind `EVIDENCE_HEADER`, chooses current versus trimmed prompt, and returns either two messages (combined system+evidence, user) or three (separate system, evidence-system, user). Any layout not exactly `combined` falls into split behavior; CLI choices constrain normal use. Missing IDs raise `KeyError`. Callers use the list directly in `stream_once()`.

```python
def stream_once(session, key, model, messages, max_tokens,
                sticky_session, reasoning) -> dict[str, Any]:
```

Builds a deterministic-temperature streaming OpenRouter payload. Positive/nonzero `max_tokens`, sticky session, and disabled reasoning are added conditionally. It records start, streams SSE, captures generation ID header, updates `usage` whenever an event carries it, skips events without choices, marks first nonempty content time, and accumulates text. It raises if no content ever arrives.

The returned dict includes TTFT, total time, time after first content, prompt/completion/reasoning/cache token telemetry, cost, generation ID, and full output. Missing usage becomes zeros. Generation time includes network gaps through response completion; completion tokens later subtract reasoning for throughput. Unlike production `stream_answer`, empty choices are tolerated. JSON shape errors other than missing optional keys propagate.

```python
def correlation(xs: list[float], ys: list[float]) -> float | None:
```

Returns `None` for fewer than two observations or zero population standard deviation on either side. Otherwise manually computes Pearson correlation using paired `zip`. Unequal lengths silently truncate numerator while means/denominator use full lists, so callers must supply aligned equal-length lists (they do). Floating rounding can slightly exceed theoretical bounds.

```python
def histogram(values: list[float]) -> None:
```

Same bins/rendering as the full-path benchmark, with peak stored as `width`.

```python
def model_price(session, key, model)
        -> tuple[float | None, float | None]:
```

GETs OpenRouter's model catalog, finds exact ID, returns `(None, None)` if absent, otherwise converts per-token prompt/completion strings to dollars per million. Missing/non-numeric pricing keys raise. The price is current at run time and informational; measured result cost comes from usage.

```python
def print_summary(results: list[dict[str, Any]], model: str) -> None:
```

Extracts distributions, computes aggregate non-reasoning completion tokens divided by aggregate post-first-token seconds, cache hit count, total cached tokens/cost, Pearson prompt-token/TTFT, histogram, and per-exact-prompt-token p50/p95 groups. It assumes a nonempty result list and can divide by zero only if pathological total generation were zero (each row clamps to .001 ms). Grouping by exact token count can produce many tiny groups.

```python
def run(args, key, chunks, session, requests_count, vary, k,
        heading) -> list[dict[str, Any]]:
```

Prints configuration; loops sequentially over either the first fixture or cycling ten; builds messages; calls `stream_once`; stores/prints each result; prints the summary; returns raw rows. It propagates the first HTTP/provider/parse error and has no retries. Reusing the Requests session preserves HTTP connections across calls.

```python
def main() -> None:
```

Defines CLI controls for N/model/k/layout/prompt/token cap/reasoning/sticky session/vary/size probe/corpus/output. It validates counts, accepts either historical key environment variable, loads fixtures, creates a Requests session, fetches/prints catalog price, and runs the primary benchmark. Optional JSON contains primary run only. If size-probe N is nonzero, it then runs a varying-input probe whose results are printed but not added to that JSON. Paid calls are the central side effect; the main guard prevents them on import.

## 16. Phase-3 recomputation — `phase3_analyze.py`

Paths are fixed to six evidence JSON files. Importing `TOKEN_RE` from `bench_turn` is safe because that module's main guard prevents requests.

```python
def percentile(values: list[float], fraction: float) -> float:
```

Same nearest-rank helper and assumptions as benchmarks.

```python
def sentence_prefix(answer: str) -> str:
```

Finds first sentence-ending regex and returns answer through the match, stripped; otherwise returns entire answer. Empty answer returns empty.

```python
def reconciled(path: Path) -> list[dict[str, Any]]:
```

Loads `results`, mutates each row in memory: punctuation-free nonempty rows with missing sentence time get done time; sentence text is recomputed; proxy tokens are recounted. Returns rows. It does not write source JSON, and does not reconcile empty answers/missing other metrics.

```python
def stats(values: list[float]) -> dict[str, float]:
```

Returns median, nearest-rank p90/p95/p99, and max. Nonempty input is required.

```python
def main() -> None:
```

For each of four full-path runs it reconciles rows, computes every latency metric, throughput/sentence/generation statistics, counts <3s versus ≥3s TTFT, identifies any answer containing the handoff phrase, and prints Markdown tables. For the first-sentence median decomposition it takes one middle row for odd N or two rows straddling the median for even N and averages their TTFT and generation, ensuring components add to the reported cohort median.

It then loads cache-on/off provider rows, computes TTFT stats, cache hits and <5s split, records on-minus-off deltas but prints off-minus-on as “savings.” Finally it adds a fixed 300 ms TTS assumption to p50/p95 sentence time, defines a small local `verdict = lambda target: ...` closure, prints pass/fail for 1.5s/2.5s, writes `latency_evidence/phase3_analysis.json`, and prints its path.

The lambda is a callable local to each loop iteration: it closes over current `p50`/`p95`, returns `PASS/FAIL` text for a millisecond target, and is not accessible after `main()`. The analysis assumes exactly the expected evidence shape and even hard-codes display `/100` for provider cache hits despite using dynamic row counts elsewhere. Running it modifies a generated evidence file.

## 17. Phase-3 answer quality — `phase3_quality.py`

Importing it imports `rag`, so a model key must exist even before CLI validation. The quality checks are automatic proxies, not complete answer correctness.

```python
def folded(value: str) -> str:
```

Case-folds, NFKD-decomposes, and removes combining marks for citation/refusal matching.

```python
def canonical_number(raw: str) -> str:
```

Removes percent and grouping apostrophes. With both comma and period it assumes commas are thousands separators. With only comma, a three-digit tail and nonzero head is treated as grouping; otherwise comma becomes decimal point. It parses `Decimal`; invalid values return transformed text. Integral values quantize to no decimals, others normalize trailing zeros and use fixed format. This makes several source/answer formats comparable, but ambiguities such as `1,234` are resolved by heuristic and percent semantics are discarded.

```python
def numbers(text: str) -> list[str]:
```

Finds all number-like tokens with `NUMBER_RE`, canonicalizes each, and returns them in occurrence order, including duplicates. Dates, article numbers, currency amounts, and rates are treated identically.

```python
def citation_present(answer: str,
                     hits: list[dict[str, Any]]) -> bool:
```

Folds the answer, then returns true if any nonempty full document name is a substring or if regex finds `neni {article}` for any hit. It returns false after all hits. A cited article number from the wrong document can pass; rate source docs usually pass through full-name matching; paraphrased/short citations can fail.

```python
def complete(session, key, model, messages)
        -> tuple[str, dict[str, Any]]:
```

Makes a non-streaming OpenRouter completion with 120-second timeout, raises on HTTP failure, extracts first choice content (or empty) and usage (or empty), and returns both. It does not catch errors or validate choices, set temperature, cap output, or use sticky session.

```python
def rate_questions(path: Path) -> list[dict[str, Any]]:
```

Loads all JSONL rows, selects IDs beginning `rate_`, requires at least 20, and returns the first 20. File order determines the benchmark subset.

```python
def score_rates(session, key, model, rows)
        -> list[dict[str, Any]]:
```

For each row it retrieves production evidence. A retrieval refusal becomes the “answer” with no usage; otherwise it calls the model on production grounded messages. It extracts answer numbers, serializes hits and extracts evidence numbers, then flags every answer occurrence not present in the evidence set. It also checks citations, records IDs/usage, prints pass/fail/full answer, and returns detailed rows.

An answer with no numbers automatically passes numeric grounding. The check ignores whether a supported number is attached to the right bank/label, whether required numbers are omitted, and whether retrieval contained the gold ID. Duplicate unsupported numbers stay duplicated in the list.

```python
def request_turn(session, url, question, session_id)
        -> dict[str, Any]:
```

Streams `/turn`, accumulates token text, captures first done event, and returns question/answer/done. It ignores tool/error events and does not require a done event; callers may then see `{}`. This is simpler than benchmark timing because it tests outcome only.

```python
def policy_refusals(session, url, path)
        -> list[dict[str, Any]]:
```

Loads call-policy cases and keeps any scenario whose expected sequence contains `unsupported`. It executes every turn in such a scenario while carrying session ID, labels each actual turn with its expected route, then requires only expected-unsupported turns to end with outcome unsupported. It prints all turns and returns per-case results. Other expected routes can be wrong without failing this particular check.

```python
def model_refusals(session, key, model) -> list[dict[str, Any]]:
```

Builds an empty-evidence system message, calls the model for five off-topic prompts, and passes an answer if any folded refusal marker occurs. It stores/prints results. Marker substring presence can pass an answer that refuses then still gives advice, or fail a valid novel refusal phrasing.

```python
def main() -> None:
```

Requires model/output, optional eval/calls/URL; resolves key; runs 20 rate scores, five direct refusals, and optional routed policy cases; prints totals; writes detailed JSON. Output overwrite and paid network/database/model work are intended. The output summary records counts but not a single overall pass exit code: failed quality items do not cause nonzero process status unless an exception occurs.

## 18. Phase-3 report builder — `phase3_build_report.py`

This script turns fixed evidence files into a report and overwrites `FINAL_REPORT_PHASE3.md`. The long f-string contains report claims/constants rather than calculating them from `phase3_analysis.json`, so evidence and prose can drift.

```python
def answer(quality: dict, index: int) -> str:
```

Returns the indexed rate answer with newlines replaced by `<br>` for a Markdown table. It does not escape pipes, quotes, or HTML; generated answers containing `|` could break the table.

```python
def main() -> None:
```

Loads Gemini/DeepSeek quality JSON; selects zero-based rate indices 1, 5, and 18; builds side-by-side quote rows using Gemini's question; interpolates them into a large static report body; then reads eight transcript files, removes carriage returns, embeds each in fenced text, joins all parts, writes the report, and prints transcript count. Missing/malformed evidence aborts. Triple backticks inside a transcript could prematurely close a fence. Direct execution invokes it; import only defines paths/constants.

## 19. Historical `.orig` snapshots

These files are not imported by Python because of the `.orig` suffix. They are covered as evolutionary/debugging evidence, not competing implementations. Duplicate unchanged definitions have the same contracts already described; the differences below explain every snapshot-only callable.

### `api.py.orig`

This snapshot contains 13 function/method definitions and two classes.

```python
class Req(BaseModel):
```

The removed `/chat` request accepted `question` plus client-supplied `history` (up to 12 messages), unlike current server-owned history.

```python
def clean_question(cls, value: str) -> str:
```

Stripped/rechecked the old request question exactly as current `clean_turn_question` does.

```python
def validate_history(cls, history: list[dict[str, str]])
        -> list[dict[str, str]]:
```

Required each dict to contain exactly `role`/`content`, allowed only user/assistant, stripped content, required 1–4,000 characters, and returned cleaned dicts. This reduced but did not remove the trust risk of client-authored assistant history.

```python
def sse(obj):
def stream_answer(messages):
def source(hit):
```

These are older forms of current helpers. Streaming had no sticky session payload.

```python
def generate(req: Req):
```

The old `/chat` generator gated input/business deposits, built system+client history, and let the model choose `TOOLS` for up to three iterations. It parsed each tool call, always rewrote its query, retrieved, appended tool-role JSON, then streamed a second model request. No tool call/no sources produced no-evidence. Errors emitted explicit `error` events instead of current handoff.

```python
def chat(req: Req):
```

Wrapped `generate(req)` in a `/chat` StreamingResponse. Both were removed in phase 1.

```python
def health():
def index():
class TurnReq(BaseModel):
def clean_turn_question(...):
def turn_done(...):
def turn(req: TurnReq):
```

These snapshot definitions correspond directly to current versions. The embedded JavaScript snapshot is missing a closing brace after `if (ev.handoff)`, making its served script syntactically broken.

```python
def generate_turn(req: TurnReq):
```

The older voice-ready path still asked the model for a retrieval tool call before search, always rewrote tool queries, and streamed after appending tool messages. It lacked conditional rewrite, always-first application retrieval, warmup/pool shutdown hooks, vector reuse, grounded split prompt, sticky session, and explicit emission of fallback text for an empty stream.

### `callcenter.py.orig`

It contains the same four data/session classes and seven functions/methods. `SessionStore.__init__`, `get`, `record`, `_evict`, `_redact_pii`, and `_is_repeat` behave as current versions.

```python
def decide(question, last_answer, history) -> Decision:
```

The old router used broad `_SECRET_RE` and `_ACCOUNT_ACTION_RE` regular expressions for credentials/account incidents, clarified every turn under three words, and otherwise returned a clean question. It had no `_encode_question` or `_probe_score`, and `Decision` had no vector/score fields. The broad regexes handed off recognizable phrases but lacked semantic coverage; replacing them with a narrow credential fast path plus frozen classifier is the snapshot's key significance.

### `rag.py.orig`

It contains `RAGError`, `_post`, `completion_message`, `tool_query`, and `rewrite` with essentially the contracts above, plus the tool schema and old functions:

```python
def retrieve_evidence(query, history=None):
```

It checked business deposits, called `retrieve(query, k=5)`, trusted hits, and returned hits/refusal. It had no configurable k or vector/source-text reuse invariant.

```python
def ask(question, history=None):
```

It built system/history/question, then ran up to three model-selected tool rounds. For each tool call it validated query, rewrote unconditionally, retrieved directly, and appended tool messages. It returned model content/messages once no more tools appeared or a fixed failure after three loops. Snapshot layout includes stray module-level preflight code referring to undefined `question` before the function, so the file is not safely runnable as a module.

### `trust.py.orig`

The intended definitions mirror current `GateResult`, `_fold`, `_looks_like_base64`, `_looks_like_instruction_override`, `input_gate`, `is_business_deposit_question`, and `trusted_hits`. The snapshot cannot be parsed: after the control-character `if`, another `if` appears at the same indentation before a body. It also lacks the final successful `return GateResult(True)` in `trusted_hits()`, so even repairing indentation alone would make accepted evidence return `None`. Current `trust.py` is the corrected authoritative version.

## 20. Cross-file debugging invariants

These are the assumptions most likely to explain failures that appear far from their cause:

- Rate IDs are positional. `boa_embed.ipynb`, `make_eval.py`, `eval.py`, `validate_eval.py`, and `bench_provider.py` all assume the same `rate_tables.jsonl` order.
- Query/index embeddings must both be normalized bge-m3 1,024-float vectors. `retrieve()` does not validate a reused vector; production correctness depends on `decide()` plus `retrieve_evidence()` assertions.
- `generate_turn()` treats `Decision.outcome is None` as the only RAG path. A message string alone does not make a decision terminal.
- History is a flat list of role/content dicts and is bounded only when written through `SessionStore.record()`. Direct RAG/eval calls can supply other shapes/lengths.
- Retrieval returns descending scores and only live statuses. `trusted_hits()` trusts the first score and does not re-check status.
- A `done.sources` list means “evidence supplied,” not verified claim-to-source entailment.
- The same environment key is read at `rag` import, so inspection/evaluation modules importing `rag` can fail before argument parsing.
- Several analysis/benchmark scripts overwrite output files. They are source files documented here, but running them is not read-only.

## 21. Coverage accounting

The 26 requested project files comprise 19 active `.py` files, four `.orig` snapshots, two notebooks, and one Compose file. The active source contains 101 Python `def`/method definitions and seven Python classes across the 19 `.py` files. The scraper notebook adds 17 Python function definitions (counting both sequential `regnum` definitions), and the embedded web client adds five named JavaScript functions. Thus this document covers **123 active named function/method definitions**, plus every active class, anonymous callback behavior where it affects flow, all function-free script bodies/configuration, the notebook cells, and 26 function/method definitions across the three parseable historical snapshots. `trust.py.orig` is covered from its text/diff but cannot contribute a valid AST count because it has a syntax error.
