# BoABot, explained from the ground up

This document describes the repository as it exists at the current checkout. It is written for a reader who has never seen the project or worked with an AI assistant before.

The most important clarification is this:

> BoABot is currently a text-based Albanian banking-information assistant with a web interface and a voice-ready turn protocol. It is not yet a complete voice or telephone system.

The implemented service accepts text, retrieves Bank of Albania material, asks a large language model to write a grounded Albanian answer, streams that answer to the caller, and can signal that a person must take over. A future speech or telephone layer could transcribe a caller's speech into the existing `/turn` request and speak the streamed text back, but no speech recognition, text-to-speech connection, telephone provider, WebSocket audio bridge, or agent-queue integration exists in this repository.

There is no `README.md` in this checkout. `PROJECT_SUMMARY.md`, the two handoff documents, and the three latency reports are the nearest equivalents, but they describe different points in the project's history. This file reconciles those documents with the current code and data.

## 1. What BoABot is trying to solve

BoABot is a portfolio project for an Albanian bank/contact-center use case. It answers two main kinds of informational questions:

- questions about Bank of Albania regulations, such as bank licensing, the Credit Registry, capital, transparency, or credit-risk rules;
- questions about comparative Albanian-bank rates and fees, such as deposit interest or card and loan commissions.

A **call-center bot** is software that handles a customer's turn before or instead of a human agent. In BoABot, a **turn** means one customer question and one assistant response. The current turn arrives as text even though the contract was designed so a future voice gateway can use it.

The project deliberately does not let the language model answer only from what it learned during general training. It uses **RAG**, short for **retrieval-augmented generation**. In plain language, RAG first searches a controlled local library, then gives the matching passages to the language model as reference material. That reduces unsupported guesses and makes citations possible.

It also tries to fail safely. It refuses when the local corpus does not contain dependable evidence, and it marks sensitive or account-specific requests for **handoff**, meaning that a human agent should take over. The current implementation only returns a structured `handoff: true` signal and a safety message; it does not actually transfer a call.

## 2. The big picture

```text
Bank of Albania website
        |
        |  Colab scraper/extraction notebook
        v
manifest.jsonl -> pdf_text.jsonl -> chunks.jsonl
                           rate_tables.jsonl
                                  |
                                  |  bge-m3 embedding notebook on a GPU
                                  v
                         embedded.parquet
                                  |
                                  |  load.py
                                  v
                    PostgreSQL + pgvector database

Browser or future voice gateway
        |
        | POST /turn {question, session_id}
        v
FastAPI (api.py)
        |
        +--> call-center policy and session state (callcenter.py)
        |       |-- refuse unsafe input
        |       |-- clarify short input
        |       |-- repeat last answer
        |       `-- signal human handoff for sensitive intent or PII
        |
        +--> optional follow-up rewrite (rag.py)
        +--> semantic retrieval (retrieve.py -> pgvector)
        +--> evidence gate (trust.py)
        +--> grounded prompt -> OpenRouter language model
        |
        `--> SSE stream: search event, text pieces, final outcome and sources
```

The main pieces are:

| Piece | Plain-language job | Main files |
|---|---|---|
| Corpus pipeline | Crawl and turn public Bank of Albania material into searchable passages | `boa_scraper_v2 (2).ipynb`, `fix_rates.py`, `boa_embed.ipynb`, the corpus `.jsonl` files |
| Vector database | Store passages and their numeric meaning representations | `embedded.parquet`, `load.py`, `db/docker-compose.yml` |
| Retrieval | Find passages closest in meaning to a question | `retrieve.py` |
| Trust gates | Reject unsafe input and weak or mismatched evidence | `trust.py` |
| Call-center policy | Decide whether to clarify, repeat, refuse, answer, or hand off | `callcenter.py`, `handoff_probe.json` |
| Grounded generation | Build the evidence prompt and call the language model | `rag.py` |
| API and web client | Expose `/turn`, stream events, and render a minimal browser chat | `api.py` |
| Evaluation | Test retrieval quality, call policy, handoff classification, and answer grounding | `eval*.py`, `validate_eval.py`, the eval data files |
| Performance work | Measure the server and provider, caching, context size, and voice-relevant delay | `bench_*.py`, `phase3_*.py`, `latency_evidence/` |

## 3. Essential terms

- **Corpus:** the controlled collection of documents the bot is allowed to search. Here it is Bank of Albania regulations plus comparison tables for Albanian-bank rates and fees.
- **Chunk:** a passage small enough to search and send to a model. Regulation chunks usually correspond to an article (`Neni` in Albanian); rate chunks correspond to one table row across banks.
- **Embedding:** a list of numbers representing the meaning of text. Similar questions and passages should have nearby embeddings even when they do not use exactly the same words.
- **bge-m3:** the embedding model used by this project. It produces 1,024 numbers per question or chunk.
- **Vector:** another word for that numeric list. Every stored vector here is normalized to length 1, which makes cosine comparison straightforward.
- **pgvector:** a PostgreSQL extension that stores vectors and can sort rows by vector distance.
- **Cosine similarity:** a measure of how closely two vectors point in the same direction. BoABot converts pgvector's cosine distance into a score with `1 - distance`; higher is more similar.
- **Top-k retrieval:** return the `k` closest chunks. Production uses `k=5`.
- **LLM:** large language model, the component that writes the final natural-language response. Production currently names `deepseek/deepseek-v4-flash` through OpenRouter.
- **OpenRouter:** the remote API gateway used to reach the LLM provider. The environment variable is still named `DEEPSEEK_API_KEY`, but the code sends it to OpenRouter.
- **Grounded answer:** an answer whose claims are supported by retrieved corpus passages.
- **Prompt:** the messages sent to the LLM: behavioral rules, evidence, prior conversation, and the current question.
- **Prompt caching:** provider reuse of an unchanged beginning of a prompt. It can reduce repeated processing cost and, in these measurements, reduced high-percentile latency.
- **SSE:** Server-Sent Events, a simple HTTP streaming format. The server sends lines beginning with `data:` as work progresses instead of waiting for the entire answer.
- **TTFT:** time to first token, meaning delay from request start until the first piece of generated answer text.
- **p50, p95, p99:** latency percentiles. p50 is the median; p95 means 95% of observations were at or below that value. Tail percentiles matter in voice systems because occasional long silences feel broken.
- **PII:** personally identifiable information, such as an email, phone number, or long account-like number.
- **ASR:** automatic speech recognition, which turns voice into text. It is not implemented here.
- **TTS:** text-to-speech, which turns text into audio. It is not implemented here.

## 4. Current data state

The current files and the live local database agree on the following:

| Artifact | Current fact |
|---|---:|
| Regulation chunks in `chunks.jsonl` | 4,049 |
| Rate/fee chunks in `rate_tables.jsonl` | 119 |
| Total rows/vectors in `embedded.parquet` and PostgreSQL | 4,168 |
| Vector dimensions | 1,024 |
| Regulation documents represented by the chunks | 98 |
| Regulation statuses | 189 canonical, 3,685 base, 107 amendment, 68 superseded |
| Database statuses after adding 119 base rate chunks | 189 canonical, 3,804 base, 107 amendment, 68 superseded |
| Raw extracted PDF record pages in `pdf_text.jsonl` | 2,257 |
| Crawl manifest rows | 306: 194 pages and 112 documents |

Some historical prose says 4,152 rows, 4,172 rows, or 2,306 pages. Those values describe earlier pipeline states or do not match the current artifacts. The current Parquet file contains 4,168 unique IDs: `reg_00000` through `reg_04048` and `rate_0000` through `rate_0118`. `embedded_old.parquet` is the older 4,172-row version with 123 rate chunks.

Regulation status is important correctness logic:

- `canonical` means an integrated or consolidated version intended to be current;
- `base` means an ordinary base document;
- `amendment` means a change decision that should not be retrieved by itself as current law;
- `superseded` means an older version known to have been replaced.

