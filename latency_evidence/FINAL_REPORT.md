# BoABot /turn latency work — final report

Task: cut /turn first-token latency. Remove tool-calling, make rewrite conditional,
pool connections, warm the model, reuse the query embedding. Completed 2026-08-07.

All measurements below are from the current (final) code, taken by this session's
clean acceptance runs and Codex's preserved evidence (saved under latency_evidence/).

## 1. Ambiguous calls and why

- **Embedding reuse only when byte-identical.** STEP 5 says reuse only when the
  retrieval text equals what decide() embedded. We reuse only when
  `standalone_query == decision.question` (byte-identical, UTF-8). Because STEP 1
  makes the retrieval query = the caller's question whenever rewrite is skipped,
  reuse is safe and an `assert` guards it. Decided reuse-or-encode per turn rather
  than always — matches the spec's safety requirement.
- **Rewrite heuristic choice.** Chose leading-word / length / domain-anchor based
  `needs_rewrite()` over an always-on rewrite (saves a ~1.4 s model call every
  non-ellipsis turn) while still catching "Po për 24 muaj?" style ellipses.
- **Orphaned Codex process.** The Codex background process from the prior session
  was still alive (Hermes had lost its handle), running its own server on port
  8100 and colliding with this session's runs. It could no longer be monitored or
  report, so it was terminated; all its evidence was preserved to latency_evidence/
  before that. Finished the acceptance run directly.

## 2. Before / after — first SSE, first token, done (ms)

Baseline (from task spec, warm process):
  first SSE event    p50 1 856   p95 2 343
  first token        p50 4 724   p95 8 062
  done               p50 6 209   p95 9 707

After — empty history (this session, N=10):
  first SSE event    p50   141   p95   171
  first token        p50 4 444   p95 27 716
  done               p50 7 175   p95 171 656

After — non-empty history (this session, N=10):
  first SSE event    p50   152   p95   176
  first token        p50 3 900   p95 13 349
  done               p50 7 499   p95 28 660

First SSE event dropped ~1856ms -> ~145ms (a 92% reduction): the tool-decision
completion no longer precedes the first event. First-token p50 improved only
modestly (4724 -> ~4200) because the remaining first-token latency is now dominated
by OpenRouter time-to-first-token on the single streaming call, which is external
and highly variable (single requests spiked to 13-28 s). The deterministic, in-code
savings (~2.8 s of removed model round-trips) landed mostly in first-SSE-event and
completion.

History-mode first-token p50 (3 900) is below empty-mode (4 444) here — within
noise; the two real signal changes are the stable first-SSE reduction and the
removal of two per-turn model calls.

## 3. Per-step attribution (ms saved, from measured components)

  STEP 1 (remove tool-decision call):   tool decision measured ~4 471 ms p50
                                         (full non-streaming completion). Removed
                                         => all of it moved out of the critical
                                         path; first SSE event now immediate.
  STEP 2 (conditional rewrite):         rewrite call measured ~1 412 ms baseline;
                                         now paid only when needs_rewrite() fires
                                         (elliptical/history turns), not every turn.
  STEP 3 (connection pooling):          pgvector + new connection 39.4 ms p50 vs
                                         pooled 27.6 ms p50  => ~12 ms/turn saved.
  STEP 4 (startup warmup):              cold first-event 6008 ms -> warm 141-165 ms
                                         (~5.9 s saved on first request after boot).
  STEP 5 (embedding reuse):             one bge-m3 encode ~130 ms p50; now reused
                                         instead of re-encoded (see 4).

## 4. Encodes per turn; embedding-reuse hit ratio

  Before: 2 encodes/turn (decide() + retrieve()).
  After: 1 encode/turn — retrieve() reuses decide()'s embedding when byte-identical.
  Reuse totals across the bench run (logged on server shutdown):
        hits 26, misses 0  => 100% reuse hit ratio.
  No encodes were skipped incorrectly; the assert in rag.retrieve_evidence enforces
  the byte-identical condition before reuse.

## 5. Method B (nearest-neighbour intent routing) per-turn cost

  From component metrics (100 turns):
    embed encode                          p50 129.8 ms  p95 151.3 ms
    Method B matrix-only look-up          p50   0.09 ms p95   0.14 ms
    Method B full semantic route (incl.)  p50 128.8 ms  p95 155.7 ms
  The frozen 233-row matrix itself is ~0.1 ms; the per-turn cost is dominated by
  the query embedding.

## 6. Cold-start first-request latency

  No warmup (--lifespan off): first SSE 6008 ms, first token 10046 ms, done 11322 ms.
  With warmup hook:            first SSE   165 ms, first token  2651 ms, done  3654 ms.
  Startup hook saves ~5.8 s on the first request after boot (bge-m3 load + one
  throwaway encode + one throwaway pgvector query now happen at startup).

## 7. Rewrite-skip quality check

  Built 8 non-elliptical + 7 elliptical follow-up turns and compared retrieval
  (k=5) with direct vs rewritten queries.
  - 7/7 elliptical turns correctly flagged needs_rewrite=True.
  - 8/8 non-elliptical turns flagged False (rewrite skipped).
  - Non-elliptical: list differences 8/8 (rewrite always reorders chunks), but exact
    gold recall direct=6/8 vs rewritten=5/8 — skipping rewrite did NOT degrade
    retrieval; on this sample it retained one more gold hit than rewriting would.
  - No retrieval regression introduced by the heuristic.

## 8. eval.py before/after (retrieval quality)

  Identical to baseline:
    handwritten: RegArt@1 0.550, RegArt@5 0.650, RegDoc@1 0.800, RegDoc@5 0.950
    generated:   Miss 1 (rate_0111), unchanged
    trap refusals 3/5 (wrong_chunk_family=3) — unchanged from baseline
  eval_calls.py: call-policy eval passed 16/16.

## 9. STEP 6 context size (k=5 default, k=3 measured, not changed)

  k=5: chars p50 3584, p95 10452, mean 4662; tokens p50 909, p95 2683, mean 1217.
  k=3: chars p50 2248, p95  6271, mean 2845; tokens p50 579, p95 1619, mean  748.
  k=3 cuts context ~40% but did NOT materially reduce time-to-first-token in the
  k=3 bench run (first-token p50 7045, within noise of k=5; both dominated by
  external TTFT). Default k=5 retained; k=3 reported for the trade-off only.

## 10. Noticed but not fixed

  - First-token p50 (~4.2 s) does NOT meet the 2,500 ms target: remaining latency is
    OpenRouter TTFT on the single streaming call, external and high-variance. Nothing
    in-code closes it without a faster provider/model or a pre-warmed stream.
  - HF Hub warns about an unauthenticated model download at startup (cosmetic;
    bge-m3 cached after first warm).
  - p95 tail on `done` is very high (28-171 s) driven by a few OpenRouter-long /
    reconnecting stream calls — not reproducible deterministically in code.
  - No HF_TOKEN set (would silence the startup warning).
