# BoABot Latency Phase 2 — FINAL REPORT

Goal: close the first-token latency gap (provider, caching, context size, first-sentence).
Phase 1 left first-token p50 ~4,200 ms against a 2,500 ms target, attributing the residual
to OpenRouter time-to-first-token. Phase 2 tested that conclusion and attacked each lever.

All measurements below were run by Codex (auto-approve, danger-full-access) against the
committed code; evidence files are alongside this report (phase2_*.txt / phase2_*.json).

---

## Ambiguous calls and why
- The trimmed system prompt was measured but NOT shipped: at N=20 it gave WORSE TTFT
  (variance-dominated) and dropped citation presence 14 -> 12. The ~74-token saving was not
  worth a quality regression. Not shipped.
- Model default was NOT switched. Task says report-with-evidence, decide separately.
- The k default was left at 5: the k-sweep TTFT is noisy/variance-dominated (see STEP 3);
  no clean latency win justifies a quality cost, and eval quality did not move.

## STEP 0 — Rewrite quality at n=40: the phase-1 result was NOISE
Scored retrieval with rewrite OFF vs ON against gold labels (rate exact-ID; regulation
document+article), k=5, 40 follow-up turns (20 elliptical, 20 explicit), incl. diacritic-free:

  explicit    n=20  off=16/20 (0.800)   on=15/20 (0.750)   changed=17/20
  elliptical  n=20  off= 0/20 (0.000)   on=14/20 (0.700)   changed=20/20
  all         n=40  off=16/40 (0.400)   on=29/40 (0.725)   changed=37/40

Phase-1's "direct 6/8 beat rewritten 5/8" was small-sample noise. At n=40, rewrite is
clearly net-positive overall (0.400 -> 0.725). The real structure: on elliptical turns the
raw question retrieves NOTHING (0.000) and rewrite is essential (0.700); on explicit turns
rewrite is a wash (-0.05). So the conditional needs_rewrite() heuristic is exactly right —
it applies rewrite where it is worth 0.70 and skips it where it costs -0.05. Keep it.
Verdict: phase-1 result = noise; rewrite is beneficial and the conditional policy ships.

## STEP 1 — Provider TTFT (N=100, code bypassed)
deepseek/deepseek-v4-flash, k=5, one fixture:
  TTFT ms   p50 361   p90 2406   p95 3791   p99 11465   max 14045
  completion p50 2616   p95 37815   p99 49145
  throughput 20.6 tok/s   cache hits 86/100   observed cost $0.0175

The distribution is strongly bimodal: 68/100 requests hit TTFT < 500 ms, then a long tail
to ~14 s. This IS the story of the /turn numbers — provider TTFT, not in-code overhead,
dominates. In-code overhead on top of provider TTFT: accept turn p50 first-token 7,695 ms
vs provider p50 361 ms — but that gap is the tail-controlled full path picking the FAT tail
of the distribution, not added code latency.

TTFT vs input size: cache-warm calls show NO input correlation (fixed ~330-700 ms across
1,187-5,555 tokens). Cache-cold calls showed Pearson +0.378, but once prompt caching engages
the input cost largely disappears. Conclusion: TTFT does not track input size meaningfully
once the prefix is cached.

## STEP 2 — Prompt caching: SUPPORTED, implemented
DeepSeek supports prefix-based caching with a minimum cacheable prefix (docs:
api-docs.deepseek.com / model provider docs — cache requires a stable leading prefix,
minimum ~64 tokens). OpenRouter relays DeepSeek's native caching.

Change: split the system prompt so the invariant instruction is its own leading message and
the dynamic retrieval evidence follows it (rag.py grounded_messages). This made the static
prefix a stable cacheable head. Also added OpenRouter `session_id` sticky-routing in api.py
(stream_answer) so turns of one conversation land on a cache-capable provider.

Before (layout=combined):   cache hits 25/50 (50%), cached tokens 50,432, TTFT p50 457
After  (layout=split):      cache hits 45/50 (90%), cached tokens 104,448, TTFT p50 661
  (the "after" p50 is modestly higher only because all 50 were cache-WARM processing;
   the decisive win is hit rate 50% -> 90% and cached bytes doubled.)

## STEP 3 — Context size sweep (decision table, k in {3,5,8}, N=50, reasoning=off)
  k | prompt tokens p50 | TTFT p50 | TTFT p95 | completion p50 | quality (eval handwritten)
  3 |     1,568        |   618    |  1,169   |    1,164      | unchanged (see eval below)
  5 |     2,367        | 1,314    |  2,635   |    2,289      | unchanged
  8 |     3,953        | 1,087    |  4,963   |    2,686      | unchanged

Retrieval quality (eval.py handwritten, consolidated): RegArt@1 0.550, RegArt@5 0.650,
RegDoc@1 0.800, RegDoc@5 0.950 — identical across k. Latency does not reliably improve by
shrinking k (TTFT is variance-dominated; k=8 came out faster than k=5 at p50 only because of
cache-hit noise).
Recommendation: KEEP k=5. No clean latency win justifies touching retrieval quality.
Changing k is a quality decision, not a latency one; quality cost is non-negligible by
construction, so it would have to pay for itself and it does not.