Production retrieval permits only `canonical` and `base`. That prevents the model from mixing current text with amendments or replaced provisions. The raw excluded rows remain in the database for traceability.

## 5. One request, from start to finish

### 5.1 Startup

When Uvicorn starts `api:app`, importing `rag.py` immediately reads `DEEPSEEK_API_KEY` from the environment. If it is absent, the service cannot even import.

FastAPI's startup hook calls `retrieve.warmup()`. Warmup:

1. loads the CPU copy of `BAAI/bge-m3` if necessary;
2. embeds a throwaway Albanian phrase;
3. opens the PostgreSQL connection pool;
4. executes one small pgvector query;
5. resets the embedding-reuse counters so warmup is not counted as real traffic.

This moves model loading and the first database connection out of the first customer turn. Phase 1 measured first-SSE delay of about 6,008 ms without warmup versus 165 ms after warmup, although those were single observations rather than distributions.

### 5.2 The client submits a turn

The browser sends:

```json
{
  "question": "Kush administron Regjistrin e Kredive?",
  "session_id": null
}
```

`TurnReq` accepts questions from 2 through 1,500 characters and an optional session ID of at most 128 characters. The server trims leading and trailing whitespace. Conversation history is not accepted from the browser; the server owns it.

`SessionStore.get()` finds the requested in-memory session or creates a random hexadecimal ID. Sessions retain at most 12 messages, expire after one hour, and are capped at 1,000. Cleanup happens when a session is fetched. This is thread-protected but process-local: restarting the server loses all history, and multiple workers would each have a different view. Production would need shared storage such as Redis.

### 5.3 Deterministic and semantic call-center routing

`callcenter.decide()` runs before retrieval or generation. The order matters:

1. `trust.input_gate()` rejects control characters, heavily percent-encoded text, strings that look like printable Base64, and a small set of English or Albanian prompt-injection phrases.
2. Repeat phrases such as “ma përsërit” return the previous answer without searching or calling the LLM.
3. A fast regular expression catches explicit credential incidents such as a compromised PIN, CVV, or OTP and requests human handoff.
4. Business-deposit questions are refused because that category is absent from the corpus. History is considered, so “Po për biznese?” after a deposit turn is also caught.
5. Email addresses, Albanian-looking phone numbers, and long account-like numbers trigger handoff with `pii_redacted: true`. The raw sensitive turn is not stored; the session receives a safety placeholder. Despite the helper name `_redact_pii`, the redacted text is not sent onward because the function returns a handoff immediately.
6. A one-word request is clarified without embedding.
7. Longer text is embedded once with bge-m3 and compared with a frozen handoff-intent classifier.
8. If the semantic handoff score passes its threshold, the request is handed off. A two-word non-handoff request is clarified; three or more words continue to retrieval.

The semantic classifier is a one-nearest-neighbor design. It compares the question embedding with 233 frozen training embeddings stored in `handoff_probe.json`. Those rows contain 191 positive handoff examples and 42 negative examples. The nearest row must be positive, and the best-positive similarity minus the best-negative similarity must be at least `0.04658478498458862`.

The training phrase bank covers lost cards, stolen cards, unauthorized activity, block/freeze requests, secret credentials, and ordinary negative examples. The grouped held-out evaluation avoids putting trivial spelling/diacritic variants of the same phrase in both training and test. On the repository's current test split, Method B caught 98.8% of positive examples with 0/18 negative false positives; the one miss was “A mundeni te kontrolloni, me mungojne para?”. That is a useful measured result, not a guarantee on real callers.

An early policy result streams one `token` event containing a fixed Albanian message, then a `done` event. Possible outcomes are:

- `clarify`: the request needs more detail;
- `unsupported`: the bot refuses because the input or topic is unsupported;
- `handoff`: a human should take over;
- `repeat`: replay the last answer;
- `answer`: a grounded model answer was produced.

### 5.4 Rewrite only contextual follow-ups

If routing allows the question, `rag.needs_rewrite()` decides whether it is understandable without conversation history. No-history questions are never rewritten. With history, short questions or those beginning with contextual words such as “po”, “dhe”, “kjo”, or “ndërsa” are candidates. Explicit names, numbers, and recognizable banking terms often let the original question pass unchanged.

For an elliptical follow-up such as “Po për 24 muaj?”, `rag.rewrite()` asks the remote LLM to turn the last question plus up to four recent messages into one standalone question. The original customer wording still goes into the answer-generation prompt; the rewrite is only the search query.

This extra LLM call is expensive, so the heuristic is a latency feature as well as a retrieval feature. The phase-2 40-turn quality check found:

- explicit follow-ups: direct retrieval 16/20, rewritten retrieval 15/20;
- elliptical follow-ups: direct retrieval 0/20, rewritten retrieval 14/20.

That is why rewriting is conditional rather than always on or always off.

### 5.5 Emit the first search event

The API immediately streams a `tool` SSE event containing the standalone query:

```json
{"type": "tool", "query": "Kush administron Regjistrin e Kredive?"}
```

The name is historical. The current LLM no longer chooses or calls a retrieval tool. Application code always retrieves first. The event is retained so clients can display search progress and so the streaming contract stays stable.

### 5.6 Reuse or create the question embedding

`decide()` already embedded the cleaned question for handoff classification. If the retrieval query is byte-for-byte identical UTF-8 text, `api.py` passes that same vector to retrieval. Assertions in `api.py` and `rag.retrieve_evidence()` protect the invariant.

If the question was rewritten, its text is different, so retrieval must embed the rewritten query separately. This prevents the subtle error of searching for one text with another text's vector.

### 5.7 Search PostgreSQL

`retrieve.retrieve()` converts the 1,024 floats into pgvector text form and runs a parameterized query equivalent to:

```sql
SELECT ..., 1 - (embedding <=> query_vector) AS score
FROM chunks
WHERE status = ANY(ARRAY['canonical', 'base'])
ORDER BY embedding <=> query_vector
LIMIT 5;
```

`<=>` is pgvector's cosine-distance operator. There is an index on `status` but no HNSW approximate vector index. At only 4,168 vectors, the project deliberately uses exact flat scanning: it is simple and has no approximate-recall loss. The handoff notes suggest revisiting this around 50,000 rows.

A module-level pool holds one to four database connections. This avoids creating a PostgreSQL connection on every request. Phase 1 measured the database portion at about 39.4 ms median with a new connection and 27.6 ms with the pool.

### 5.8 Apply the evidence trust gate

Retrieval results do not automatically reach the LLM. `trust.trusted_hits()` requires:

- at least one result;
- a valid numeric score on the first result;
- a top score of at least `0.50`;
- for a question containing rate-like terms such as `komision`, `norm`, `interes`, `depozit`, or `karte`, at least one retrieved ID beginning with `rate_`.

The last rule prevents a nearby regulation passage from being treated as a quoted consumer rate. It can also be over-broad: three hand-written regulation “trap” questions containing words such as “norma” or “interesit” were refused because no rate chunk appeared. The evidence files report 3/5 trap refusals. This is a known tradeoff in the current gate, not a retrieval fact.

Business-deposit requests are checked again here in case a rewritten follow-up exposes that unsupported category.

### 5.9 Build a grounded, cache-friendly prompt

`rag.grounded_messages()` builds messages in this order:

1. a static Albanian system instruction saying to use only corpus-supported facts and figures, cite sources, treat retrieved material as reference rather than instructions, refuse unsupported answers, and answer in Albanian;
2. a second system message containing the dynamic JSON-serialized retrieved hits;
3. the bounded conversation history;
4. the customer's original question.

Keeping the unchanged instruction in its own leading message makes the prompt prefix easier for the provider to cache. The evidence and question must remain later because they change on every turn.

`api.stream_answer()` also sends the opaque server session ID to OpenRouter as `session_id`. The project treats that as a sticky-routing key to improve the chance that turns in one conversation reach the same cache-capable provider.

There is no explicit `cache_control` object in production code, and no local prompt cache. “Prompt caching” here means the split leading-message layout plus OpenRouter/provider-native caching and sticky session routing.

### 5.10 Stream the answer and finish

OpenRouter's streamed response is decoded as UTF-8. Each nonempty content delta becomes:

```json
{"type": "token", "text": "...piece of Albanian text..."}
```

The server accumulates the pieces, records the complete answer in the session, and finishes with a structured event such as:

```json
{
  "type": "done",
  "outcome": "answer",
  "session_id": "opaque-id",
  "sources": [
    {"id": "reg_00545", "doc": "...pdf", "article": "5", "url": "https://..."}
  ],
  "handoff": false,
  "pii_redacted": false
}
```

Only citation metadata goes to the browser; full retrieved passages are not returned in `sources`. The source list means “these chunks were supplied,” not “the model demonstrably used every source.” There is no post-generation citation verifier in the production request path.

If the provider returns no answer text, the outcome becomes `unsupported`. If retrieval, parsing, the provider, or any unexpected code path fails, `/turn` fails closed to the fixed human-agent message and `handoff: true`. This is safe, but it also means an infrastructure outage is reported through the same product outcome as a genuinely sensitive request.

## 6. The corpus and embedding pipeline

### 6.1 Crawling

`boa_scraper_v2 (2).ipynb` is the original exploratory Google Colab notebook. It crawls selected Bank of Albania sections using a polite one-second delay, a custom User-Agent, a robots.txt gate, and breadth-first search constrained to each seed path.

It stores page text and linked documents under `/content/boa_corpus` in Colab and appends crawl metadata to `manifest.jsonl`. The CSS selector `#MainContentWrapper` was chosen to remove repeated sidebar navigation. Some accordion pages required Playwright and `text_content()` because collapsed or JavaScript-injected text was missing from simpler extraction attempts.

The notebook is exploratory rather than a clean, single-pass production job: later cells retry or replace earlier strategies, two Playwright cells are duplicates, it contains both full and incremental embedding approaches, and its final SSH example contains a literal example password. Do not run every cell blindly or expose that SSH tunnel.

### 6.2 Table extraction

Five comparison pages are parsed with `pandas.read_html()`. The notebook identifies the widest table, resolves bank short codes through a legend table, follows repeated category headers, and creates one rate chunk per fee/rate row. Each chunk repeats the source, category, item, and all available bank-value pairs.

This unusual shape is intentional. A single rate chunk represents one item across several banks, so semantic retrieval finds the relevant fee or maturity band; the LLM then reads the requested bank's value from that chunk. Consequently, naming a bank often does not discriminate between rate chunks, and retrieval recall mainly tests item/category matching.

The scraper once mislabeled maturity ranges such as `13-24` as item names and retained bare `Muaj` header rows. `fix_rates.py` removes the header rows and renames numeric bands to text such as `maturitet 13-24 muaj`. The correction is still downstream of the scraper's extraction cell. Re-running table extraction can recreate the defect, so the clean embedding notebook has a gate that refuses to continue if no corrected `maturitet` labels are present.

### 6.3 PDF extraction and chunking

The scraper notebook triages PDFs with PyMuPDF, estimates text density, excludes scanned or blank files, and extracts the usable PDF text into `pdf_text.jsonl`. It then splits regulations on lines matching `Neni N` or `Neni N/1`. Long articles are subdivided; documents without enough article markers fall back to overlapping character windows.

Every regulation chunk repeats its document title and article number so a retrieved fragment remains understandable and citable. The current 4,049 regulation chunks have a median text length of 1,566 characters; 3,587 contain an article value.

Status tagging is heuristic: filenames containing “integruar” or “konsoliduar” become canonical; apparent change decisions become amendments; other documents become base. A specific older regulation 63 document is manually demoted to superseded. Because legal/current-version correctness is high stakes, these tags should be reviewed rather than assumed perfect.

Six scanned-image PDFs were described as excluded in the handoff notes, and OCR was deferred. The raw files downloaded by the notebook are not present in this repository, so this checkout cannot independently re-run extraction without reacquiring or transferring the corpus.

### 6.4 Embedding

`boa_embed.ipynb` is the cleaner re-embedding notebook. It must run on a GPU environment such as Colab; the handoff documents warn not to embed the entire corpus on the CPU-only VPS.

It loads regulation and corrected rate JSONL, gives rate rows enumeration-based IDs, encodes all text with `BAAI/bge-m3` and `normalize_embeddings=True`, stores each float32 vector as bytes, and writes `embedded.parquet`. The notebook's prose says to expect roughly 4,152 rows, which is stale; the current input and output count is 4,168. The checked-in notebook copy has no recorded executions or outputs.

The index and query vectors must come from the same model and normalization scheme. Changing the embedding model requires re-embedding the corpus; otherwise distances are meaningless.

### 6.5 Loading

`load.py` reads `embedded.parquet`, drops and recreates the `chunks` table, and inserts every row. This makes the script repeatable but destructive to the current table. It creates only a status index, not a vector index.

The script assumes the `vector` PostgreSQL type already exists; it does not execute `CREATE EXTENSION vector`. A completely fresh database may therefore need the extension enabled before `load.py` can create its table.

## 7. Trust, evaluation, and what the scores mean

### 7.1 Retrieval evaluation

`eval.py` retrieves the top 10 for each question and reports several different notions of success:

- exact chunk-ID recall for all rows;
- for regulation questions, document-and-article recall as the primary metric;
- regulation exact-ID recall as a stricter secondary metric;
- same-document recall as a looser secondary metric;
- median and p95 retrieval time;
- exact-ID misses, broken down by `reg_` and `rate_` prefixes;
- whether rate questions name a bank that actually appears in the gold rate chunk;
- whether special trap questions were refused by the trust gate.

Article-level regulation scoring is more honest than exact chunk ID when a long article was subdivided: two chunks from the same document and article can both contain an answer. Same-document recall is useful but can over-credit the wrong article.

The default sets are labeled `old (buggy)`, `generated`, and `handwritten` in code. The “old” label is hard-coded and should not be read as a statement that every row currently has stale IDs. Generated questions overlap their source chunks heavily, so they are mostly a pipeline sanity test. The handwritten set is harder and closer to real phrasing, but still only 40 questions.

The preserved current-family evaluation reports the handwritten regulation results around RegArt@1 0.550, RegArt@5 0.650, RegDoc@1 0.800, and RegDoc@5 0.950. Exact-ID misses do not mean the answer was necessarily absent at document or article level. The five trap questions intentionally reveal that the rate-family safety rule can reject regulations containing words like “normë”.

### 7.2 Eval-set integrity

`validate_eval.py` checks that generated and handwritten gold IDs exist in the database, use allowed statuses, do not repeat, and have valid rate-bank relationships. It also requires the handwritten set to contain 20 rate and 20 regulation questions and no more than two regulation questions per source document.

It hard-codes the expected current rate-table row count as 119. That catches mismatched files, but it must be updated deliberately if the corpus changes.

### 7.3 Call policy

`eval_calls.py` walks 16 deterministic one- or two-turn scenarios from `eval_calls.jsonl`. It checks only routing labels such as `clarify`, `unsupported`, `handoff`, `repeat`, or `model`; it does not call the answer LLM. Because semantic routing embeds several turns, it still loads bge-m3.

The cases cover ambiguous input, supported regulation, the business-deposit gap, repeat, lost/stolen-card and fraud wording, account actions, credential incidents, PII, encoded override text, and an off-topic question. The off-topic case expects `model`, because the call-center router does not decide topical support; retrieval and the trust gate do that later.

### 7.4 Handoff classifier evaluation

`eval_handoff.py` compares three methods:

- Method A: similarity to diverse positive anchors;
- Method B: nearest-neighbor positive-versus-negative class margin;
- Method C: logistic regression.