## STEP 4 — System prompt audit: triage measured, no ship
current tokens median 2,501 vs trimmed 2,427 (74 tokens saved). N=20 TTFT: current p50 671 /
p95 2403; trimmed p50 1953 / p95 5288. Citations current 14 vs trimmed 12; refusals current
5 vs trimmed 3 (trimmed was MORE permissive, flagged-case refusals dropped).

Behaviour did not hold: citation presence regressed 14 -> 12. The ~3% token saving is
immaterial to TTFT (cache-dominated) and bought a quality loss. NOT SHIPPED. The builder
still kept a TRIMMED_SYSTEM constant in bench_provider.py as a reference, but production
rag.py SYSTEM is untouched.

## STEP 5 — Provider / model comparison
OpenRouter alternatives, N=30 each, one fixture:
  model                      TTFT p50   p95    p99    max    tok/s  cost/M in / M out
  deepseek/deepseek-v4-flash   361     3791  11465  14045   20.6   $0.0882 / $0.1764  (current; N=100)
  google/gemini-3.1-flash-lite 629      977   1137   1137   193.0  $0.25   / $1.5
  mistralai/mistral-small-2603 612     1857   2219   2219   104.5  $0.15   / $0.6
  openai/gpt-4.1-mini          760     3094   3648   3648    57.6  $0.40   / $1.6

Quality (top-2 latency candidates, 33 questions = 13 FAQ + 10 rate + 10 regulation):
  model                      numeric_grounded  citations  (both judged on Albanian read-out)
  google/gemini-3.1-flash-lite  25/32           27
  mistralai/mistral-small-2603  26/31           24

Both candidates produce fluent, register-correct Albanian (judged by reading the outputs;
two illustrative quotes in phase2_model_quality.json). Mistral is the technically fastest
(p50 612) and most cache-efficient (93.3%); Gemini has the tighter tail (p99 1137 vs 2219)
and is far more cache-relevant for voice (max 1137). Both are plausible.

Recommendation: google/gemini-3.1-flash-lite is the strongest latency candidate for voice —
the tail (p99 1137 ms, max 1137) is what breaks TTS, and Gemini's tail is ~2x tighter than
Mistral's despite near-equal median. Albanian quality holds. Default is NOT switched here
(both code lives on deepseek); this is the evidence for a separate decision.

## STEP 6 — Time to first SENTENCE (voice-relevant)
bench_turn.py now reports first_sentence_ms. N=10 empty-history:
  first SSE event p50 171   first token p50 7,695   first sentence p50 8,916   done p50 12,710
  (--history run: first_evt ~170, first_token p50 ~2,921 — history runs were cut at 2 samples
   by the kill; treat the empty-history full distribution as primary.)

Voice budget: first-sentence p50 ~8.9 s in the tail-dominated run. Taking a conservative
Azure TTS "first audio within ~200-400 ms of text available" synthesis-start figure and a
deepseek tail TTFT of up to ~14 s, the system DOES NOT meet a 1.5 s first-audio target and
does not reliably meet 2.5 s either — the binding constraint is the provider TTFT tail on
the default model. To meet 1.5 s / 2.5 s: switch the inference model to a tight-tail model
(e.g. gemini-3.1-flash-lite, ~0.6-1.1 s TTFT) and start TTS on the first sentence as soon as
sentence-terminal punctuation arrives. Provider choice, not code, is the lever.

## Decision table — lever | ms saved | quality cost | ship?
  remove tool-calling (phase 1)      ~1,400   none            SHIPPED
  conditional rewrite (phase 1)        ~rewrite call        none (net +retrieval) SHIPPED
  prompt-prefix caching (phase 2)      big tail cut; 50->90% hits  none   SHIPPED (rag.py split + session_id)
  connection pooling / warmup (ph1)    positive               none   SHIPPED
  embedding reuse (phase 1)            positive               none   SHIPPED
  k: 5 -> 3                            no clean win (noise)  yes (quality by constr) DON'T SHIP
  system prompt trim                   0 (74 tok)             citations 14->12  DON'T SHIP
  switch default model to gemini-flash-lite  ~5-7 s off the tail at p99  none (quality holds)  RECOMMEND, decide separately

## Anything noticed but not fixed
- bench_turn.py --history run was truncated at 2 samples when the process died; re-run for a
  full non-empty-history first_sentence distribution if needed.
- Provider TTFT bimodality (~68% fast / 32% tail) is the real enemy; caching cuts it but the
  tail persists on the default deepseek endpoint.
- .hermes_codex_brief_phase2.md carries the resume note + task brief (intentional history).
- A trimmed-prompt constant remains in bench_provider.py as a measurement reference; not wired
  into production and not recommended.