It demonstrates why the old row-stratified split was misleading: 64% of its test rows belonged to phrase families also seen in training. `handoff_split_grouped.json` keeps normalized phrase families together and has 233 training rows and 99 test rows with zero family overlap.

Method C reached 100% positive recall on the grouped test but produced 2/18 false positives, violating the chosen 2% false-positive ceiling. Method B reached 98.8% positive recall with 0/18 false positives and was exported. The script also verifies that the frozen vectors, labels, margin, and production `decide()` outputs still match the selected method.

### 7.5 Answer-quality evaluation

`phase3_quality.py` makes live, paid model calls for the 20 handwritten rate questions. It checks whether every number in an answer appears somewhere in the retrieved evidence after normalizing formats such as `1'000.00`, `1,000`, `0.00`, and `0.00%`. It also checks whether a retrieved document name or article appears in the answer and tests five empty-evidence refusal prompts.

This is useful but deliberately limited. Numeric grounding does not prove that the model chose the right row, attached the right label, answered the exact question, or included all necessary information. The phase-3 report explicitly notes examples that passed numeric grounding while using an arguably wrong service/maintenance label or listing unlabeled values.

## 8. Latency work and the three phases

### 8.1 What the benchmark metrics mean

`bench_turn.py` measures the full local `/turn` path:

- first SSE event: normally the early search-progress event;
- first token: first generated answer text;
- first sentence: the first streamed sentence-ending punctuation, or completion time for a punctuation-free answer;
- done: receipt of the final event;
- a model-neutral lexical-token throughput proxy.

The benchmark cycles five rate and five regulation questions. With `--history`, it creates a new session, sends an unmeasured primer, then measures the target turn. The primer's own time and cost are not included in the reported target latency. Sentence detection is a regular expression, so abbreviations such as `Nr.` can sometimes be mistaken for a complete first sentence.

`bench_provider.py` bypasses BoABot, PostgreSQL, FastAPI, routing, and trust checks. It constructs prompts from frozen top-eight chunk-ID fixtures and calls OpenRouter directly. It can compare models, `k` values, split versus combined prompt layouts, current versus trimmed system prompts, reasoning settings, and sticky session IDs. It reports provider TTFT, completion time, token use, provider cache telemetry, observed cost, histograms, and prompt-size correlation.

Both scripts can make many paid external calls. Their default `N=100` is intentional for p95/p99 claims but should not be run casually.

### 8.2 Phase 1: remove avoidable round trips

The first latency phase changed the architecture from model-selected tool calling to application-controlled, always-first retrieval:

- removed a preliminary LLM call whose only job was deciding to call retrieval;
- made follow-up rewriting conditional;
- added the PostgreSQL connection pool;
- added startup warmup;
- reused the call-center question embedding in retrieval when safe.

The largest stable visible change was first-SSE latency, from a historical 1,856 ms median to roughly 145–170 ms in small runs. The provider's first generated token remained slow and highly variable.

This is why current `rag.py` no longer exposes a `TOOLS` schema even though older handoff documents and `.orig` snapshots describe tool calling. The current system pre-retrieves and sends one grounded generation request.

### 8.3 Phase 2: prompt, context, and providers

Phase 2 added or measured:

- the provider-only benchmark;
- first-sentence timing for voice relevance;
- prompt-prefix caching structure and sticky session routing;
- `k=3`, `k=5`, and `k=8` context comparisons;
- a shorter system-prompt experiment;
- DeepSeek, Gemini, Mistral, and GPT-4.1 Mini provider comparisons;
- answer-quality comparisons for the two faster candidates.

The trimmed prompt was not shipped because it reduced citations and refusals while saving only about 74 prompt tokens. Production remains at `k=5` because smaller context did not show a clean latency benefit that justified a retrieval tradeoff.

Phase 2 recommended `google/gemini-3.1-flash-lite` for a future voice path because of its tighter provider tail, but deliberately did not change production's default model.

### 8.4 Phase 3: test the caching claim and Gemini end to end

Phase 3 repeated the important comparisons at `N=100` and preserved raw JSON. In that matched observation, the cache-friendly split/sticky layout improved DeepSeek provider TTFT compared with combined/no-session by about 315 ms at p50, 5,180 ms at p95, and 8,449 ms at p99. It did not eliminate the slow mode, and even the “cache off” run showed incidental native cache hits, so the experiment is an observed association rather than a perfectly controlled provider switch.

The full `/turn` results in the final report were:

| Model and mode | First token p50 / p95 | First sentence p50 / p95 | Done p50 / p95 |
|---|---:|---:|---:|
| DeepSeek, empty history | 4,912 / 16,109 ms | 6,554 / 20,369 ms | 8,848 / 56,454 ms |
| DeepSeek, with history | 4,728 / 18,125 ms | 6,675 / 20,613 ms | 11,727 / 65,624 ms |
| Gemini benchmark override, empty | 844 / 5,986 ms | 1,062 / 6,992 ms | 1,982 / 8,664 ms |
| Gemini benchmark override, history | 789 / 1,562 ms | 1,049 / 2,001 ms | 1,506 / 3,726 ms |

Gemini also streamed much faster in the lexical proxy and matched the requested 20/20 numeric-grounding and 20/20 citation checks. The report judged both models' Albanian usable, with Gemini generally more direct.

The Gemini run was a benchmark-only in-memory override performed before importing `api.py`. There is no runtime provider-switching abstraction or environment setting in this repository. Production remains hard-coded to DeepSeek. A real switch requires changing code or adding configuration and then revalidating behavior.

The phase-3 voice calculation added an assumed 300 ms TTS start delay to first-sentence time. That assumption came from the report's engineering interpretation of Azure streaming guidance, not a measured SLA on this host. No TTS call was executed. With that assumption, neither model met a strict 1.5-second p95 first-audio goal; Gemini with history met a 2.5-second p95 goal in that run, while Gemini first turns and all DeepSeek cases did not.

## 9. Getting started

These instructions are inferred from the code and handoff notes. The repository has no `requirements.txt`, `pyproject.toml`, Dockerfile for the API, or automated setup script, so a fresh setup is not fully pinned or one-command reproducible.

### 9.1 Prerequisites

- Python 3.11 is the documented environment.
- Docker with Compose access is needed for PostgreSQL.
- Enough memory for the CPU bge-m3 model; the handoff notes describe roughly 2.2 GB resident use.
- An OpenRouter API key placed in `DEEPSEEK_API_KEY`.
- The local corpus artifacts, especially `embedded.parquet`, which are ignored by Git and may not exist in a fresh clone.

The currently installed environment shows these important packages: FastAPI, Uvicorn, Requests, Pydantic 2, NumPy, pandas, fastparquet, psycopg 3, psycopg-pool, sentence-transformers, PyTorch, transformers, and scikit-learn. A reasonable unpinned installation based on imports is:

```bash
python3.11 -m venv .venv
.venv/bin/pip install fastapi uvicorn requests pydantic numpy pandas fastparquet \
  'psycopg[binary]' psycopg-pool sentence-transformers scikit-learn
```

That command is an inferred dependency list, not a lockfile replacement.

### 9.2 Configure the key

```bash
cp .env.example .env
# Replace the masked placeholder with an OpenRouter key.
set -a
source .env
set +a
```

Do not commit `.env`. The variable's historical name is misleading: it is used as a bearer token for `https://openrouter.ai/api/v1/chat/completions`.

### 9.3 Start PostgreSQL

```bash
docker compose -f db/docker-compose.yml up -d
```

The database listens only on `127.0.0.1:5433`; host port 5432 was deliberately avoided. The Compose file uses development credentials `boa` / `boa` and persists data under `db/pgdata`.

On a completely new database, ensure pgvector is enabled because `load.py` assumes the `vector` type exists:

```bash
docker exec boabot-postgres psql -U boa -d boa \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

### 9.4 Load or check the corpus

First inspect without changing the database:

```bash
.venv/bin/python main.py
```

Then, only if loading/replacing the database is intended:

```bash
.venv/bin/python load.py
```

`load.py` drops the existing `chunks` table before recreating it. It should print `loaded: (4168, 4168)` for the current artifacts.

### 9.5 Run the service

```bash
set -a
source .env
set +a
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8100
```

Running `python api.py` alone does not start Uvicorn; `api.py` has no executable main block. Bind to loopback unless authentication, a reverse proxy, and deployment hardening have been added. The API currently has no authentication and permits CORS from every origin.

Open `http://127.0.0.1:8100/` for the minimal chat page, or test directly:

```bash
curl http://127.0.0.1:8100/health

curl -N -X POST http://127.0.0.1:8100/turn \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"question":"Kush administron Regjistrin e Kredive?","session_id":null}'
```

Keep the `session_id` from the `done` event and send it on the next turn to exercise history and repeat behavior.

### 9.6 Run checks

With the database available:

```bash
.venv/bin/python validate_eval.py
.venv/bin/python eval.py
.venv/bin/python eval_calls.py
.venv/bin/python eval_handoff.py
```

`eval.py` and the classifier checks load bge-m3 and can use substantial memory. `eval_handoff.py` also needs scikit-learn. The provider and quality benchmarks need the API key, internet access, and money; the full `/turn` benchmark additionally needs the server running:

```bash
.venv/bin/python bench_turn.py -n 10
.venv/bin/python bench_turn.py -n 10 --history

# Paid provider-only example; production caching layout requires these flags.
.venv/bin/python bench_provider.py -n 10 --vary --layout split \
  --session-id boabot-benchmark --size-probe-requests 0
```

Do not use a small `N` to make reliable p95 or p99 claims.

## 10. Every source and configuration file

### Runtime and data-loading code

#### `api.py`

The current FastAPI application and embedded HTML/JavaScript client. It defines `/health`, `/`, and `/turn`; registers retrieval warmup/shutdown; validates requests; orchestrates policy, rewrite, retrieval, trust, prompt construction, and streaming; returns citation metadata and structured outcomes; and converts errors to human handoff.

Why it exists: it is the integration point that turns the lower-level modules into a usable service and defines the contract a future audio or telephony bridge should call.

Gotchas: there is no `/chat` route in current code despite older comments/docs; CORS is open; there is no auth or rate limiting; calls and generators are synchronous; `PAGE` is a demo rather than a production UI; and a `handoff` event is only a signal, not queue transfer.

#### `callcenter.py`

Owns deterministic conversation outcomes, server-side session memory, PII detection, repeat and clarification behavior, the credential fast path, and the frozen semantic handoff classifier. It also creates the question embedding that retrieval can reuse.

Why it exists: sensitive call-center routing should not depend on a generative model deciding whether to be safe.

Gotchas: state is in one Python process; classifier coverage is limited to its phrase bank; some incidents can still be missed; PII detection uses patterns rather than a general entity detector; and any production multi-worker deployment needs shared state.

#### `trust.py`

Contains input and retrieval guardrails plus fixed Albanian refusal messages. It detects a narrow set of encoded/instruction-override inputs, recognizes the missing business-deposit category, enforces a 0.50 top-score threshold, and applies the rate-chunk-family rule.

Why it exists: these checks are deterministic, testable, and run before unsupported text can influence generation.

Gotchas: this is not a comprehensive security system; obfuscated injections outside its patterns can pass, and the rate-term heuristic can reject valid regulation questions.

#### `retrieve.py`

The one production semantic-search function. It lazily loads bge-m3 on CPU, manages a one-to-four-connection PostgreSQL pool, supports safe vector reuse, records reuse statistics, warms dependencies, filters status, and runs exact top-k cosine search.

Why it exists: every caller should use one consistent embedding model, query, status filter, and result shape.

Gotchas: the DSN is hard-coded instead of imported from `config.py`; vector serialization rounds each query component to six decimal places; model loading is large; and startup fails if the model or database is unavailable.

#### `rag.py`

Defines the OpenRouter endpoint, hard-coded DeepSeek model, system and rewrite prompts, provider error handling, follow-up rewrite heuristic, grounded-message layout, retrieval trust wrapper, and a non-streaming `ask()` helper.

Why it exists: it separates RAG/prompt/provider logic from HTTP and call-center policy.

Gotchas: importing it requires `DEEPSEEK_API_KEY`; `ask()` does not run `callcenter.decide()` or server session logic and is therefore not equivalent to `/turn`; `tool_query()` remains as a validated historical helper but the current production path does not call it; and provider/model selection is not configuration-driven.

#### `load.py`

Converts `embedded.parquet` to the PostgreSQL `chunks` table. It creates the schema, a status index, inserts the vectors, and prints row/vector counts.

Why it exists: Parquet is the portable embedding artifact; PostgreSQL is the online query store.

Gotchas: it drops the table, loads all rows in memory, hard-codes the DSN, and assumes the pgvector extension already exists.

#### `config.py`

Contains only the local PostgreSQL DSN. `validate_eval.py` and ad-hoc examples can import it, but the current `retrieve.py`, `load.py`, `eval.py`, and `validate_eval.py` each duplicate the same string rather than consistently using this module.

#### `main.py`

A six-line Parquet inspection utility. It prints shape, columns, the first vector's dimensions and norm, and status counts.

Why it exists: it is a quick artifact sanity check. It is not the application entry point.

#### `fix_rates.py`

Patches `rate_tables.jsonl`: removes bare `Muaj` rows and relabels pure numeric ranges as `maturitet ... muaj`, rebuilding each changed chunk's header text.

Why it exists: the scraped table structure once produced semantically poor and misleading labels.

Gotchas: it rewrites the file in place, and the underlying extraction notebook can regenerate the bad source labels. Back up or verify the data before using it on a new corpus revision.

#### `make_eval.py`

Builds `eval_generated.jsonl` by assigning rate IDs according to file order, extracting actual bank names from text, shuffling deterministically with seed 0, and writing up to 40 answerable rate questions.

Why it exists: it creates a gold-ID retrieval smoke test tied to the current rate file.

Gotchas: enumeration order must exactly match embedding time; questions are derived from chunk labels and therefore overestimate real-world generalization; output deliberately omits Albanian diacritics; and changing `rate_tables.jsonl` requires regeneration.

### Evaluation code

#### `eval.py`

Runs retrieval against one or more JSONL sets and prints article, exact-ID, document, latency, miss, bank-consistency, prefix, and trap-gate metrics.

Why it exists: it makes retrieval changes measurable and exposes different meanings of “correct.”

Gotchas: labels such as `old (buggy)` are historical constants; its compact prefix totals mean exact gold found somewhere in the top 10, not per-k recall; and the hand-written set is still small.

#### `validate_eval.py`

Checks eval-file integrity against both the current 119-row rate file and PostgreSQL. It verifies gold existence/status, duplicate IDs, rate-bank matching, handwritten 20/20 composition, and regulation-document diversity.

Why it exists: silent ID drift between data, Parquet, database, and eval files previously wasted runs and invalidated scores.

Gotchas: it is coupled to the exact row count and local database; it uses another hard-coded DSN; and a legitimate corpus update requires updating expectations and regenerating evaluations together.

#### `eval_calls.py`

Checks the 16 deterministic call-policy scenarios without invoking answer generation. It simulates session history and compares route sequences with expected labels.

Why it exists: safety and escalation behavior should be regression-tested separately from provider variability.

Gotchas: a route labeled `model` only means “continue past call-center policy”; it does not guarantee retrieval or generation will answer.

#### `eval_handoff.py`

Re-embeds the 332 handoff phrases, audits train/test family leakage, trains and compares anchor, nearest-neighbor, and logistic methods, verifies the exported Method B artifact, and checks production `decide()` against held-out predictions.

Why it exists: it provides evidence for the semantic human-handoff router and prevents an easy-but-misleading split from inflating performance.

Gotchas: it is an evaluation/training script, not invoked per request; it requires scikit-learn and substantial embedding-model memory; and it expects exact hashes and frozen artifacts.

### Benchmark and phase-3 code

#### `bench_turn.py`

Runs sequential `/turn` requests, optionally primes history, captures SSE timings, counts a model-neutral lexical token proxy, prints percentiles and a first-token histogram, and can save raw JSON.

Why it exists: it measures what a client actually experiences, including routing, embedding, database, provider, and streaming.

Gotchas: `--history` also makes an unmeasured primer request for every measured request; the benchmark incurs model costs; one bad upstream outlier can be very long; and its sentence boundary is heuristic.

#### `bench_provider.py`

Calls OpenRouter directly with ten frozen real-looking prompt fixtures. It supports model, context size, prompt version, message layout, reasoning, sticky session, completion cap, and raw JSON options, and fetches live model pricing.

Why it exists: it separates provider delay from BoABot's local overhead and supports controlled phase-2/3 comparisons.

Gotchas: frozen chunk IDs tie it to the current corpus; its default `layout=combined` is not production's cache-friendly split; prices and providers are live external facts; and runs are paid.

#### `phase3_analyze.py`

Recomputes phase-3 percentile tables from the four saved `/turn` JSON files and two provider-cache JSON files. It reconciles punctuation-free answers, computes generation proxies and fast/slow splits, adds a fixed 300 ms TTS assumption, prints tables, and writes `phase3_analysis.json`.

Why it exists: report claims can be reproduced from raw observations rather than copied by hand.

Gotchas: running it rewrites an evidence artifact; its TTS value is an assumption; and the lexical token regex is not any provider's billing tokenizer.

#### `phase3_quality.py`

Runs live model answer-quality checks for 20 rate questions, direct empty-evidence refusals, and optional end-to-end `/turn` policy refusals, then writes detailed JSON.

Why it exists: a faster provider is unacceptable if it invents figures, drops citations, ignores refusals, or writes poor Albanian.

Gotchas: numeric inclusion is weaker than semantic correctness, citation matching is string-based, calls are paid, and importing `rag.py` requires the key even when only inspecting helpers.

#### `phase3_build_report.py`

Builds `latency_evidence/FINAL_REPORT_PHASE3.md` from saved quality JSON and eight full console transcripts. It injects three side-by-side Albanian answer pairs and the complete acceptance outputs.

Why it exists: it makes the final report reproducible and self-contained.

Gotchas: running it overwrites the final report and assumes every named evidence file exists.

### Historical `.orig` snapshots

These are ignored historical copies, not alternative modules to import:

- `api.py.orig` preserves the earlier `/chat` endpoint and model-driven tool loop alongside an early `/turn` path. Its embedded JavaScript has a visibly missing closing brace around handoff handling, so it should not be treated as a clean runnable backup.
- `callcenter.py.orig` is the earlier regex-only handoff router, before the frozen semantic classifier and embedding reuse fields.
- `rag.py.orig` is the earlier tool-calling RAG design. This saved copy also contains stray module-level code referring to an undefined `question`, so it is not runnable as-is.
- `trust.py.orig` is a broken intermediate snapshot with malformed indentation/order in `input_gate()` and no final success return from `trusted_hits()`.

They exist to show evolution and support diffs. The current non-`.orig` files are authoritative.

## 11. Every data and model artifact

### Corpus artifacts

#### `manifest.jsonl`

The 306-row crawl ledger: URL, section, type (`page` or `doc`), title, and Colab path, plus character or byte counts. It records 194 pages and 112 documents across 13 seed sections.

Why it exists: resumable crawling and provenance. Gotcha: paths point to `/content/boa_corpus`; the downloaded `pages/` and `docs/` are not present here.

#### `pdf_text.jsonl`

The 98 usable PDF extraction records with URL, section, title, page count, and full text. Current records total 2,257 pages.

Why it exists: it is the intermediate from raw PDF to re-chunkable text. Gotcha: it contains a large amount of source material, has no row-level `chars` field, and the current page total differs from some prose documents.

#### `chunks.jsonl`

The 4,049 regulation chunks with `id`, `doc`, `article`, `status`, `section`, `url`, and `text`. IDs are order-dependent. All four status classes are retained.

Why it exists: this is the text and metadata fed into regulation embedding. Gotchas: it is large, ignored by Git, carries public-source copyright concerns, and status tagging was partly heuristic/manual.

#### `rate_tables.jsonl`

The 119 corrected fee/rate row chunks with source, category, item, URL, and self-contained table text. It contains 15 current `maturitet ... muaj` labels and no bare `Muaj` rows.

Why it exists: tables need a different structure from prose regulations. Gotchas: it has no as-of date, so figures can silently become stale; rate IDs are assigned later by row order; and the scraper can regenerate the old labeling defect.

#### `embedded.parquet`

The current 4,168-row portable index source. Columns are `id`, `doc`, `article`, `status`, `section`, `url`, `text`, and raw float32 `embedding` bytes. Every vector is 1,024-dimensional and normalized.

Why it exists: it transfers GPU-produced embeddings to the CPU/database host and supports repeatable database loads. Gotchas: it is roughly 19 MB, ignored by Git, and must stay aligned with the JSONL ordering/model.

#### `embedded_old.parquet`

The older 4,172-row artifact: the same 4,049 regulation chunks plus 123 old rate chunks. It is retained only as history and ignored by Git. Do not load it when using the current 119-row rate/eval files.

### Retrieval eval data

#### `eval_retrieval.jsonl`

Forty rate questions with gold IDs and URLs. `eval.py` labels this set `old (buggy)`, reflecting the earlier generator/data-quality history. The preserved baseline output found all exact gold IDs in top 10 but detected seven rate-bank inconsistencies.

#### `eval_generated.jsonl`

Forty rate questions produced by the current `make_eval.py`, using bank names observed in each row. It is the cleaner generated rate set and remains tightly coupled to chunk wording/order.

#### `eval_handwritten.jsonl`

Forty manually phrased questions: 20 rate and 20 regulation. Five regulation rows carry `trap: true` to test the trust gate's rate-word ambiguity.

Why it exists: it is harder and more realistic than templated questions. Gotcha: 40 examples are still too few for broad production claims.

#### `eval_faq.jsonl`

Thirteen institution-authored question/answer pairs scraped from Bank of Albania FAQ pages, with section and URL but no `gold_id`.

Why it exists: it is independent natural Albanian quality material. Gotcha: because it lacks chunk gold labels, default `eval.py` does not score it; phase-2 model-quality work used it instead.

### Call-center and handoff data

#### `eval_calls.jsonl`

Sixteen named multi-turn routing scenarios. Each row contains input `turns` and expected route labels.

Why it exists: stable call-center acceptance cases. Gotcha: it evaluates routing, not answer truth or live handoff transfer.

#### `handoff_phrases.jsonl`

The 332-phrase intent bank: 52 lost-card, 50 stolen-card, 66 fraud/unauthorized, 50 block/freeze, 54 secret-credential, and 60 negative examples. There are 322 exact unique texts because variants/duplicates are deliberate families.

#### `handoff_split.json`

The older seed-stable row-stratified 232/100 train/test split. It is retained to demonstrate that normalized phrase families leaked across the boundary.

#### `handoff_split_grouped.json`

The replacement 233/99 train/test split. Case, diacritics, punctuation, and whitespace are normalized into phrase-family keys, and each family stays wholly on one side. It includes per-intent row/family counts and the source SHA-256.

#### `handoff_probe.json`

The production classifier artifact. It records model, method, dimensions, split, threshold, train indices, labels, and a zlib-compressed Base64 block containing the 233 normalized float32 embeddings.

Why it exists: serving needs NumPy matrix multiplication but does not need scikit-learn or retraining. The decoded vectors, labels, training indices, and phrase-bank hash match the grouped split.

## 12. Every notebook

#### `boa_scraper_v2 (2).ipynb`

The 62-cell Colab research notebook for crawling, diagnostics, rate-table parsing, accordion extraction, FAQ parsing, PDF triage, regulation chunking/status tagging, embedding experiments, and file transfer. Its notebook metadata records a T4 GPU and a large amount of widget state from prior downloads/encoding.

Why it exists: it is the provenance and experimentation record for the corpus pipeline.

Gotchas: it is not a clean linear production pipeline; cell order/state matters; some cells supersede others; rate correction is not integrated at extraction; full and incremental embedding variants coexist; local corpus paths are Colab-specific; and the final `colab-ssh` example should not be reused with its literal sample password.

#### `boa_embed.ipynb`

The focused 11-cell GPU notebook for a fresh full re-embed after correcting rate labels. It gates on `maturitet`, reads both chunk files, checks ID uniqueness, encodes, verifies dimensions/norms, writes Parquet, and downloads it.

Why it exists: it avoids stale notebook state and the confusing incremental merge path.

Gotchas: its expected row-count prose is stale, it assumes files are already uploaded to `/content/boa_corpus`, and the repository copy contains no executed outputs proving a run.

## 13. Every project document and hidden brief

#### `EXPLAINED.md`

This file is the newcomer-oriented map of the current repository. It reconciles the implementation, local artifacts, historical handoffs, briefs, and saved benchmark evidence in one place. Unlike the older documents, it distinguishes the working text service from the still-unimplemented voice and telephony layers.

#### `HANDOFF.md`

An earlier project-state handoff from before the final rate correction and current service architecture. It documents the two-environment VPS/Colab workflow, the maturity-label defect, correction order, early retrieval results, and the planned tool-calling/voice design.

Gotcha: many counts and “remaining” tasks are historical. Use it as development history, not current operating truth.

#### `HANDOFF_updated.md`

A longer updated handoff after corrected embedding and initial text service work. It contains data validation/load steps, eval generation, retrieval checks, then-current tool-calling API examples, query rewriting, known gaps, and constraints.

Gotcha: it still describes model-selected retrieval tools and a `/chat` path that phase 1 later removed. Its 4,168-row database count is current; several architectural instructions are not.

#### `PROJECT_SUMMARY.md`

The concise product summary: Albanian bank/contact-center goal, corpus and retrieval, structured `/turn` outcomes, trust controls, session limitations, verification, and future voice/deployment work.

Gotcha: it says “voice assistant” as the product goal; current code is the text foundation. Its 2,306-page statement does not match the 2,257 pages stored in current `pdf_text.jsonl`.

#### `.hermes_codex_brief.md`

The phase-1 latency work order. It specified always-retrieve generation, conditional rewrite, pooling, warmup, embedding reuse, context-size measurement, behavioral preservation, and acceptance output.

Why it exists: project/task provenance. It is not runtime configuration.

#### `.hermes_codex_brief_phase2.md`

The phase-2 work order plus resume context from an interrupted run. It asked for larger rewrite testing, direct provider characterization, prompt caching research, context/prompt/model comparisons, first-sentence latency, and a decision report.

#### `.hermes_codex_brief_phase3.md`

The phase-3 work order. It challenged the earlier caching claim, required N=100 cache-on/off and full-path runs, measured generation speed, benchmarked Gemini end to end, evaluated quality and Albanian, and required restoration of the DeepSeek default.

#### `.hermes_codex_explain_brief.md`

The untracked instruction that requested this `EXPLAINED.md`. It is task provenance and was already present before this document was created.

#### `.gitignore`

Ignores PostgreSQL state, Parquet embeddings, every `.jsonl`, `.env`, bytecode, the notebooks, updated summary/handoff files, and historical `.orig` files.

Why it exists: keeps copyrighted/large corpus data, secrets, generated state, and local history out of normal Git commits.

Gotcha: many important files discussed here are intentionally invisible in ordinary `git status` and absent from a fresh clone. `*.jsonl` also broadly ignores future JSONL files unless force-added.

#### `.env.example`

Contains only a masked `DEEPSEEK_API_KEY` assignment. It identifies the required variable but does not explain that the value is an OpenRouter key.

#### `.env`

The ignored local secret file with the same variable. Its value must remain private. It is required before importing `rag.py` or `api.py`.

## 14. Database and generated directories

#### `db/docker-compose.yml`

Defines one `pgvector/pgvector:pg16` service named `boabot-postgres`, database/user/password `boa`, loopback host port 5433 mapped to container port 5432, a persistent `./pgdata` volume, and `restart: unless-stopped`.

Why it exists: it supplies PostgreSQL 16 with pgvector locally without exposing the database publicly.

Gotchas: credentials are development-only, relative volume placement depends on the Compose file/project behavior, and the application has no migration or readiness retry layer.

#### `db/pgdata/`

Generated PostgreSQL storage owned by the database container. It is ignored and not authored source. Its contents should never be documented file-by-file, edited manually, or committed.

#### `.venv/`

The local Python virtual environment, currently about 5 GB. It contains third-party packages, executables, metadata, and its own ignore-all rule. These thousands of files are generated dependencies, not BoABot components. Recreate the environment from declared dependencies once the project gains a proper lockfile; do not treat the installed tree as source.

#### `__pycache__/`

Generated Python bytecode for the modules. It includes both Python 3.11 and 3.12 cache names from previous runs. It is ignored, disposable, and not a source-of-truth version of the code.

#### `.git/`

Git's internal object database and metadata. It is repository infrastructure rather than a project component. The commit history shows initial work, intent-routing/group-split checkpoints, latency phases 1–3, and their reports.

## 15. Every latency-evidence artifact

The `latency_evidence/` directory is an audit trail. Text files are console transcripts or summaries; JSON files preserve raw per-request measurements and full answers. Measurements describe a particular provider, time, model, prompt, and corpus state. They are evidence, not permanent service guarantees.

### Phase 1 and baseline evidence

- `latency_evidence/FINAL_REPORT.md` — phase-1 narrative, before/after results, per-step attribution, rewrite and eval conclusions, context size, and known limitations.
- `latency_evidence/boabot_baseline_bench.txt` — 10 empty-history `/turn` observations and summary from the phase-1 comparison baseline.
- `latency_evidence/boabot_baseline_history_bench.txt` — the matching 10 measured turns with a primed session.
- `latency_evidence/boabot_baseline_eval.txt` — preserved retrieval-eval table, misses, trap refusals, and prefix breakdown.
- `latency_evidence/boabot_baseline_eval_calls.txt` — preserved 16/16 call-policy pass line.
- `latency_evidence/boabot_cold_no_warmup.txt` — one first request with lifespan warmup disabled.
- `latency_evidence/boabot_cold_after_warmup.txt` — one first request after startup warmup.
- `latency_evidence/boabot_component_metrics.txt` — 100-run embedding, handoff-matrix, new-versus-pooled database timing, and k=3/k=5 context character/token sizes.
- `latency_evidence/boabot_model_stage_metrics.txt` — small measurements of the removed tool-decision and unconditional-rewrite model stages.
- `latency_evidence/boabot_k3_bench.txt` — 10-turn full-path experiment with three retrieved chunks.
- `latency_evidence/boabot_rewrite_quality.txt` — the original eight explicit and seven elliptical follow-up comparisons, including rewritten queries and result IDs.

### Phase 2 evidence

- `latency_evidence/FINAL_REPORT_PHASE2.md` — phase-2 decision report covering 40-turn rewrite quality, provider distribution, caching, k sweep, prompt trim, model comparison, and first-sentence voice budget.
- `latency_evidence/phase2_acceptance_bench_provider.txt` — DeepSeek provider-only acceptance transcript: 100 fixed-prompt calls followed by its varying-input correlation probe.
- `latency_evidence/phase2_acceptance_bench_turn.txt` — 10 empty-history full `/turn` observations with first-sentence timing.
- `latency_evidence/phase2_acceptance_bench_turn_history.txt` — interrupted acceptance transcript containing only two of the requested 10 history observations.
- `latency_evidence/phase2_bench_history_N10.txt` — the later completed 10-history-turn rerun that closes the previous evidence gap.
- `latency_evidence/phase2_acceptance_eval.txt` — retrieval acceptance output, including the HF Hub warning and the same miss/trap analysis.
- `latency_evidence/phase2_acceptance_eval_calls.txt` — call-policy acceptance output with model-load warning and 16/16 pass.
- `latency_evidence/phase2_cache_before.txt` — provider prompt-cache experiment using the older combined layout.
- `latency_evidence/phase2_cache_after.txt` — matching split-prefix/cache-friendly layout experiment.
- `latency_evidence/phase2_k3.txt` — 50 provider calls with three evidence chunks and reasoning disabled.
- `latency_evidence/phase2_k5.txt` — corresponding five-chunk run.
- `latency_evidence/phase2_k8.txt` — corresponding eight-chunk run.
- `latency_evidence/phase2_model_google_gemini-3.1-flash-lite.txt` — 30-call provider benchmark for Gemini Flash Lite.
- `latency_evidence/phase2_model_mistralai_mistral-small-2603.txt` — 30-call provider benchmark for Mistral Small.
- `latency_evidence/phase2_model_openai_gpt-4.1-mini.txt` — 30-call provider benchmark for GPT-4.1 Mini.
- `latency_evidence/phase2_model_quality.json` — full 33-answer results for Gemini and Mistral: 13 FAQ, 10 rate, and 10 regulation questions per model, plus numeric/citation aggregates.
- `latency_evidence/phase2_prompt_quality.json` — full current-versus-trimmed prompt outputs for 15 supported and five unsupported questions. It records the citation and refusal regression that prevented shipping the trim.

### Phase 3 evidence

- `latency_evidence/FINAL_REPORT_PHASE3.md` — definitive phase-3 narrative plus embedded complete acceptance transcripts. It corrects the phase-2 caching evidence claim and compares DeepSeek/Gemini end to end.
- `latency_evidence/phase3_analysis.txt` — console tables produced by `phase3_analyze.py` from raw JSON.
- `latency_evidence/phase3_analysis.json` — machine-readable recomputation of all four full-path distributions, provider cache comparison, generation decomposition, fast/slow splits, and the 300 ms TTS assumption.
- `latency_evidence/phase3_deepseek_empty_N100.txt` — human-readable 100-turn DeepSeek empty-history run.
- `latency_evidence/phase3_deepseek_empty_N100.json` — raw timings, session IDs, questions, complete answers, first-sentence text, and token proxies for that run.
- `latency_evidence/phase3_deepseek_history_N100.txt` — human-readable 100-turn DeepSeek primed-history run.
- `latency_evidence/phase3_deepseek_history_N100.json` — raw observations and answers for that run.
- `latency_evidence/phase3_gemini_empty_N100.txt` — human-readable Gemini benchmark-override empty-history run.
- `latency_evidence/phase3_gemini_empty_N100.json` — raw Gemini empty-history observations; sample 37 records the partial-response/provider anomaly discussed in the report.
- `latency_evidence/phase3_gemini_history_N100.txt` — human-readable Gemini primed-history run.
- `latency_evidence/phase3_gemini_history_N100.json` — raw observations and answers for it.
- `latency_evidence/phase3_provider_cache_on_N100.txt` — 100 varied DeepSeek provider calls using split messages and a sticky session, with summary/histogram.
- `latency_evidence/phase3_provider_cache_on_N100.json` — raw provider usage, cache tokens, costs, generation IDs, outputs, and timings for cache-friendly structure.
- `latency_evidence/phase3_provider_cache_off_N100.txt` — 100 varied DeepSeek provider calls using combined prompt/no sticky session.
- `latency_evidence/phase3_provider_cache_off_N100.json` — raw comparison observations. “Off” is structural shorthand; provider-native caching still produced 41 cache hits.
- `latency_evidence/phase3_eval.txt` — final preserved retrieval-eval acceptance output.
- `latency_evidence/phase3_eval_calls.txt` — final preserved 16/16 call-policy output.
- `latency_evidence/phase3_quality_deepseek.txt` — printed 20-answer DeepSeek grounding/citation review and five direct refusal cases. Its automatic matcher counted 4/5 refusals, while the report's manual reading judged the fifth wording to be a refusal.
- `latency_evidence/phase3_quality_deepseek.json` — full DeepSeek answers, hit IDs, normalized numbers, unsupported-number lists, citation flags, usage, and summary.
- `latency_evidence/phase3_quality_gemini.txt` — printed Gemini 20-answer checks, direct refusals, and three end-to-end unsupported-policy cases.
- `latency_evidence/phase3_quality_gemini.json` — full Gemini quality evidence, including 20/20 numeric grounding, 20/20 citations, 5/5 direct refusals, and 3/3 routed refusal cases under the script's tests.

## 16. Important limitations and next work

### The product is not voice-complete

The repository has a voice-friendly text contract and voice-oriented latency measurements, but it still needs ASR/audio input, TTS/audio output, interruption or barge-in handling, streaming audio transport, telephony call control, and a real agent queue. The “first audio” figures are calculations, not observed calls.

### Handoff is advisory

`handoff: true` tells an upstream system what should happen. Nothing here places the caller in a queue, transfers a call, sends context to an agent, or confirms that a human accepted it.

### Deployment is local-development grade

There is no authentication, HTTPS proxy, durable/shared sessions, audit log, monitoring, rate limiting, deployment manifest for the API, secret manager, or multi-instance coordination. Bind to `127.0.0.1`; do not expose the current FastAPI service directly to the internet.

### Data freshness and coverage matter

- Business-deposit rates are absent and deliberately refused.
- Rate tables carry no effective/as-of date.
- Six scanned PDFs were excluded without OCR.
- Crawled HTML pages are mostly not chunked or embedded.
- The `ligjet` section yielded titles rather than consolidated law text; the handoff suggests qbz.gov.al as a better source.
- Albanian diacritics vary between source tables and users.
- There is no Albanian PostgreSQL stemmer; the project uses dense semantic search rather than Postgres full-text hybrid search.
- Status classification and legal currency need expert review.

### Evaluation is useful but incomplete

Generated questions are close to the chunks that generated them. The handwritten set is only 40 rows. The handoff classifier phrase bank is synthetic/curated. Numeric grounding cannot detect every wrong answer. A production evaluation should add real anonymized caller wording, ASR errors, dialect and no-diacritic forms, semantic answer correctness, citation entailment, multi-turn behavior, interruptions, and end-to-end human-transfer acceptance.

### Provider results can change

OpenRouter routing, provider load, model versions, prices, cache behavior, and network conditions are external. The saved phase results are excellent audit evidence for the dates and conditions recorded, not promises about future latency. Current production still hard-codes DeepSeek; the Gemini result is a recommendation and benchmark, not a shipped provider switch.

## 17. The shortest accurate mental model

BoABot is a guarded text service for an eventual Albanian banking voice assistant:

1. a caller's transcribed question reaches `/turn`;
2. deterministic policy either clarifies, repeats, refuses, or signals a human handoff;
3. a semantic handoff classifier catches sensitive call intents;
4. an elliptical follow-up may be rewritten for search;
5. one bge-m3 vector search finds five current Bank of Albania chunks;
6. trust gates reject weak or wrong-family evidence;
7. a remotely hosted LLM receives the vetted evidence and writes Albanian text;
8. FastAPI streams text and finishes with sources and an outcome;
9. extensive saved evaluations and benchmarks show what was measured, including where the design still fails.

That foundation is real and substantially implemented. The telephone/audio layer and operational contact-center integration are still future work.
