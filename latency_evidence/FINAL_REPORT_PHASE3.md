# BoABot Latency Phase 3 — FINAL REPORT

Completed 2026-08-07. All latency percentiles use nearest rank and N=100. Raw JSON,
full console transcripts, quality answers, and the reproducible analysis script are
committed beside this report.

## 1. Ambiguous calls and why

- `/turn` does not expose OpenRouter's native completion-token usage or generation ID.
  Output speed therefore uses one model-neutral lexical proxy: Unicode words, numbers,
  and punctuation divided by first-token-to-done wall time. This is comparable between
  the two `/turn` runs, but it is not a provider-token billing rate. OpenRouter documents
  that native counts normally arrive in the upstream usage object; production intentionally
  does not forward that object ([OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)).
- A punctuation-free completed answer is one complete TTS utterance, so its `done` time is
  the first-sentence boundary. This affected one DeepSeek history answer. Its saved answer
  and timestamp were reconciled deterministically; no request was rerun or dropped.
- "Cache off" means exactly the requested structural rollback: combined system+evidence
  message and no `session_id`. DeepSeek/OpenRouter's automatic native caching cannot be
  disabled by that layout, so the off run still reported 41 incidental hits. The relevant
  comparison is shipped cache-friendly structure versus combined/no-session structure.
- The provider experiments were sequential matched runs, not a randomized crossover.
  They use the same ten prompts, k=5 context, current system prompt, model, and N=100;
  time-varying provider conditions remain a possible confound.
- "Fast/slow" is reported with an explicit operational threshold, not an inferred causal
  label: `/turn` fast is <3 s (the empty-history histogram valley), provider fast is <5 s.
  Phase 1's <500 ms provider split is also quoted but is not directly comparable to full
  `/turn` latency or today's varied-prompt provider workload.
- Gemini was selected only at process import time (`rag.MODEL` patched in memory before
  importing `api`); no model or caching source configuration was edited.
- Azure TTS was not available on this host. Voice calculations assume 300 ms from sentence
  availability to first audio byte with a preconnected, reused streaming synthesizer. This
  is an engineering assumption, not an Azure SLA. Microsoft defines client first-byte
  latency as synthesis start to first client audio chunk, recommends streaming, and advises
  preconnection/reuse ([Microsoft latency guidance](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency)).

## 2. Prompt-caching verdict

| condition | TTFT p50 | p90 | p95 | p99 | max | cache hits | fast/slow (<5 s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| shipped split + sticky session | 4,197 | 8,253 | 9,349 | 12,271 | 15,443 | 95/100 | 65/35 |
| combined + no session | 4,512 | 11,249 | 14,529 | 20,720 | 27,331 | 41/100 | 59/41 |
| **shipped savings** | **315** | **2,996** | **5,180** | **8,449** | **11,888** | — | — |

Prompt caching/sticky routing measurably improved TTFT in this matched observation: only
315 ms at p50, but 5.18 s at p95 and 8.45 s at p99. It did **not** collapse the slow mode:
35% of cache-on provider calls were still >=5 s, while the full DeepSeek `/turn` split was
34 fast/66 slow for empty history and 32/68 with history at the stated 3-second threshold.
The phase-2 conclusion turns out directionally correct at the tail, but its evidence sentence
was wrong: **“the decisive win is hit rate 50% -> 90% and cached bytes doubled.”** Hit rate
and cached bytes establish cache use/cost, not latency. The corrected evidence is the N=100
TTFT delta above. The decision-table phrase **“big tail cut; 50->90% hits”** was therefore
unsubstantiated in phase 2 even though phase 3 now measures a real tail cut.

## 3. Generation speed and first-sentence decomposition

The additive decompositions use the two observations straddling the median first-sentence
rank, so their averaged TTFT + generation components equal the reported p50 exactly.

| model | mode | lexical tok/s p50 | p10 | mean sentence tokens | first-sentence p50 decomposition |
|---|---|---:|---:|---:|---:|
| DeepSeek | empty | 38.2 | 6.3 | 37.4 | 5,814 TTFT + 739 generation = 6,554 ms |
| DeepSeek | history | 48.6 | 4.9 | 42.4 | 6,054 + 621 = 6,675 ms |
| Gemini | empty | 171.4 | 86.2 | 37.7 | 933 + 129 = 1,062 ms |
| Gemini | history | 159.1 | 78.4 | 36.8 | 777 + 272 = 1,049 ms |

For reference, the independent component medians (which do not algebraically add because
they may come from different requests) are DeepSeek empty TTFT 4,912 ms + sentence
generation 855 ms, versus Gemini empty 844 + 170 ms. Gemini attacks both budget components:
roughly 4.5x higher median lexical delivery throughput and much lower TTFT.

## 4. Full `/turn` latency table (milliseconds)

| model | mode | metric | p50 | p90 | p95 | p99 | max |
|---|---|---|---:|---:|---:|---:|---:|
| DeepSeek | empty | first SSE | 164 | 188 | 195 | 228 | 278 |
| DeepSeek | empty | first token | 4,912 | 13,995 | 16,109 | 22,664 | 28,212 |
| DeepSeek | empty | first sentence | 6,554 | 17,022 | 20,369 | 25,803 | 29,460 |
| DeepSeek | empty | done | 8,848 | 44,802 | 56,454 | 94,374 | 215,591 |
| DeepSeek | history | first SSE | 164 | 185 | 191 | 197 | 199 |
| DeepSeek | history | first token | 4,728 | 13,370 | 18,125 | 30,927 | 42,336 |
| DeepSeek | history | first sentence | 6,675 | 17,071 | 20,613 | 33,439 | 43,179 |
| DeepSeek | history | done | 11,727 | 41,024 | 65,624 | 112,587 | 178,715 |
| Gemini | empty | first SSE | 162 | 187 | 195 | 208 | 236 |
| Gemini | empty | first token | 844 | 2,706 | 5,986 | 10,608 | 12,024 |
| Gemini | empty | first sentence | 1,062 | 3,066 | 6,992 | 10,611 | 12,054 |
| Gemini | empty | done | 1,982 | 6,441 | 8,664 | 12,945 | 23,838 |
| Gemini | history | first SSE | 163 | 181 | 188 | 208 | 223 |
| Gemini | history | first token | 789 | 1,243 | 1,562 | 2,949 | 4,761 |
| Gemini | history | first sentence | 1,049 | 1,580 | 2,001 | 4,925 | 5,702 |
| Gemini | history | done | 1,506 | 3,435 | 3,726 | 5,702 | 6,613 |

First-token fast/slow (<3 s / >=3 s): DeepSeek empty 34/66, DeepSeek history 32/68,
Gemini empty 90/10, Gemini history 99/1. The full histograms are in the embedded outputs.

## 5. Gemini quality on this corpus

| check | Gemini | current DeepSeek baseline | verdict |
|---|---:|---:|---|
| retrieval (`eval.py`) | model-independent; unchanged | unchanged | handwritten RegArt@1/5 0.550/0.650; RegDoc@1/5 0.800/0.950 |
| call policy | 16/16 | 16/16 | unchanged |
| strict normalized numeric grounding, 20 rate questions | 20/20 | 20/20 | tie; **Gemini failures: none** |
| names retrieved document/article | 20/20 | 20/20 | tie |
| direct empty-evidence refusals | 5/5 | 5/5 by reading | both refused parametric answers |
| `/turn` refusal cases from `eval_calls.jsonl` | 3/3 cases | deterministic baseline | Gemini server returned `unsupported` for every expected refusal |

Normalization removes apostrophe/comma grouping, percent signs, and insignificant decimal
zeros, so `1'000.00`, `1,000`, `0.00`, and `0.00%` compare canonically. Every numeric token
in each answer had to occur in serialized retrieved evidence. The original DeepSeek scorer
printed 4/5 because its phrase matcher omitted “nuk kam”; reading the output confirms the
fifth answer said **“Nuk kam informacion në korpus për receta gatimi”** and did not provide
a recipe. The scorer now recognizes that refusal form.

Grounding is not full semantic correctness: Gemini's small-business answer lists four
unlabeled values, and both models' Procredit answer labels a service/maintenance value in
response to an administration question. Those outputs passed the requested numeric test
because all numbers were retrieved; this was noticed, not fixed.

## 6. Albanian register and fluency — judged by reading

I read all 20 paired outputs. These are three same-question pairs (verbatim):

| question | Gemini | DeepSeek |
|---|---|---|
| Nese me duhet nje pin i ri per karten e kreditit, sa me kushton te Banka Kombëtare Tregtare? | “Për marrjen e një PIN-i të ri për kartën e kreditit te Banka Kombëtare Tregtare, komisioni është 500.00 lekë.<br><br>Burimi: Dokumenti "Komisionet për biznese", seksioni: "Komisionet për biznese — Karte krediti — Dhenia e nje Pin-i te ri".” | “Sipas materialit të marrë nga korpusi, në dokumentin **"Komisionet për biznese"** (Burimi: https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/), për kategorinë **"Karte krediti — Dhenia e nje Pin-i te ri"**, tarifa për **Bankën Kombëtare Tregtare** është **500.00** (lekë).” |
| Per kredi per shtepi, sa eshte komisioni per ristrukturim minimal ne Banka Tirana? | “Komisioni minimal për ristrukturimin e kredisë për shtëpi në Bankën Tirana është 4,200.00.<br><br>Burimi: "Komisionet për individë", tabela: "Komisionet për individë — Kredi per shtepi — Komision për ristrukturimin e kredive MIN".” | “Sipas materialeve të marra nga korpusi, për kreditë për shtëpi, komisioni minimal për ristrukturimin e kredive në Banka Tirana është **4,200.00** lekë.<br><br>Burimi: Dokumenti "Komisionet për individë" nga Banka e Shqipërisë, seksioni "Kredi per shtepi — Komision për ristrukturimin e kredive MIN".” |
| Sa eshte komisioni i terheqjes se parave nga terminalet jashte shtetit me karte krediti tek Banka Raiffeisen? | “Për tërheqjen e parave (cash) nga terminalet jashtë shtetit me kartë krediti tek Banka Raiffeisen, komisionet sipas dokumentit "Komisionet për biznese" janë:<br><br>*   **Komisioni MIN:** 1'000.00 (Burimi: "Komisionet për biznese", ID: rate_0100)<br>*   **Komisioni MAX:** 1'000.00 (Burimi: "Komisionet për biznese", ID: rate_0102)” | “Sipas korpusit, për Banka Raiffeisen, komisioni për tërheqje cash nga terminalet jashtë shtetit me kartë krediti është:<br><br>- **MIN**: 1'000.00 (një mijë lekë)<br>- **MAX**: 1'000.00 (një mijë lekë)<br><br>Burimi: Dokumenti "Komisionet për biznese" nga Banka e Shqipërisë, seksionet "Karte krediti — Terheqje Cash nga terminalet jashte shtetit MIN" dhe "... MAX".<br><br>Shënim: Në korpus nuk gjendet asnjë përqindje (%) për Banka Raiffeisen për këtë kategori specifike (lista e përqindjeve në seksionin përkatës nuk e përfshin atë).” |

Both models are fluent enough for professional Albanian; Albanian quality rules out neither.
Gemini is usually more direct and natural, leading with the answer (“Komisioni ... është”)
instead of the repeated DeepSeek preamble “Sipas/Bazuar në materialet e marra nga korpusi.”
Specific DeepSeek awkwardness includes the agreement error **“komisioni i administrimit të
kredisë konsumatore të pasiguruara”** (singular *kredisë* with plural *të pasiguruara*), the
unnatural preposition **“materialit të marrë në korpus”** (natural: *nga korpusi*), and the
English leakage **“Bank of Albania.”** Both sometimes reproduce table-label noun stacks.
Gemini also retains the English corpus term **“cash”** where natural prose would say *para
në dorë*, and uses labels such as **“Komisioni MIN/MAX”**; these are minor, source-induced
awkward constructions rather than broad fluency failures.

## 7. Voice budget

Cells show `p50 verdict / p95 verdict`. First audio = measured first sentence + the stated
300 ms Azure first-byte assumption.

| model | mode | first-audio p50 | first-audio p95 | 1.5 s target | 2.5 s target |
|---|---|---:|---:|---|---|
| DeepSeek | empty | 6,854 ms | 20,669 ms | FAIL / FAIL | FAIL / FAIL |
| DeepSeek | history | 6,975 ms | 20,913 ms | FAIL / FAIL | FAIL / FAIL |
| Gemini | empty | 1,362 ms | 7,292 ms | PASS / FAIL | PASS / FAIL |
| Gemini | history | 1,349 ms | 2,301 ms | PASS / FAIL | PASS / PASS |

Neither model meets a strict 1.5-second p95 first-audio SLO. Gemini meets both targets at
the median, and meets 2.5 seconds at p95 for established/history turns, but its empty-session
p95 fails both because of sparse OpenRouter outliers. Therefore the phase-2 claim that
Gemini's provider TTFT alone closed the voice budget was too strong: end-to-end first-sentence
and routing tails still matter.

## 8. Recommendation

- **Voice:** use `google/gemini-3.1-flash-lite` as the candidate. It is dramatically faster
  at median and p95, generates much faster, and matches DeepSeek on the requested corpus
  checks. Do not promise a 1.5-second p95; for a 2.5-second p95 across first turns, pin/qualify
  the provider path or obtain a stronger routing SLO first.
- **Text:** keep the shipped `deepseek/deepseek-v4-flash` default when latency is not binding.
  Current catalog prices measured in this run are $0.14/M input and $0.28/M output versus
  Gemini's $0.25/M and $1.50/M. Gemini is also defensible for latency-sensitive text, but a
  default switch is a separate cost/product decision and was not made here.
- **Albanian:** rules out neither model; Gemini was slightly more concise/natural by reading.

## 9. Anything noticed but not fixed

- One Gemini empty-history request (sample 37) received an OpenRouter SSE event with empty
  `choices`; production appended the safe human-agent handoff after partial text. It remains
  in the N=100 full-path measurement.
- DeepSeek has uncapped generation tails: measured `/turn` completion max was 215.6 s, and
  several unmeasured history primers also ran for minutes.
- Native cache telemetry is provider-dependent: cache-on still had 5 misses, cache-off had
  41 hits, and prompt-token usage sometimes varied for the same fixture.
- The lexical token proxy is delivery throughput, not hidden provider generation speed;
  SSE batching can produce very high per-request values.
- Numeric grounding does not detect wrong labels, omissions, or selection of the wrong
  retrieved rate; add a semantic answer-correctness scorer in a separate quality task.
- The HF Hub warning about unauthenticated downloads remains cosmetic; bge-m3 was cached.

## 10. Restoration and verification

The shipped model remains `deepseek/deepseek-v4-flash`. The cacheable leading system message
in `rag.grounded_messages` and sticky `session_id` in `api.stream_answer` are unchanged.
No server is left on port 8100. Final git/config verification is recorded immediately before
the final commit and summarized to the user.

## 11. Acceptance — full command outputs

The commands below used `.venv/bin/python` (the repository interpreter); command labels use
`python3` to match the acceptance wording. Every latency percentile comes from N=100.

### `python3 bench_turn.py -n 100 (DeepSeek, empty history)`

```text
/turn benchmark: N=100, mode=empty history, url=http://127.0.0.1:8100
 1 first_event=    172 ms  first_token=   1000 ms  first_sentence=  11778 ms  done=  16181 ms  tokens= 95  tok/s=   6.3
 2 first_event=    156 ms  first_token=   1558 ms  first_sentence=   6953 ms  done=  10919 ms  tokens= 47  tok/s=   5.0
 3 first_event=    156 ms  first_token=  10029 ms  first_sentence=  10048 ms  done=  10127 ms  tokens= 70  tok/s= 712.5
 4 first_event=    135 ms  first_token=   3454 ms  first_sentence=   3979 ms  done=   4546 ms  tokens= 72  tok/s=  65.9
 5 first_event=    173 ms  first_token=   3300 ms  first_sentence=   4140 ms  done=   4530 ms  tokens= 76  tok/s=  61.8
 6 first_event=    151 ms  first_token=   1553 ms  first_sentence=   1553 ms  done=   1555 ms  tokens= 21  tok/s=12170.6
 7 first_event=    138 ms  first_token=   6344 ms  first_sentence=   7321 ms  done=  16221 ms  tokens=639  tok/s=  64.7
 8 first_event=    152 ms  first_token=  14348 ms  first_sentence=  14760 ms  done=  19472 ms  tokens=404  tok/s=  78.9
 9 first_event=    123 ms  first_token=    900 ms  first_sentence=   2841 ms  done=   7116 ms  tokens=202  tok/s=  32.5
10 first_event=    166 ms  first_token=   1509 ms  first_sentence=  20369 ms  done= 215591 ms  tokens=580  tok/s=   2.7
11 first_event=    177 ms  first_token=    753 ms  first_sentence=   1655 ms  done=   2601 ms  tokens= 68  tok/s=  36.8
12 first_event=    165 ms  first_token=   7075 ms  first_sentence=   7929 ms  done=   9474 ms  tokens= 63  tok/s=  26.3
13 first_event=    160 ms  first_token=   5389 ms  first_sentence=   5793 ms  done=   6427 ms  tokens= 80  tok/s=  77.1
14 first_event=    152 ms  first_token=   3554 ms  first_sentence=   4369 ms  done=   6049 ms  tokens=139  tok/s=  55.7
15 first_event=    178 ms  first_token=   6391 ms  first_sentence=   7329 ms  done=   7963 ms  tokens= 75  tok/s=  47.7
16 first_event=    188 ms  first_token=   5340 ms  first_sentence=   5437 ms  done=   6503 ms  tokens= 53  tok/s=  45.6
17 first_event=    164 ms  first_token=  15973 ms  first_sentence=  17022 ms  done=  29031 ms  tokens=488  tok/s=  37.4
18 first_event=    156 ms  first_token=   8618 ms  first_sentence=  10362 ms  done=  14345 ms  tokens=271  tok/s=  47.3
19 first_event=    135 ms  first_token=   6428 ms  first_sentence=   6899 ms  done=   9009 ms  tokens=168  tok/s=  65.1
20 first_event=    180 ms  first_token=    851 ms  first_sentence=   3559 ms  done=  16296 ms  tokens=411  tok/s=  26.6
21 first_event=    167 ms  first_token=   4288 ms  first_sentence=   4924 ms  done=   5767 ms  tokens= 64  tok/s=  43.3
22 first_event=    180 ms  first_token=   3643 ms  first_sentence=   4355 ms  done=   4402 ms  tokens= 25  tok/s=  32.9
23 first_event=    175 ms  first_token=    825 ms  first_sentence=   4718 ms  done=   5528 ms  tokens= 63  tok/s=  13.4
24 first_event=    159 ms  first_token=    577 ms  first_sentence=   1208 ms  done=   1923 ms  tokens= 82  tok/s=  60.9
25 first_event=    170 ms  first_token=   5155 ms  first_sentence=  11526 ms  done=  27068 ms  tokens= 81  tok/s=   3.7
26 first_event=    142 ms  first_token=  17702 ms  first_sentence=  18053 ms  done=  20785 ms  tokens= 55  tok/s=  17.8
27 first_event=    156 ms  first_token=  22664 ms  first_sentence=  23596 ms  done=  44802 ms  tokens=874  tok/s=  39.5
28 first_event=    150 ms  first_token=   7994 ms  first_sentence=   8705 ms  done=  17159 ms  tokens=585  tok/s=  63.8
29 first_event=    129 ms  first_token=  14440 ms  first_sentence=  15351 ms  done=  20394 ms  tokens=133  tok/s=  22.3
30 first_event=    193 ms  first_token=  11526 ms  first_sentence=  11932 ms  done=  17114 ms  tokens=337  tok/s=  60.3
31 first_event=    180 ms  first_token=   6468 ms  first_sentence=   7744 ms  done=   7768 ms  tokens= 94  tok/s=  72.3
32 first_event=    183 ms  first_token=    573 ms  first_sentence=   1335 ms  done=   1978 ms  tokens= 53  tok/s=  37.7
33 first_event=    159 ms  first_token=   5889 ms  first_sentence=   6454 ms  done=   8686 ms  tokens=137  tok/s=  49.0
34 first_event=    168 ms  first_token=   5002 ms  first_sentence=   6141 ms  done=   7453 ms  tokens= 74  tok/s=  30.2
35 first_event=    169 ms  first_token=   2277 ms  first_sentence=   3217 ms  done=   6608 ms  tokens= 57  tok/s=  13.2
36 first_event=    149 ms  first_token=   1939 ms  first_sentence=   2538 ms  done=   6797 ms  tokens= 45  tok/s=   9.3
37 first_event=    139 ms  first_token=   9581 ms  first_sentence=  10056 ms  done=  20029 ms  tokens=558  tok/s=  53.4
38 first_event=    154 ms  first_token=   6088 ms  first_sentence=   6614 ms  done=  12491 ms  tokens=424  tok/s=  66.2
39 first_event=    137 ms  first_token=  10706 ms  first_sentence=  10765 ms  done=  16302 ms  tokens=206  tok/s=  36.8
40 first_event=    162 ms  first_token=    693 ms  first_sentence=   2103 ms  done=  39782 ms  tokens=338  tok/s=   8.6
41 first_event=    160 ms  first_token=   9657 ms  first_sentence=  10987 ms  done=  12767 ms  tokens= 87  tok/s=  28.0
42 first_event=    204 ms  first_token=   4063 ms  first_sentence=   4626 ms  done=   5375 ms  tokens= 45  tok/s=  34.3
43 first_event=    171 ms  first_token=    949 ms  first_sentence=   5349 ms  done=   7756 ms  tokens= 61  tok/s=   9.0
44 first_event=    153 ms  first_token=   3720 ms  first_sentence=   4752 ms  done=   4807 ms  tokens= 47  tok/s=  43.2
45 first_event=    196 ms  first_token=   3580 ms  first_sentence=   4713 ms  done=   5148 ms  tokens= 78  tok/s=  49.7
46 first_event=    153 ms  first_token=   1795 ms  first_sentence=   5062 ms  done=   6823 ms  tokens= 43  tok/s=   8.6
47 first_event=    136 ms  first_token=   1527 ms  first_sentence=   7377 ms  done=  48691 ms  tokens=386  tok/s=   8.2
48 first_event=    150 ms  first_token=   1528 ms  first_sentence=  11073 ms  done=  56540 ms  tokens=334  tok/s=   6.1
49 first_event=    133 ms  first_token=   1461 ms  first_sentence=   2865 ms  done=  16587 ms  tokens=473  tok/s=  31.3
50 first_event=    178 ms  first_token=  15124 ms  first_sentence=  15544 ms  done=  28639 ms  tokens=703  tok/s=  52.0
51 first_event=    166 ms  first_token=   5217 ms  first_sentence=   6120 ms  done=   7067 ms  tokens=112  tok/s=  60.5
52 first_event=    162 ms  first_token=   2274 ms  first_sentence=   2765 ms  done=   2867 ms  tokens= 46  tok/s=  77.6
53 first_event=    159 ms  first_token=   3889 ms  first_sentence=   4231 ms  done=   5264 ms  tokens= 44  tok/s=  32.0
54 first_event=    157 ms  first_token=  13995 ms  first_sentence=  15026 ms  done=  17305 ms  tokens=121  tok/s=  36.6
55 first_event=    159 ms  first_token=   5091 ms  first_sentence=   6335 ms  done=   7634 ms  tokens= 80  tok/s=  31.5
56 first_event=    143 ms  first_token=    675 ms  first_sentence=    784 ms  done=   2042 ms  tokens= 53  tok/s=  38.8
57 first_event=    149 ms  first_token=    896 ms  first_sentence=   1603 ms  done=  14566 ms  tokens=426  tok/s=  31.2
58 first_event=    170 ms  first_token=    931 ms  first_sentence=   4411 ms  done=  26239 ms  tokens=142  tok/s=   5.6
59 first_event=    146 ms  first_token=  28212 ms  first_sentence=  29460 ms  done=  45951 ms  tokens=326  tok/s=  18.4
60 first_event=    202 ms  first_token=   1685 ms  first_sentence=   7595 ms  done=  47157 ms  tokens=272  tok/s=   6.0
61 first_event=    166 ms  first_token=    713 ms  first_sentence=   1301 ms  done=   2773 ms  tokens= 99  tok/s=  48.1
62 first_event=    193 ms  first_token=    859 ms  first_sentence=   1610 ms  done=   2250 ms  tokens= 49  tok/s=  35.2
63 first_event=    187 ms  first_token=   3333 ms  first_sentence=   3651 ms  done=   4441 ms  tokens= 58  tok/s=  52.3
64 first_event=    161 ms  first_token=   6925 ms  first_sentence=   7739 ms  done=   8528 ms  tokens= 74  tok/s=  46.2
65 first_event=    185 ms  first_token=   6895 ms  first_sentence=   7952 ms  done=   8633 ms  tokens= 68  tok/s=  39.1
66 first_event=    145 ms  first_token=    622 ms  first_sentence=    694 ms  done=   8109 ms  tokens= 44  tok/s=   5.9
67 first_event=    180 ms  first_token=   8161 ms  first_sentence=   8273 ms  done=  18101 ms  tokens=572  tok/s=  57.5
68 first_event=    185 ms  first_token=  21848 ms  first_sentence=  25803 ms  done=  37407 ms  tokens=462  tok/s=  29.7
69 first_event=    140 ms  first_token=   9858 ms  first_sentence=  10708 ms  done=  20224 ms  tokens=376  tok/s=  36.3
70 first_event=    278 ms  first_token=   1582 ms  first_sentence=  23198 ms  done=  53254 ms  tokens=294  tok/s=   5.7
71 first_event=    182 ms  first_token=   6202 ms  first_sentence=   6911 ms  done=   7413 ms  tokens= 68  tok/s=  56.1
72 first_event=    180 ms  first_token=   1974 ms  first_sentence=   2271 ms  done=   2564 ms  tokens= 47  tok/s=  79.7
73 first_event=    172 ms  first_token=   4334 ms  first_sentence=   5585 ms  done=   6254 ms  tokens= 93  tok/s=  48.4
74 first_event=    178 ms  first_token=  11416 ms  first_sentence=  15112 ms  done=  15122 ms  tokens= 76  tok/s=  20.5
75 first_event=    228 ms  first_token=   7400 ms  first_sentence=   7910 ms  done=   8221 ms  tokens= 87  tok/s= 105.9
76 first_event=    165 ms  first_token=   5158 ms  first_sentence=   5462 ms  done=   6127 ms  tokens= 43  tok/s=  44.4
77 first_event=    160 ms  first_token=  13952 ms  first_sentence=  17117 ms  done=  94374 ms  tokens=577  tok/s=   7.2
78 first_event=    191 ms  first_token=   5540 ms  first_sentence=   6493 ms  done=   9192 ms  tokens= 99  tok/s=  27.1
79 first_event=    160 ms  first_token=   1865 ms  first_sentence=   8396 ms  done=  69033 ms  tokens=353  tok/s=   5.3
80 first_event=    159 ms  first_token=   8192 ms  first_sentence=   9926 ms  done=  17604 ms  tokens=539  tok/s=  57.3
81 first_event=    191 ms  first_token=   4060 ms  first_sentence=   4558 ms  done=   5152 ms  tokens= 97  tok/s=  88.8
82 first_event=    195 ms  first_token=    833 ms  first_sentence=   1528 ms  done=   2234 ms  tokens= 37  tok/s=  26.4
83 first_event=    185 ms  first_token=  16109 ms  first_sentence=  17477 ms  done=  22116 ms  tokens=102  tok/s=  17.0
84 first_event=    169 ms  first_token=  20141 ms  first_sentence=  22704 ms  done=  24583 ms  tokens=100  tok/s=  22.5
85 first_event=    177 ms  first_token=    580 ms  first_sentence=   1436 ms  done=   2412 ms  tokens= 56  tok/s=  30.6
86 first_event=    152 ms  first_token=   5877 ms  first_sentence=   5990 ms  done=   7335 ms  tokens= 82  tok/s=  56.3
87 first_event=    159 ms  first_token=   7778 ms  first_sentence=   8543 ms  done=  19099 ms  tokens=597  tok/s=  52.7
88 first_event=    169 ms  first_token=   4788 ms  first_sentence=   5933 ms  done=   8503 ms  tokens=221  tok/s=  59.5
89 first_event=    153 ms  first_token=  11085 ms  first_sentence=  11372 ms  done=  14045 ms  tokens=171  tok/s=  57.8
90 first_event=    159 ms  first_token=   1400 ms  first_sentence=   3122 ms  done=  29219 ms  tokens=525  tok/s=  18.9
91 first_event=    174 ms  first_token=   4422 ms  first_sentence=   4832 ms  done=   6279 ms  tokens=134  tok/s=  72.1
92 first_event=    181 ms  first_token=   4821 ms  first_sentence=   5021 ms  done=   5978 ms  tokens= 75  tok/s=  64.8
93 first_event=    183 ms  first_token=  13829 ms  first_sentence=  16554 ms  done=  19433 ms  tokens= 98  tok/s=  17.5
94 first_event=    166 ms  first_token=   4251 ms  first_sentence=   5253 ms  done=   5306 ms  tokens= 61  tok/s=  57.8
95 first_event=    169 ms  first_token=   7177 ms  first_sentence=   7895 ms  done=   8579 ms  tokens=100  tok/s=  71.3
96 first_event=    146 ms  first_token=   6407 ms  first_sentence=   6452 ms  done=   7747 ms  tokens=135  tok/s= 100.7
97 first_event=    155 ms  first_token=   8121 ms  first_sentence=   8354 ms  done=  20202 ms  tokens=753  tok/s=  62.3
98 first_event=    140 ms  first_token=   1350 ms  first_sentence=  17373 ms  done=  64906 ms  tokens=487  tok/s=   7.7
99 first_event=    143 ms  first_token=   1337 ms  first_sentence=   8384 ms  done=  56454 ms  tokens=370  tok/s=   6.7
100 first_event=    170 ms  first_token=  11721 ms  first_sentence=  11733 ms  done=  20189 ms  tokens=459  tok/s=  54.2
summary (ms)
  first SSE event  p50     164  p90     188  p95     195  p99     228  max     278  n 100/100
  first token      p50    4912  p90   13995  p95   16109  p99   22664  max   28212  n 100/100
  first sentence   p50    6554  p90   17022  p95   20369  p99   25803  max   29460  n 100/100
  done             p50    8848  p90   44802  p95   56454  p99   94374  max  215591  n 100/100
first-token histogram
      0-500   ms     0
    500-1000  ms    16 ###############
   1000-1500  ms     5 #####
   1500-2000  ms    11 ###########
   2000-3000  ms     2 ##
   3000-5000  ms    16 ###############
   5000-10000 ms    31 ##############################
  10000-20000 ms    15 ###############
  >=20000    ms     4 ####
generation (lexical-token proxy; words, numbers, and punctuation)
  output tokens/s  p50 38.2  p10 6.3  n 100
  first-sentence tokens mean 35.1  n 100/100
  first-sentence p50 decomposition  TTFT 4912 ms + generation 855 ms
raw JSON written to latency_evidence/phase3_deepseek_empty_N100.json
```

### `python3 bench_turn.py -n 100 --history (DeepSeek)`

```text
/turn benchmark: N=100, mode=non-empty history, url=http://127.0.0.1:8100
 1 first_event=    180 ms  first_token=   2059 ms  first_sentence=   2214 ms  done=   3299 ms  tokens=123  tok/s=  99.2
 2 first_event=    177 ms  first_token=   2098 ms  first_sentence=   6238 ms  done=   9977 ms  tokens= 39  tok/s=   4.9
 3 first_event=    163 ms  first_token=  11157 ms  first_sentence=  11504 ms  done=  12872 ms  tokens=140  tok/s=  81.6
 4 first_event=    163 ms  first_token=   1957 ms  first_sentence=  17298 ms  done=  22754 ms  tokens= 89  tok/s=   4.3
 5 first_event=    167 ms  first_token=   3972 ms  first_sentence=   4328 ms  done=   4598 ms  tokens= 40  tok/s=  63.9
 6 first_event=    176 ms  first_token=   2197 ms  first_sentence=   2477 ms  done=   2871 ms  tokens= 41  tok/s=  60.9
 7 first_event=    140 ms  first_token=  15781 ms  first_sentence=  17071 ms  done=  42744 ms  tokens=798  tok/s=  29.6
 8 first_event=    159 ms  first_token=   1846 ms  first_sentence=   9086 ms  done=  77526 ms  tokens=325  tok/s=   4.3
 9 first_event=    126 ms  first_token=    909 ms  first_sentence=   5881 ms  done=  65624 ms  tokens=303  tok/s=   4.7
10 first_event=    170 ms  first_token=   1410 ms  first_sentence=   2845 ms  done=  80705 ms  tokens=453  tok/s=   5.7
11 first_event=    158 ms  first_token=   3794 ms  first_sentence=   4506 ms  done=   5270 ms  tokens=101  tok/s=  68.4
12 first_event=    184 ms  first_token=   1832 ms  first_sentence=  14135 ms  done=  16411 ms  tokens= 45  tok/s=   3.1
13 first_event=    166 ms  first_token=   1312 ms  first_sentence=  18731 ms  done=  48347 ms  tokens=103  tok/s=   2.2
14 first_event=    156 ms  first_token=  13370 ms  first_sentence=  15138 ms  done=  22957 ms  tokens=104  tok/s=  10.8
15 first_event=    193 ms  first_token=   3690 ms  first_sentence=   4036 ms  done=   4427 ms  tokens= 41  tok/s=  55.6
16 first_event=    153 ms  first_token=   5607 ms  first_sentence=   6263 ms  done=   8034 ms  tokens= 89  tok/s=  36.7
17 first_event=    145 ms  first_token=   7441 ms  first_sentence=   7746 ms  done=  19184 ms  tokens=728  tok/s=  62.0
18 first_event=    155 ms  first_token=  11423 ms  first_sentence=  17314 ms  done= 105181 ms  tokens=296  tok/s=   3.2
19 first_event=    143 ms  first_token=   7815 ms  first_sentence=  14132 ms  done=  41024 ms  tokens=257  tok/s=   7.7
20 first_event=    199 ms  first_token=   5780 ms  first_sentence=   6317 ms  done=  10676 ms  tokens=299  tok/s=  61.1
21 first_event=    176 ms  first_token=   1990 ms  first_sentence=   6087 ms  done=  13100 ms  tokens= 82  tok/s=   7.4
22 first_event=    183 ms  first_token=   2673 ms  first_sentence=   4054 ms  done=   4143 ms  tokens= 15  tok/s=  10.2
23 first_event=    161 ms  first_token=   3947 ms  first_sentence=   4486 ms  done=   6050 ms  tokens= 93  tok/s=  44.2
24 first_event=    171 ms  first_token=   3363 ms  first_sentence=    n/a     done=   5091 ms  tokens=116  tok/s=  67.1
25 first_event=    185 ms  first_token=   2899 ms  first_sentence=   3676 ms  done=   4489 ms  tokens=101  tok/s=  63.5
26 first_event=    154 ms  first_token=   9664 ms  first_sentence=  10639 ms  done=  12617 ms  tokens= 76  tok/s=  25.7
27 first_event=    154 ms  first_token=   2124 ms  first_sentence=  20663 ms  done= 178715 ms  tokens=681  tok/s=   3.9
28 first_event=    152 ms  first_token=  12517 ms  first_sentence=  13644 ms  done=  17807 ms  tokens=257  tok/s=  48.6
29 first_event=    161 ms  first_token=  42336 ms  first_sentence=  43179 ms  done=  47794 ms  tokens=323  tok/s=  59.2
30 first_event=    150 ms  first_token=   8177 ms  first_sentence=   8654 ms  done=  13841 ms  tokens=369  tok/s=  65.2
31 first_event=    174 ms  first_token=  11956 ms  first_sentence=  12301 ms  done=  12965 ms  tokens= 93  tok/s=  92.2
32 first_event=    189 ms  first_token=   4771 ms  first_sentence=   5311 ms  done=   5795 ms  tokens= 56  tok/s=  54.7
33 first_event=    191 ms  first_token=  13835 ms  first_sentence=  15073 ms  done=  16856 ms  tokens= 89  tok/s=  29.5
34 first_event=    183 ms  first_token=   1695 ms  first_sentence=   2061 ms  done=   2115 ms  tokens= 40  tok/s=  95.2
35 first_event=    174 ms  first_token=   3291 ms  first_sentence=   4057 ms  done=   4990 ms  tokens=112  tok/s=  65.9
36 first_event=    167 ms  first_token=   4529 ms  first_sentence=   4986 ms  done=   6360 ms  tokens=101  tok/s=  55.2
37 first_event=    145 ms  first_token=  10170 ms  first_sentence=  11069 ms  done=  26359 ms  tokens=847  tok/s=  52.3
38 first_event=    172 ms  first_token=   5544 ms  first_sentence=   5964 ms  done=   9947 ms  tokens=288  tok/s=  65.4
39 first_event=    149 ms  first_token=   1099 ms  first_sentence=  11423 ms  done= 112587 ms  tokens=360  tok/s=   3.2
40 first_event=    197 ms  first_token=   6327 ms  first_sentence=   7032 ms  done=  15202 ms  tokens=468  tok/s=  52.7
41 first_event=    179 ms  first_token=  18125 ms  first_sentence=  20298 ms  done=  22508 ms  tokens= 96  tok/s=  21.9
42 first_event=    171 ms  first_token=   3818 ms  first_sentence=   4154 ms  done=   4935 ms  tokens= 64  tok/s=  57.3
43 first_event=    174 ms  first_token=   4633 ms  first_sentence=   5351 ms  done=   5818 ms  tokens= 83  tok/s=  70.1
44 first_event=    166 ms  first_token=   2865 ms  first_sentence=   4156 ms  done=   4178 ms  tokens= 79  tok/s=  60.2
45 first_event=    159 ms  first_token=    548 ms  first_sentence=   1336 ms  done=   3095 ms  tokens= 86  tok/s=  33.8
46 first_event=    141 ms  first_token=   3091 ms  first_sentence=   3176 ms  done=   3663 ms  tokens= 31  tok/s=  54.2
47 first_event=    152 ms  first_token=  12428 ms  first_sentence=  12776 ms  done=  28075 ms  tokens=938  tok/s=  59.9
48 first_event=    166 ms  first_token=    931 ms  first_sentence=   1836 ms  done=   7467 ms  tokens=200  tok/s=  30.6
49 first_event=    130 ms  first_token=  10296 ms  first_sentence=  10993 ms  done=  16427 ms  tokens=335  tok/s=  54.6
50 first_event=    172 ms  first_token=   5703 ms  first_sentence=   6157 ms  done=  11935 ms  tokens=303  tok/s=  48.6
51 first_event=    173 ms  first_token=   3322 ms  first_sentence=   3780 ms  done=   4997 ms  tokens=115  tok/s=  68.7
52 first_event=    186 ms  first_token=   7410 ms  first_sentence=   7700 ms  done=   8040 ms  tokens= 51  tok/s=  81.0
53 first_event=    160 ms  first_token=   3773 ms  first_sentence=   4516 ms  done=   6389 ms  tokens=141  tok/s=  53.9
54 first_event=    131 ms  first_token=  19151 ms  first_sentence=  20613 ms  done=  24391 ms  tokens=131  tok/s=  25.0
55 first_event=    180 ms  first_token=   8454 ms  first_sentence=  12097 ms  done=  13798 ms  tokens=159  tok/s=  29.8
56 first_event=    141 ms  first_token=   1285 ms  first_sentence=   1959 ms  done=   2478 ms  tokens= 39  tok/s=  32.7
57 first_event=    169 ms  first_token=  30927 ms  first_sentence=  33439 ms  done=  60494 ms  tokens=748  tok/s=  25.3
58 first_event=    155 ms  first_token=  26650 ms  first_sentence=  27675 ms  done=  33208 ms  tokens=391  tok/s=  59.6
59 first_event=    126 ms  first_token=   1033 ms  first_sentence=   1693 ms  done=  14456 ms  tokens=520  tok/s=  38.7
60 first_event=    156 ms  first_token=   5586 ms  first_sentence=   6154 ms  done=  14558 ms  tokens=384  tok/s=  42.8
61 first_event=    168 ms  first_token=   3850 ms  first_sentence=   4789 ms  done=   5322 ms  tokens= 98  tok/s=  66.6
62 first_event=    195 ms  first_token=    560 ms  first_sentence=   1851 ms  done=   2036 ms  tokens= 30  tok/s=  20.3
63 first_event=    174 ms  first_token=   7222 ms  first_sentence=   7816 ms  done=   9721 ms  tokens=124  tok/s=  49.6
64 first_event=    164 ms  first_token=   4597 ms  first_sentence=   5035 ms  done=   6805 ms  tokens=115  tok/s=  52.1
65 first_event=    178 ms  first_token=   1643 ms  first_sentence=   9120 ms  done=  15390 ms  tokens= 79  tok/s=   5.7
66 first_event=    147 ms  first_token=   8865 ms  first_sentence=   9524 ms  done=  10262 ms  tokens= 43  tok/s=  30.8
67 first_event=    165 ms  first_token=   9662 ms  first_sentence=  10752 ms  done=  17658 ms  tokens=456  tok/s=  57.0
68 first_event=    164 ms  first_token=  12344 ms  first_sentence=  14696 ms  done=  21327 ms  tokens=241  tok/s=  26.8
69 first_event=    135 ms  first_token=   6553 ms  first_sentence=   7093 ms  done=  10362 ms  tokens=203  tok/s=  53.3
70 first_event=    170 ms  first_token=    951 ms  first_sentence=   1552 ms  done=  11518 ms  tokens=399  tok/s=  37.8
71 first_event=    165 ms  first_token=   1187 ms  first_sentence=   3685 ms  done=   6409 ms  tokens=118  tok/s=  22.6
72 first_event=    175 ms  first_token=   2696 ms  first_sentence=   3515 ms  done=   3559 ms  tokens= 44  tok/s=  51.0
73 first_event=    175 ms  first_token=    902 ms  first_sentence=   1064 ms  done=   4753 ms  tokens=112  tok/s=  29.1
74 first_event=    145 ms  first_token=   4772 ms  first_sentence=   6187 ms  done=   6487 ms  tokens= 88  tok/s=  51.3
75 first_event=    164 ms  first_token=  14342 ms  first_sentence=  16256 ms  done=  17489 ms  tokens=119  tok/s=  37.8
76 first_event=    190 ms  first_token=   9471 ms  first_sentence=  10197 ms  done=  12637 ms  tokens=117  tok/s=  37.0
77 first_event=    145 ms  first_token=  10739 ms  first_sentence=  11147 ms  done=  21533 ms  tokens=587  tok/s=  54.4
78 first_event=    154 ms  first_token=  10200 ms  first_sentence=  13054 ms  done=  18129 ms  tokens=236  tok/s=  29.8
79 first_event=    151 ms  first_token=   3558 ms  first_sentence=   4085 ms  done=   5749 ms  tokens=122  tok/s=  55.7
80 first_event=    168 ms  first_token=   9234 ms  first_sentence=   9273 ms  done=  15062 ms  tokens=399  tok/s=  68.5
81 first_event=    167 ms  first_token=   2354 ms  first_sentence=  12858 ms  done=  18915 ms  tokens= 91  tok/s=   5.5
82 first_event=    176 ms  first_token=  24175 ms  first_sentence=  25935 ms  done=  25943 ms  tokens= 43  tok/s=  24.3
83 first_event=    191 ms  first_token=   5410 ms  first_sentence=   6119 ms  done=   7690 ms  tokens=120  tok/s=  52.6
84 first_event=    142 ms  first_token=   9368 ms  first_sentence=  12425 ms  done=  14384 ms  tokens= 74  tok/s=  14.8
85 first_event=    160 ms  first_token=  10975 ms  first_sentence=  13435 ms  done=  14088 ms  tokens=106  tok/s=  34.0
86 first_event=    168 ms  first_token=   4339 ms  first_sentence=   4503 ms  done=   5578 ms  tokens= 78  tok/s=  63.0
87 first_event=    151 ms  first_token=   5535 ms  first_sentence=   6191 ms  done=  15630 ms  tokens=524  tok/s=  51.9
88 first_event=    147 ms  first_token=   6344 ms  first_sentence=   7326 ms  done=  10998 ms  tokens=276  tok/s=  59.3
89 first_event=    130 ms  first_token=    944 ms  first_sentence=   2078 ms  done=   7435 ms  tokens=214  tok/s=  33.0
90 first_event=    190 ms  first_token=   7288 ms  first_sentence=   7817 ms  done=  13603 ms  tokens=342  tok/s=  54.2
91 first_event=    153 ms  first_token=   7631 ms  first_sentence=  10795 ms  done=  10862 ms  tokens=113  tok/s=  35.0
92 first_event=    173 ms  first_token=   4685 ms  first_sentence=   5301 ms  done=   5338 ms  tokens= 46  tok/s=  70.4
93 first_event=    170 ms  first_token=   1344 ms  first_sentence=   2500 ms  done=   3354 ms  tokens= 78  tok/s=  38.8
94 first_event=    150 ms  first_token=   2083 ms  first_sentence=  14089 ms  done=  29803 ms  tokens= 92  tok/s=   3.3
95 first_event=    160 ms  first_token=   3082 ms  first_sentence=   3618 ms  done=   3823 ms  tokens= 40  tok/s=  54.0
96 first_event=    139 ms  first_token=   2517 ms  first_sentence=   2571 ms  done=   4661 ms  tokens= 78  tok/s=  36.4
97 first_event=    133 ms  first_token=   8570 ms  first_sentence=   9216 ms  done=  22928 ms  tokens=803  tok/s=  55.9
98 first_event=    155 ms  first_token=   6942 ms  first_sentence=   8346 ms  done=  10787 ms  tokens=240  tok/s=  62.4
99 first_event=    122 ms  first_token=  13514 ms  first_sentence=  14435 ms  done=  21008 ms  tokens=206  tok/s=  27.5
100 first_event=    164 ms  first_token=    785 ms  first_sentence=   1404 ms  done=  10705 ms  tokens=319  tok/s=  32.2
summary (ms)
  first SSE event  p50     164  p90     185  p95     191  p99     197  max     199  n 100/100
  first token      p50    4728  p90   13370  p95   18125  p99   30927  max   42336  n 100/100
  first sentence   p50    7032  p90   17298  p95   20663  p99   43179  max   43179  n 99/100
  done             p50   11727  p90   41024  p95   65624  p99  112587  max  178715  n 100/100
first-token histogram
      0-500   ms     0
    500-1000  ms     8 #########
   1000-1500  ms     7 ########
   1500-2000  ms     6 #######
   2000-3000  ms    11 #############
   3000-5000  ms    20 #######################
   5000-10000 ms    26 ##############################
  10000-20000 ms    18 #####################
  >=20000    ms     4 #####
generation (lexical-token proxy; words, numbers, and punctuation)
  output tokens/s  p50 48.6  p10 4.9  n 100
  first-sentence tokens mean 41.1  n 99/100
  first-sentence p50 decomposition  TTFT 4728 ms + generation 778 ms
raw JSON written to latency_evidence/phase3_deepseek_history_N100.json
```

### `python3 bench_provider.py -n 100 --vary --layout split --session-id ... --size-probe-requests 0 (cache on)`

```text
catalog price: $0.14/M input, $0.28/M output
provider TTFT benchmark (BoABot code bypassed)
  N=100, model=deepseek/deepseek-v4-flash, k=5, layout=split, prompt=current, reasoning=auto, 10 fixtures
    1/100 ttft=   2506 ms  done=   3795 ms  in=1722  out=340  reason=167  cached=   0
    2/100 ttft=   4114 ms  done=   4522 ms  in=1187  out=376  reason=269  cached= 256
    3/100 ttft=   4967 ms  done=   5880 ms  in=1201  out=538  reason=345  cached= 256
    4/100 ttft=   2400 ms  done=   3444 ms  in=1691  out=344  reason=219  cached= 256
    5/100 ttft=   5733 ms  done=   8645 ms  in=1258  out=1000  reason=599  cached= 256
    6/100 ttft=   2722 ms  done=   3663 ms  in=3012  out=315  reason=202  cached=   0
    7/100 ttft=   8313 ms  done=  22007 ms  in=5555  out=2836  reason=969  cached= 256
    8/100 ttft=   4631 ms  done=   6180 ms  in=3946  out=602  reason=470  cached=   0
    9/100 ttft=   2273 ms  done=   3319 ms  in=5321  out=260  reason=127  cached= 256
   10/100 ttft=   8036 ms  done=  15771 ms  in=4371  out=1984  reason=898  cached= 256
   11/100 ttft=   4565 ms  done=   6611 ms  in=1722  out=446  reason=299  cached=1024
   12/100 ttft=   1959 ms  done=   2621 ms  in=1187  out=216  reason=131  cached=1024
   13/100 ttft=   2478 ms  done=   3098 ms  in=1201  out=288  reason=169  cached=1024
   14/100 ttft=   4439 ms  done=   5494 ms  in=1691  out=495  reason=346  cached=1024
   15/100 ttft=   2560 ms  done=   3947 ms  in=1258  out=383  reason=195  cached=1024
   16/100 ttft=   2498 ms  done=   4000 ms  in=3012  out=393  reason=197  cached=2048
   17/100 ttft=   3327 ms  done=  13688 ms  in=5555  out=1670  reason=272  cached=4096
   18/100 ttft=   8818 ms  done=  19473 ms  in=3946  out=2410  reason=995  cached=3072
   19/100 ttft=   4472 ms  done=   6093 ms  in=5321  out=636  reason=418  cached=4096
   20/100 ttft=   3970 ms  done=  13485 ms  in=4371  out=1647  reason=382  cached=4096
   21/100 ttft=   5546 ms  done=   6707 ms  in=1722  out=747  reason=583  cached=1024
   22/100 ttft=   2540 ms  done=   3169 ms  in=1187  out=280  reason=197  cached=1024
   23/100 ttft=   5370 ms  done=   8396 ms  in=1201  out=994  reason=593  cached=1024
   24/100 ttft=   6202 ms  done=   7181 ms  in=1691  out=695  reason=567  cached=1024
   25/100 ttft=   2958 ms  done=   4134 ms  in=1258  out=385  reason=227  cached=1024
   26/100 ttft=   3565 ms  done=   5038 ms  in=3012  out=531  reason=342  cached=2048
   27/100 ttft=   9919 ms  done=  23804 ms  in=5555  out=3005  reason=1156  cached=4096
   28/100 ttft=   5800 ms  done=  13295 ms  in=3946  out=1538  reason=582  cached=3072
   29/100 ttft=   4574 ms  done=   7702 ms  in=5321  out=816  reason=395  cached=4096
   30/100 ttft=  15443 ms  done=  23872 ms  in=4371  out=2837  reason=1413  cached=4096
   31/100 ttft=   2864 ms  done=   3965 ms  in=1722  out=335  reason=234  cached=1024
   32/100 ttft=   5753 ms  done=   6743 ms  in=1187  out=504  reason=385  cached=1024
   33/100 ttft=   6591 ms  done=   8346 ms  in=1201  out=821  reason=510  cached=1024
   34/100 ttft=   3648 ms  done=   5914 ms  in=1691  out=494  reason=268  cached=1024
   35/100 ttft=   2373 ms  done=   3554 ms  in=1258  out=347  reason=192  cached=1024
   36/100 ttft=   1905 ms  done=   2731 ms  in=3012  out=299  reason=178  cached=2048
   37/100 ttft=   4541 ms  done=  12252 ms  in=5555  out=1562  reason=425  cached=4096
   38/100 ttft=   7286 ms  done=  14036 ms  in=3946  out=1626  reason=583  cached=3072
   39/100 ttft=   3411 ms  done=   5941 ms  in=5321  out=613  reason=344  cached=4096
   40/100 ttft=  12271 ms  done=  26017 ms  in=4371  out=3033  reason=1195  cached=4096
   41/100 ttft=   3319 ms  done=   4463 ms  in=1722  out=458  reason=310  cached=1024
   42/100 ttft=   1977 ms  done=   2772 ms  in=1187  out=235  reason=138  cached=1024
   43/100 ttft=   1992 ms  done=   2696 ms  in=1201  out=259  reason=162  cached=1024
   44/100 ttft=   2758 ms  done=   3748 ms  in=1691  out=308  reason=178  cached=1024
   45/100 ttft=   6926 ms  done=  11027 ms  in=1258  out=520  reason=357  cached=1024
   46/100 ttft=   2818 ms  done=   4052 ms  in=3012  out=370  reason=257  cached=2048
   47/100 ttft=   6919 ms  done=  16510 ms  in=5555  out=1971  reason=671  cached=4096
   48/100 ttft=   9349 ms  done=  14758 ms  in=3946  out=1591  reason=870  cached=3072
   49/100 ttft=   5346 ms  done=   6571 ms  in=5321  out=737  reason=549  cached=4096
   50/100 ttft=   6420 ms  done=  18408 ms  in=4371  out=2273  reason=692  cached=4096
   51/100 ttft=   3435 ms  done=   4755 ms  in=1722  out=501  reason=322  cached=1024
   52/100 ttft=   3366 ms  done=   4233 ms  in=1187  out=359  reason=250  cached=1024
   53/100 ttft=   2151 ms  done=   3044 ms  in=1201  out=265  reason=150  cached=1024
   54/100 ttft=   4006 ms  done=   5020 ms  in=1691  out=531  reason=394  cached=1024
   55/100 ttft=   2901 ms  done=   3640 ms  in=1258  out=303  reason=205  cached=1024
   56/100 ttft=   2946 ms  done=   3720 ms  in=3012  out=441  reason=324  cached=2048
   57/100 ttft=   5049 ms  done=  15529 ms  in=5555  out=1866  reason=474  cached=4096
   58/100 ttft=   6112 ms  done=  16914 ms  in=3946  out=1509  reason=424  cached=3072
   59/100 ttft=   7964 ms  done=   9567 ms  in=5321  out=1070  reason=836  cached=4096
   60/100 ttft=   4310 ms  done=  15660 ms  in=4371  out=2026  reason=471  cached=4096
   61/100 ttft=   3659 ms  done=   4852 ms  in=1722  out=507  reason=263  cached=1024
   62/100 ttft=   2301 ms  done=   2618 ms  in=1187  out=218  reason=140  cached=1024
   63/100 ttft=   2813 ms  done=   3719 ms  in=1201  out=400  reason=267  cached=1024
   64/100 ttft=   2995 ms  done=   4861 ms  in=1691  out=437  reason=297  cached=1024
   65/100 ttft=   6362 ms  done=   7388 ms  in=1258  out=859  reason=728  cached=1024
   66/100 ttft=   3060 ms  done=   4048 ms  in=3012  out=394  reason=224  cached=2048
   67/100 ttft=   9353 ms  done=  17030 ms  in=5555  out=2163  reason=1097  cached=4096
   68/100 ttft=  10207 ms  done=  16269 ms  in=3946  out=2012  reason=1202  cached=3072
   69/100 ttft=   8537 ms  done=  10721 ms  in=5321  out=1259  reason=964  cached=4096
   70/100 ttft=   6943 ms  done=  14696 ms  in=4371  out=2022  reason=821  cached=4096
   71/100 ttft=   4377 ms  done=   5571 ms  in=1722  out=586  reason=426  cached=1024
   72/100 ttft=   2508 ms  done=   3315 ms  in=1187  out=292  reason=181  cached=1024
   73/100 ttft=   2496 ms  done=   3740 ms  in=1201  out=334  reason=172  cached=1024
   74/100 ttft=   4279 ms  done=   5552 ms  in=1691  out=518  reason=344  cached=1024
   75/100 ttft=   3413 ms  done=   4191 ms  in=1258  out=398  reason=307  cached=1024
   76/100 ttft=   2346 ms  done=   2757 ms  in=3012  out=217  reason=159  cached=2048
   77/100 ttft=   8260 ms  done=  20214 ms  in=5555  out=2539  reason=929  cached=4096
   78/100 ttft=   6643 ms  done=  15120 ms  in=3946  out=2083  reason=820  cached=3072
   79/100 ttft=   5525 ms  done=   7733 ms  in=5321  out=868  reason=578  cached=4096
   80/100 ttft=   8139 ms  done=  21737 ms  in=4371  out=2807  reason=809  cached=   0
   81/100 ttft=   3907 ms  done=   4914 ms  in=1722  out=530  reason=397  cached=1024
   82/100 ttft=   2153 ms  done=   3270 ms  in=1187  out=177  reason= 85  cached=1024
   83/100 ttft=   2178 ms  done=   3187 ms  in=1201  out=277  reason=140  cached=1024
   84/100 ttft=   7078 ms  done=   8321 ms  in=1691  out=391  reason=201  cached=1024
   85/100 ttft=   2448 ms  done=   3516 ms  in=1258  out=358  reason=214  cached=1024
   86/100 ttft=   3558 ms  done=   4961 ms  in=3012  out=505  reason=317  cached=2048
   87/100 ttft=   6648 ms  done=  23273 ms  in=5555  out=2939  reason=634  cached=4096
   88/100 ttft=   8253 ms  done=  15267 ms  in=3946  out=2087  reason=1006  cached=3072
   89/100 ttft=   4936 ms  done=   6810 ms  in=5321  out=760  reason=463  cached=4096
   90/100 ttft=   3602 ms  done=   7440 ms  in=4371  out=913  reason=365  cached=4096
   91/100 ttft=   4226 ms  done=   5712 ms  in=1722  out=627  reason=411  cached=1024
   92/100 ttft=   2179 ms  done=   2394 ms  in=1187  out=203  reason=117  cached=1024
   93/100 ttft=   4858 ms  done=   6149 ms  in=1201  out=712  reason=447  cached=1024
   94/100 ttft=   4168 ms  done=   5376 ms  in=1691  out=547  reason=406  cached=1024
   95/100 ttft=   4314 ms  done=   5540 ms  in=1258  out=630  reason=455  cached=   0
   96/100 ttft=   2828 ms  done=   4132 ms  in=3012  out=436  reason=249  cached=2048
   97/100 ttft=   4251 ms  done=  16866 ms  in=5555  out=2059  reason=390  cached=4096
   98/100 ttft=   6945 ms  done=  12679 ms  in=3946  out=1613  reason=817  cached=3072
   99/100 ttft=   2771 ms  done=   4114 ms  in=5321  out=307  reason=190  cached=4096
  100/100 ttft=   3140 ms  done=   7006 ms  in=4371  out=783  reason=288  cached=4096
summary
  model             deepseek/deepseek-v4-flash
  TTFT ms           p50 4197  p90 8253  p95 9349  p99 12271  max 15443
  completion ms     p50 5897  p95 21737  p99 23872
  throughput        135.3 tokens/s
  prompt tokens     p50 2367  min 1187  max 5555
  cache hits        95/100 (95.0%); cached tokens 199424
  observed cost     $0.044954
  Pearson(tokens, TTFT) +0.483
TTFT histogram
      0-500   ms     0
    500-1000  ms     0
   1000-1500  ms     0
   1500-2000  ms     4 ####
   2000-3000  ms    28 #########################
   3000-5000  ms    33 ##############################
   5000-10000 ms    32 #############################
  10000-20000 ms     3 ###
  >=20000    ms     0
TTFT by prompt-token count
   1187 tokens  n= 10  p50=  2405 ms  p95=  5753 ms
   1201 tokens  n= 10  p50=  2655 ms  p95=  6591 ms
   1258 tokens  n= 10  p50=  3186 ms  p95=  6926 ms
   1691 tokens  n= 10  p50=  4087 ms  p95=  7078 ms
   1722 tokens  n= 10  p50=  3783 ms  p95=  5546 ms
   3012 tokens  n= 10  p50=  2823 ms  p95=  3565 ms
   3946 tokens  n= 10  p50=  7116 ms  p95= 10207 ms
   4371 tokens  n= 10  p50=  6682 ms  p95= 15443 ms
   5321 tokens  n= 10  p50=  4755 ms  p95=  8537 ms
   5555 tokens  n= 10  p50=  6784 ms  p95=  9919 ms
raw JSON written to latency_evidence/phase3_provider_cache_on_N100.json
```

### `python3 bench_provider.py -n 100 --vary --layout combined --size-probe-requests 0 (cache off)`

```text
catalog price: $0.14/M input, $0.28/M output
provider TTFT benchmark (BoABot code bypassed)
  N=100, model=deepseek/deepseek-v4-flash, k=5, layout=combined, prompt=current, reasoning=auto, 10 fixtures
    1/100 ttft=   9013 ms  done=  11307 ms  in=1722  out=621  reason=459  cached=   0
    2/100 ttft=   3025 ms  done=   3814 ms  in=1187  out=212  reason=127  cached=   0
    3/100 ttft=   5210 ms  done=   7821 ms  in=1201  out=266  reason=136  cached= 256
    4/100 ttft=   5321 ms  done=   6633 ms  in=1691  out=613  reason=446  cached=   0
    5/100 ttft=   2221 ms  done=  46094 ms  in=1258  out=144  reason=  0  cached=   0
    6/100 ttft=   4963 ms  done=   6457 ms  in=3012  out=349  reason=235  cached=   0
    7/100 ttft=  20720 ms  done=  44727 ms  in=5555  out=2237  reason=825  cached=   0
    8/100 ttft=  16608 ms  done=  20040 ms  in=3946  out=853  reason=724  cached=   0
    9/100 ttft=    880 ms  done=  11693 ms  in=5321  out=757  reason=  0  cached=   0
   10/100 ttft=   6928 ms  done=  24834 ms  in=4371  out=2622  reason=618  cached=   0
   11/100 ttft=   2914 ms  done=   4479 ms  in=1722  out=390  reason=238  cached=   0
   12/100 ttft=   2729 ms  done=   3591 ms  in=1187  out=264  reason=180  cached=   0
   13/100 ttft=   5564 ms  done=   7553 ms  in=1201  out=298  reason=169  cached=   0
   14/100 ttft=    590 ms  done=   2923 ms  in=1691  out=153  reason=  0  cached=   0
   15/100 ttft=    848 ms  done=   7100 ms  in=1258  out=136  reason=  0  cached=   0
   16/100 ttft=   3664 ms  done=   5615 ms  in=3012  out=387  reason=198  cached=2816
   17/100 ttft=    943 ms  done=  17216 ms  in=5555  out=1114  reason=  0  cached= 256
   18/100 ttft=   7006 ms  done=   9006 ms  in=3946  out=832  reason=594  cached=   0
   19/100 ttft=   5973 ms  done=   6993 ms  in=5321  out=528  reason=426  cached=   0
   20/100 ttft=   8339 ms  done=  14353 ms  in=4371  out=1508  reason=799  cached=4096
   21/100 ttft=   9974 ms  done=  12646 ms  in=1722  out=545  reason=384  cached=   0
   22/100 ttft=   4494 ms  done=   6877 ms  in=1266  out=216  reason=124  cached=1152
   23/100 ttft=   4230 ms  done=   5709 ms  in=1201  out=393  reason=274  cached=   0
   24/100 ttft=   3063 ms  done=   4243 ms  in=1691  out=436  reason=279  cached=   0
   25/100 ttft=   3275 ms  done=   5030 ms  in=1258  out=473  reason=277  cached=   0
   26/100 ttft=   5903 ms  done=   8591 ms  in=3012  out=475  reason=282  cached=2816
   27/100 ttft=    707 ms  done=  21814 ms  in=5555  out=1334  reason=  0  cached=   0
   28/100 ttft=  11578 ms  done=  25410 ms  in=3946  out=2608  reason=1036  cached=   0
   29/100 ttft=   6866 ms  done=   9451 ms  in=5321  out=396  reason=269  cached=   0
   30/100 ttft=   8790 ms  done=  21081 ms  in=4371  out=2275  reason=831  cached=4096
   31/100 ttft=  15827 ms  done=  20254 ms  in=1722  out=661  reason=315  cached=   0
   32/100 ttft=   3637 ms  done=   5074 ms  in=1266  out=285  reason=193  cached=1152
   33/100 ttft=   4917 ms  done=   6541 ms  in=1201  out=297  reason=168  cached=   0
   34/100 ttft=   5137 ms  done=   7022 ms  in=1691  out=524  reason=350  cached= 256
   35/100 ttft=   7127 ms  done=   8367 ms  in=1258  out=720  reason=531  cached= 256
   36/100 ttft=   3464 ms  done=   4689 ms  in=3012  out=250  reason=161  cached=2816
   37/100 ttft=    731 ms  done=  18497 ms  in=5555  out=1342  reason=  0  cached=   0
   38/100 ttft=   1334 ms  done=   7680 ms  in=3946  out=480  reason=  0  cached=   0
   39/100 ttft=   1797 ms  done=  93154 ms  in=5321  out=646  reason=  0  cached=   0
   40/100 ttft=   8950 ms  done=  26270 ms  in=4371  out=2900  reason=908  cached=4096
   41/100 ttft=   1859 ms  done=  21458 ms  in=1722  out=135  reason=  0  cached=   0
   42/100 ttft=   4037 ms  done=   5028 ms  in=1266  out=283  reason=176  cached=1152
   43/100 ttft=   4088 ms  done=   5526 ms  in=1201  out=399  reason=268  cached=   0
   44/100 ttft=   3562 ms  done=   5206 ms  in=1691  out=277  reason=135  cached=1536
   45/100 ttft=   4805 ms  done=   6244 ms  in=1258  out=458  reason=290  cached=1024
   46/100 ttft=   4192 ms  done=   5649 ms  in=3012  out=385  reason=268  cached=2816
   47/100 ttft=   1485 ms  done= 110401 ms  in=5555  out=1306  reason=  0  cached=   0
   48/100 ttft=   8273 ms  done=  14759 ms  in=3946  out=1522  reason=756  cached=3072
   49/100 ttft=    641 ms  done= 117388 ms  in=5321  out=1054  reason=  0  cached=   0
   50/100 ttft=  13527 ms  done=  22893 ms  in=4371  out=2542  reason=1439  cached=   0
   51/100 ttft=   6105 ms  done=   7753 ms  in=1722  out=685  reason=499  cached=   0
   52/100 ttft=   3320 ms  done=   4245 ms  in=1266  out=162  reason= 52  cached=1152
   53/100 ttft=   5455 ms  done=   6792 ms  in=1201  out=580  reason=441  cached=   0
   54/100 ttft=   7001 ms  done=   8938 ms  in=1691  out=740  reason=563  cached=1536
   55/100 ttft=   4461 ms  done=   7928 ms  in=1258  out=356  reason=197  cached=1024
   56/100 ttft=   3033 ms  done=   3428 ms  in=3012  out=306  reason=123  cached=   0
   57/100 ttft=  27331 ms  done=  37873 ms  in=5555  out=4802  reason=3267  cached=   0
   58/100 ttft=   7988 ms  done=  13476 ms  in=3946  out=1326  reason=688  cached=3072
   59/100 ttft=  10155 ms  done=  18443 ms  in=5321  out=970  reason=466  cached=   0
   60/100 ttft=   2065 ms  done=  94244 ms  in=4371  out=900  reason=  0  cached=   0
   61/100 ttft=  13655 ms  done=  17831 ms  in=1722  out=679  reason=322  cached=   0
   62/100 ttft=   4531 ms  done=   6028 ms  in=1266  out=252  reason=145  cached=1152
   63/100 ttft=   4582 ms  done=   6096 ms  in=1201  out=331  reason=205  cached=   0
   64/100 ttft=   4439 ms  done=   6052 ms  in=1691  out=385  reason=249  cached=1536
   65/100 ttft=   6300 ms  done=   7828 ms  in=1258  out=1005  reason=743  cached=   0
   66/100 ttft=   3359 ms  done=   4890 ms  in=3012  out=452  reason=275  cached=   0
   67/100 ttft=   6281 ms  done=  16277 ms  in=5555  out=1504  reason=489  cached=   0
   68/100 ttft=  11114 ms  done=  17854 ms  in=3946  out=1867  reason=1094  cached=3072
   69/100 ttft=   4148 ms  done=  89294 ms  in=5321  out=784  reason=  0  cached=   0
   70/100 ttft=  18397 ms  done=  43306 ms  in=4371  out=2387  reason=887  cached=   0
   71/100 ttft=   4870 ms  done=   6188 ms  in=1722  out=611  reason=413  cached=   0
   72/100 ttft=   3457 ms  done=   4892 ms  in=1266  out=244  reason=147  cached=1152
   73/100 ttft=   4619 ms  done=   5650 ms  in=1201  out=485  reason=367  cached=   0
   74/100 ttft=   5722 ms  done=   7913 ms  in=1691  out=585  reason=393  cached=1536
   75/100 ttft=  10025 ms  done=  12992 ms  in=1258  out=648  reason=469  cached=   0
   76/100 ttft=   3778 ms  done=   5331 ms  in=3012  out=476  reason=301  cached=   0
   77/100 ttft=   1037 ms  done= 103668 ms  in=5555  out=1043  reason=  0  cached=   0
   78/100 ttft=   4861 ms  done=   8426 ms  in=3946  out=780  reason=377  cached=3072
   79/100 ttft=  14529 ms  done=  25326 ms  in=5321  out=1106  reason=538  cached=   0
   80/100 ttft=   7516 ms  done=  18736 ms  in=4371  out=1814  reason=485  cached= 256
   81/100 ttft=   3532 ms  done=   5331 ms  in=1801  out=308  reason=137  cached=1792
   82/100 ttft=   2995 ms  done=   4812 ms  in=1266  out=199  reason=112  cached=1152
   83/100 ttft=   1709 ms  done=  14892 ms  in=1201  out=183  reason=  0  cached=   0
   84/100 ttft=   3993 ms  done=   5627 ms  in=1691  out=432  reason=277  cached=1536
   85/100 ttft=   1687 ms  done=  11441 ms  in=1258  out=136  reason=  0  cached=   0
   86/100 ttft=   2990 ms  done=   3740 ms  in=3012  out=358  reason=227  cached=   0
   87/100 ttft=  12760 ms  done=  30903 ms  in=5555  out=1734  reason=585  cached=   0
   88/100 ttft=   4488 ms  done=   8579 ms  in=3946  out=877  reason=365  cached=3072
   89/100 ttft=   1144 ms  done=  63253 ms  in=5321  out=653  reason=  0  cached=   0
   90/100 ttft=   8084 ms  done=  18188 ms  in=4371  out=1940  reason=719  cached=4352
   91/100 ttft=   3407 ms  done=   4636 ms  in=1722  out=488  reason=299  cached=1024
   92/100 ttft=   2863 ms  done=   4222 ms  in=1266  out=211  reason=110  cached=1152
   93/100 ttft=   3917 ms  done=   4603 ms  in=1201  out=251  reason=136  cached= 256
   94/100 ttft=   3768 ms  done=   5550 ms  in=1691  out=348  reason=191  cached=1536
   95/100 ttft=   7664 ms  done=   8879 ms  in=1258  out=1077  reason=883  cached=1024
   96/100 ttft=   4565 ms  done=   6111 ms  in=3012  out=490  reason=319  cached=2048
   97/100 ttft=   1692 ms  done= 107216 ms  in=5555  out=980  reason=  0  cached=   0
   98/100 ttft=   8130 ms  done=  15828 ms  in=3946  out=1639  reason=725  cached=3072
   99/100 ttft=   1460 ms  done=  81259 ms  in=5321  out=613  reason=  0  cached=   0
  100/100 ttft=  11249 ms  done=  21348 ms  in=4371  out=2207  reason=1048  cached=4352
summary
  model             deepseek/deepseek-v4-flash
  TTFT ms           p50 4512  p90 11249  p95 14529  p99 20720  max 27331
  completion ms     p50 8397  p95 93154  p99 110401
  throughput        34.6 tokens/s
  prompt tokens     p50 2406  min 1187  max 5555
  cache hits        41/100 (41.0%); cached tokens 78592
  observed cost     $0.044635
  Pearson(tokens, TTFT) +0.228
TTFT histogram
      0-500   ms     0
    500-1000  ms     7 ######
   1000-1500  ms     5 ####
   1500-2000  ms     5 ####
   2000-3000  ms     7 ######
   3000-5000  ms    35 ##############################
   5000-10000 ms    27 #######################
  10000-20000 ms    12 ##########
  >=20000    ms     2 ##
TTFT by prompt-token count
   1187 tokens  n=  2  p50=  2877 ms  p95=  3025 ms
   1201 tokens  n= 10  p50=  4601 ms  p95=  5564 ms
   1258 tokens  n= 10  p50=  4633 ms  p95= 10025 ms
   1266 tokens  n=  8  p50=  3547 ms  p95=  4531 ms
   1691 tokens  n= 10  p50=  4216 ms  p95=  7001 ms
   1722 tokens  n=  9  p50=  6105 ms  p95= 15827 ms
   1801 tokens  n=  1  p50=  3532 ms  p95=  3532 ms
   3012 tokens  n= 10  p50=  3721 ms  p95=  5903 ms
   3946 tokens  n= 10  p50=  8059 ms  p95= 16608 ms
   4371 tokens  n= 10  p50=  8564 ms  p95= 18397 ms
   5321 tokens  n= 10  p50=  2973 ms  p95= 14529 ms
   5555 tokens  n= 10  p50=  1589 ms  p95= 27331 ms
raw JSON written to latency_evidence/phase3_provider_cache_off_N100.json
```

### `python3 bench_turn.py -n 100 (Gemini benchmark override, empty history)`

```text
/turn benchmark: N=100, mode=empty history, url=http://127.0.0.1:8100
 1 first_event=    165 ms  first_token=   1222 ms  first_sentence=   1873 ms  done=   1873 ms  tokens= 95  tok/s= 145.9
 2 first_event=    165 ms  first_token=    755 ms  first_sentence=    861 ms  done=   1028 ms  tokens= 22  tok/s=  80.7
 3 first_event=    159 ms  first_token=   1034 ms  first_sentence=   1157 ms  done=   8091 ms  tokens=148  tok/s=  21.0
 4 first_event=    156 ms  first_token=    694 ms  first_sentence=    864 ms  done=  23838 ms  tokens= 82  tok/s=   3.5
 5 first_event=    162 ms  first_token=    577 ms  first_sentence=    696 ms  done=    904 ms  tokens= 54  tok/s= 165.3
 6 first_event=    165 ms  first_token=    849 ms  first_sentence=    984 ms  done=   1446 ms  tokens= 67  tok/s= 112.3
 7 first_event=    148 ms  first_token=    858 ms  first_sentence=   1079 ms  done=   4125 ms  tokens=504  tok/s= 154.3
 8 first_event=    152 ms  first_token=    642 ms  first_sentence=    699 ms  done=   2532 ms  tokens=282  tok/s= 149.2
 9 first_event=    122 ms  first_token=    649 ms  first_sentence=    959 ms  done=   2600 ms  tokens=326  tok/s= 167.1
10 first_event=    159 ms  first_token=    700 ms  first_sentence=    847 ms  done=   2418 ms  tokens=325  tok/s= 189.2
11 first_event=    169 ms  first_token=    917 ms  first_sentence=   1136 ms  done=   1136 ms  tokens= 48  tok/s= 219.6
12 first_event=    173 ms  first_token=    752 ms  first_sentence=    840 ms  done=    892 ms  tokens= 27  tok/s= 192.4
13 first_event=    161 ms  first_token=  10608 ms  first_sentence=  10611 ms  done=  10692 ms  tokens=115  tok/s=1355.8
14 first_event=    149 ms  first_token=    898 ms  first_sentence=   1405 ms  done=   1405 ms  tokens= 96  tok/s= 189.2
15 first_event=    166 ms  first_token=    990 ms  first_sentence=   1105 ms  done=   1468 ms  tokens= 34  tok/s=  71.1
16 first_event=    144 ms  first_token=    607 ms  first_sentence=    715 ms  done=   1022 ms  tokens= 69  tok/s= 166.2
17 first_event=    151 ms  first_token=    651 ms  first_sentence=    995 ms  done=   3539 ms  tokens=452  tok/s= 156.5
18 first_event=    141 ms  first_token=    640 ms  first_sentence=    877 ms  done=   2387 ms  tokens=305  tok/s= 174.6
19 first_event=    139 ms  first_token=    655 ms  first_sentence=    926 ms  done=   2399 ms  tokens=309  tok/s= 177.1
20 first_event=    159 ms  first_token=    628 ms  first_sentence=   1002 ms  done=   2357 ms  tokens=340  tok/s= 196.6
21 first_event=    159 ms  first_token=    871 ms  first_sentence=    897 ms  done=    897 ms  tokens= 43  tok/s=1707.1
22 first_event=    187 ms  first_token=    807 ms  first_sentence=    898 ms  done=   1193 ms  tokens= 41  tok/s= 106.3
23 first_event=    163 ms  first_token=    569 ms  first_sentence=    695 ms  done=   1266 ms  tokens=141  tok/s= 202.2
24 first_event=    165 ms  first_token=    613 ms  first_sentence=    969 ms  done=   1017 ms  tokens= 78  tok/s= 193.2
25 first_event=    191 ms  first_token=   1334 ms  first_sentence=   1393 ms  done=   1727 ms  tokens= 76  tok/s= 193.7
26 first_event=    151 ms  first_token=    697 ms  first_sentence=    787 ms  done=   1022 ms  tokens= 50  tok/s= 153.6
27 first_event=    144 ms  first_token=    795 ms  first_sentence=   1194 ms  done=   3395 ms  tokens=424  tok/s= 163.1
28 first_event=    165 ms  first_token=    626 ms  first_sentence=    945 ms  done=   2100 ms  tokens=232  tok/s= 157.3
29 first_event=    132 ms  first_token=    682 ms  first_sentence=    851 ms  done=   2007 ms  tokens=226  tok/s= 170.6
30 first_event=    183 ms  first_token=    722 ms  first_sentence=    757 ms  done=   2210 ms  tokens=302  tok/s= 202.9
31 first_event=    171 ms  first_token=    680 ms  first_sentence=    846 ms  done=    874 ms  tokens= 43  tok/s= 221.7
32 first_event=    204 ms  first_token=    768 ms  first_sentence=    877 ms  done=    891 ms  tokens= 22  tok/s= 178.5
33 first_event=    165 ms  first_token=   2634 ms  first_sentence=   2826 ms  done=   3347 ms  tokens=150  tok/s= 210.4
34 first_event=    161 ms  first_token=   1193 ms  first_sentence=   1402 ms  done=   1472 ms  tokens= 53  tok/s= 189.9
35 first_event=    188 ms  first_token=    920 ms  first_sentence=    990 ms  done=   1382 ms  tokens= 55  tok/s= 119.0
36 first_event=    145 ms  first_token=   1047 ms  first_sentence=   1238 ms  done=   1256 ms  tokens= 37  tok/s= 177.6
37 first_event=    148 ms  first_token=   2496 ms  first_sentence=   2695 ms  done=   3406 ms  tokens=102  tok/s= 112.1
38 first_event=    152 ms  first_token=   1941 ms  first_sentence=   2806 ms  done=   3974 ms  tokens=236  tok/s= 116.1
39 first_event=    139 ms  first_token=    850 ms  first_sentence=   1588 ms  done=   3230 ms  tokens=410  tok/s= 172.2
40 first_event=    166 ms  first_token=   2355 ms  first_sentence=   2375 ms  done=   2875 ms  tokens=410  tok/s= 787.5
41 first_event=    165 ms  first_token=    995 ms  first_sentence=   1673 ms  done=   1673 ms  tokens= 98  tok/s= 144.5
42 first_event=    208 ms  first_token=   4061 ms  first_sentence=   4155 ms  done=   4305 ms  tokens= 22  tok/s=  90.1
43 first_event=    191 ms  first_token=   1009 ms  first_sentence=   1162 ms  done=   1846 ms  tokens=157  tok/s= 187.5
44 first_event=    156 ms  first_token=    844 ms  first_sentence=   1956 ms  done=   1956 ms  tokens= 97  tok/s=  87.2
45 first_event=    175 ms  first_token=    973 ms  first_sentence=   1182 ms  done=   1368 ms  tokens= 65  tok/s= 164.4
46 first_event=    151 ms  first_token=    644 ms  first_sentence=    842 ms  done=    858 ms  tokens= 37  tok/s= 172.9
47 first_event=    151 ms  first_token=    717 ms  first_sentence=    934 ms  done=   3494 ms  tokens=453  tok/s= 163.2
48 first_event=    236 ms  first_token=   7593 ms  first_sentence=   7648 ms  done=   8664 ms  tokens=188  tok/s= 175.5
49 first_event=    142 ms  first_token=    902 ms  first_sentence=   1057 ms  done=   2578 ms  tokens=302  tok/s= 180.3
50 first_event=    174 ms  first_token=   5986 ms  first_sentence=   6023 ms  done=   8095 ms  tokens=406  tok/s= 192.5
51 first_event=    185 ms  first_token=    679 ms  first_sentence=    898 ms  done=    898 ms  tokens= 48  tok/s= 219.2
52 first_event=    174 ms  first_token=    965 ms  first_sentence=   1067 ms  done=   1468 ms  tokens= 50  tok/s=  99.3
53 first_event=    160 ms  first_token=    716 ms  first_sentence=    763 ms  done=   1328 ms  tokens=150  tok/s= 245.2
54 first_event=    159 ms  first_token=    822 ms  first_sentence=   1479 ms  done=   1479 ms  tokens= 97  tok/s= 147.6
55 first_event=    177 ms  first_token=    886 ms  first_sentence=   1336 ms  done=   1401 ms  tokens= 55  tok/s= 106.8
56 first_event=    147 ms  first_token=    716 ms  first_sentence=    756 ms  done=   1163 ms  tokens= 64  tok/s= 143.0
57 first_event=    143 ms  first_token=    760 ms  first_sentence=    940 ms  done=   3367 ms  tokens=426  tok/s= 163.4
58 first_event=    148 ms  first_token=    640 ms  first_sentence=   1017 ms  done=   1895 ms  tokens=229  tok/s= 182.4
59 first_event=    128 ms  first_token=    744 ms  first_sentence=   1170 ms  done=   2908 ms  tokens=395  tok/s= 182.6
60 first_event=    177 ms  first_token=   1203 ms  first_sentence=   1260 ms  done=   3066 ms  tokens=335  tok/s= 179.8
61 first_event=    170 ms  first_token=    628 ms  first_sentence=    802 ms  done=    838 ms  tokens= 43  tok/s= 203.9
62 first_event=    202 ms  first_token=    919 ms  first_sentence=   1374 ms  done=   1438 ms  tokens= 25  tok/s=  48.1
63 first_event=    191 ms  first_token=    844 ms  first_sentence=    983 ms  done=   2689 ms  tokens=159  tok/s=  86.2
64 first_event=    167 ms  first_token=   1258 ms  first_sentence=   1905 ms  done=   1905 ms  tokens= 95  tok/s= 146.8
65 first_event=    196 ms  first_token=    729 ms  first_sentence=    925 ms  done=   1012 ms  tokens= 42  tok/s= 148.8
66 first_event=    153 ms  first_token=    625 ms  first_sentence=    791 ms  done=    804 ms  tokens= 37  tok/s= 206.7
67 first_event=    156 ms  first_token=   1017 ms  first_sentence=   1232 ms  done=   3748 ms  tokens=398  tok/s= 145.7
68 first_event=    151 ms  first_token=   5519 ms  first_sentence=   7592 ms  done=   8096 ms  tokens=161  tok/s=  62.5
69 first_event=    137 ms  first_token=    873 ms  first_sentence=   1040 ms  done=   2736 ms  tokens=364  tok/s= 195.4
70 first_event=    168 ms  first_token=   1246 ms  first_sentence=   1403 ms  done=   3351 ms  tokens=376  tok/s= 178.7
71 first_event=    183 ms  first_token=   1134 ms  first_sentence=   1684 ms  done=   1684 ms  tokens= 75  tok/s= 136.5
72 first_event=    185 ms  first_token=    663 ms  first_sentence=    730 ms  done=    756 ms  tokens= 27  tok/s= 291.1
73 first_event=    170 ms  first_token=   1233 ms  first_sentence=   1332 ms  done=   2078 ms  tokens=146  tok/s= 172.6
74 first_event=    157 ms  first_token=   2706 ms  first_sentence=   2994 ms  done=   2994 ms  tokens=106  tok/s= 367.6
75 first_event=    168 ms  first_token=   8644 ms  first_sentence=   9657 ms  done=   9957 ms  tokens= 68  tok/s=  51.8
76 first_event=    145 ms  first_token=    661 ms  first_sentence=    782 ms  done=   1080 ms  tokens= 68  tok/s= 162.2
77 first_event=    162 ms  first_token=    781 ms  first_sentence=    959 ms  done=   3131 ms  tokens=414  tok/s= 176.2
78 first_event=    159 ms  first_token=    850 ms  first_sentence=   1117 ms  done=   2144 ms  tokens=238  tok/s= 184.0
79 first_event=    126 ms  first_token=    700 ms  first_sentence=   1380 ms  done=   5089 ms  tokens=414  tok/s=  94.3
80 first_event=    162 ms  first_token=   1115 ms  first_sentence=   1178 ms  done=   3774 ms  tokens=375  tok/s= 141.0
81 first_event=    177 ms  first_token=   1802 ms  first_sentence=   2034 ms  done=   2034 ms  tokens= 95  tok/s= 410.0
82 first_event=    179 ms  first_token=    682 ms  first_sentence=    758 ms  done=    911 ms  tokens= 47  tok/s= 205.3
83 first_event=    179 ms  first_token=  12024 ms  first_sentence=  12054 ms  done=  12945 ms  tokens=253  tok/s= 274.7
84 first_event=    148 ms  first_token=    719 ms  first_sentence=   1272 ms  done=   1272 ms  tokens= 97  tok/s= 175.3
85 first_event=    184 ms  first_token=    725 ms  first_sentence=    871 ms  done=   1078 ms  tokens= 57  tok/s= 161.4
86 first_event=    134 ms  first_token=    702 ms  first_sentence=    822 ms  done=   1105 ms  tokens= 55  tok/s= 136.3
87 first_event=    138 ms  first_token=   4611 ms  first_sentence=   4621 ms  done=   7528 ms  tokens=489  tok/s= 167.6
88 first_event=    147 ms  first_token=    875 ms  first_sentence=   1316 ms  done=   2477 ms  tokens=266  tok/s= 166.0
89 first_event=    146 ms  first_token=    855 ms  first_sentence=   1414 ms  done=   3138 ms  tokens=363  tok/s= 159.0
90 first_event=    185 ms  first_token=    902 ms  first_sentence=    959 ms  done=   2725 ms  tokens=331  tok/s= 181.5
91 first_event=    181 ms  first_token=    704 ms  first_sentence=    893 ms  done=    917 ms  tokens= 43  tok/s= 201.8
92 first_event=    178 ms  first_token=    673 ms  first_sentence=    792 ms  done=    836 ms  tokens= 29  tok/s= 178.0
93 first_event=    171 ms  first_token=    818 ms  first_sentence=    977 ms  done=   1719 ms  tokens=147  tok/s= 163.1
94 first_event=    162 ms  first_token=    753 ms  first_sentence=    898 ms  done=    947 ms  tokens= 45  tok/s= 231.2
95 first_event=    186 ms  first_token=    675 ms  first_sentence=   3066 ms  done=   3186 ms  tokens= 70  tok/s=  27.9
96 first_event=    150 ms  first_token=    795 ms  first_sentence=    849 ms  done=   1704 ms  tokens= 52  tok/s=  57.3
97 first_event=    150 ms  first_token=   6821 ms  first_sentence=   6992 ms  done=   9402 ms  tokens=468  tok/s= 181.3
98 first_event=    162 ms  first_token=    619 ms  first_sentence=    971 ms  done=   1182 ms  tokens=104  tok/s= 184.7
99 first_event=    132 ms  first_token=   1073 ms  first_sentence=   1093 ms  done=   2201 ms  tokens=259  tok/s= 229.5
100 first_event=    195 ms  first_token=   3905 ms  first_sentence=   4049 ms  done=   6441 ms  tokens=399  tok/s= 157.3
summary (ms)
  first SSE event  p50     162  p90     187  p95     195  p99     208  max     236  n 100/100
  first token      p50     844  p90    2706  p95    5986  p99   10608  max   12024  n 100/100
  first sentence   p50    1062  p90    3066  p95    6992  p99   10611  max   12054  n 100/100
  done             p50    1982  p90    6441  p95    8664  p99   12945  max   23838  n 100/100
first-token histogram
      0-500   ms     0
    500-1000  ms    70 ##############################
   1000-1500  ms    14 ######
   1500-2000  ms     2 #
   2000-3000  ms     4 ##
   3000-5000  ms     3 #
   5000-10000 ms     5 ##
  10000-20000 ms     2 #
  >=20000    ms     0
generation (lexical-token proxy; words, numbers, and punctuation)
  output tokens/s  p50 171.4  p10 86.2  n 100
  first-sentence tokens mean 37.7  n 100/100
  first-sentence p50 decomposition  TTFT 844 ms + generation 170 ms
raw JSON written to latency_evidence/phase3_gemini_empty_N100.json
```

### `python3 bench_turn.py -n 100 --history (Gemini benchmark override)`

```text
/turn benchmark: N=100, mode=non-empty history, url=http://127.0.0.1:8100
 1 first_event=    151 ms  first_token=   2949 ms  first_sentence=   5702 ms  done=   5702 ms  tokens= 83  tok/s=  30.1
 2 first_event=    170 ms  first_token=   2220 ms  first_sentence=   2635 ms  done=   2884 ms  tokens= 47  tok/s=  70.8
 3 first_event=    165 ms  first_token=    693 ms  first_sentence=    828 ms  done=   1070 ms  tokens= 61  tok/s= 161.6
 4 first_event=    143 ms  first_token=    659 ms  first_sentence=   1102 ms  done=   1102 ms  tokens= 85  tok/s= 191.9
 5 first_event=    193 ms  first_token=    669 ms  first_sentence=    794 ms  done=    824 ms  tokens= 28  tok/s= 181.0
 6 first_event=    146 ms  first_token=    801 ms  first_sentence=   1128 ms  done=   1198 ms  tokens= 39  tok/s=  98.3
 7 first_event=    147 ms  first_token=    718 ms  first_sentence=    841 ms  done=   3765 ms  tokens=488  tok/s= 160.2
 8 first_event=    186 ms  first_token=   1007 ms  first_sentence=   1376 ms  done=   2508 ms  tokens=228  tok/s= 151.9
 9 first_event=    135 ms  first_token=    710 ms  first_sentence=    947 ms  done=   2437 ms  tokens=249  tok/s= 144.2
10 first_event=    168 ms  first_token=   1445 ms  first_sentence=   1493 ms  done=   3058 ms  tokens=315  tok/s= 195.3
11 first_event=    154 ms  first_token=    690 ms  first_sentence=   1052 ms  done=   1052 ms  tokens= 73  tok/s= 201.7
12 first_event=    181 ms  first_token=    607 ms  first_sentence=    702 ms  done=    832 ms  tokens= 47  tok/s= 208.7
13 first_event=    172 ms  first_token=   1069 ms  first_sentence=   1163 ms  done=   1177 ms  tokens= 23  tok/s= 212.4
14 first_event=    180 ms  first_token=    695 ms  first_sentence=   1043 ms  done=   1104 ms  tokens= 71  tok/s= 173.3
15 first_event=    171 ms  first_token=    605 ms  first_sentence=    730 ms  done=    976 ms  tokens= 76  tok/s= 204.8
16 first_event=    151 ms  first_token=    692 ms  first_sentence=    780 ms  done=   1061 ms  tokens= 49  tok/s= 132.6
17 first_event=    151 ms  first_token=   1180 ms  first_sentence=   1190 ms  done=   3442 ms  tokens=438  tok/s= 193.6
18 first_event=    188 ms  first_token=    953 ms  first_sentence=   1067 ms  done=   2564 ms  tokens=245  tok/s= 152.1
19 first_event=    148 ms  first_token=    619 ms  first_sentence=    956 ms  done=   2341 ms  tokens=175  tok/s= 101.6
20 first_event=    165 ms  first_token=   4761 ms  first_sentence=   4925 ms  done=   6613 ms  tokens=328  tok/s= 177.1
21 first_event=    164 ms  first_token=    930 ms  first_sentence=   1454 ms  done=   1454 ms  tokens= 48  tok/s=  91.7
22 first_event=    186 ms  first_token=    603 ms  first_sentence=    714 ms  done=    848 ms  tokens= 49  tok/s= 199.4
23 first_event=    168 ms  first_token=    684 ms  first_sentence=    848 ms  done=   1364 ms  tokens=121  tok/s= 178.0
24 first_event=    151 ms  first_token=    604 ms  first_sentence=   1003 ms  done=   1003 ms  tokens= 82  tok/s= 205.5
25 first_event=    169 ms  first_token=    590 ms  first_sentence=    683 ms  done=    893 ms  tokens= 68  tok/s= 224.2
26 first_event=    154 ms  first_token=   1209 ms  first_sentence=   1412 ms  done=   1439 ms  tokens= 39  tok/s= 169.9
27 first_event=    144 ms  first_token=    877 ms  first_sentence=   1441 ms  done=   3618 ms  tokens=446  tok/s= 162.7
28 first_event=    148 ms  first_token=    724 ms  first_sentence=   1288 ms  done=   2129 ms  tokens=218  tok/s= 155.2
29 first_event=    139 ms  first_token=    734 ms  first_sentence=    979 ms  done=   1694 ms  tokens=161  tok/s= 167.7
30 first_event=    162 ms  first_token=    706 ms  first_sentence=    746 ms  done=   2339 ms  tokens=319  tok/s= 195.3
31 first_event=    167 ms  first_token=    566 ms  first_sentence=   1300 ms  done=   1300 ms  tokens= 48  tok/s=  65.4
32 first_event=    191 ms  first_token=    685 ms  first_sentence=    795 ms  done=   1019 ms  tokens= 47  tok/s= 140.6
33 first_event=    168 ms  first_token=    771 ms  first_sentence=   1027 ms  done=   1090 ms  tokens= 25  tok/s=  78.4
34 first_event=    145 ms  first_token=    850 ms  first_sentence=   1277 ms  done=   1410 ms  tokens= 77  tok/s= 137.5
35 first_event=    163 ms  first_token=   1246 ms  first_sentence=   1349 ms  done=   1507 ms  tokens= 56  tok/s= 214.8
36 first_event=    155 ms  first_token=    921 ms  first_sentence=   1314 ms  done=   1383 ms  tokens= 39  tok/s=  84.4
37 first_event=    155 ms  first_token=    650 ms  first_sentence=    743 ms  done=   3259 ms  tokens=440  tok/s= 168.6
38 first_event=    157 ms  first_token=    916 ms  first_sentence=   1185 ms  done=   2599 ms  tokens=246  tok/s= 146.2
39 first_event=    136 ms  first_token=    837 ms  first_sentence=   1054 ms  done=   2485 ms  tokens=245  tok/s= 148.6
40 first_event=    166 ms  first_token=    800 ms  first_sentence=    946 ms  done=   2554 ms  tokens=316  tok/s= 180.2
41 first_event=    170 ms  first_token=    647 ms  first_sentence=    840 ms  done=    994 ms  tokens= 67  tok/s= 193.3
42 first_event=    170 ms  first_token=   1004 ms  first_sentence=   1130 ms  done=   1807 ms  tokens= 49  tok/s=  61.0
43 first_event=    166 ms  first_token=    657 ms  first_sentence=    779 ms  done=    995 ms  tokens= 64  tok/s= 189.6
44 first_event=    153 ms  first_token=    586 ms  first_sentence=    922 ms  done=    933 ms  tokens= 63  tok/s= 181.4
45 first_event=    186 ms  first_token=    741 ms  first_sentence=   2001 ms  done=   2103 ms  tokens= 49  tok/s=  36.0
46 first_event=    163 ms  first_token=    635 ms  first_sentence=    735 ms  done=    876 ms  tokens= 38  tok/s= 158.0
47 first_event=    141 ms  first_token=   1077 ms  first_sentence=   1459 ms  done=   3736 ms  tokens=422  tok/s= 158.7
48 first_event=    163 ms  first_token=    768 ms  first_sentence=   1212 ms  done=   2198 ms  tokens=219  tok/s= 153.2
49 first_event=    129 ms  first_token=   1192 ms  first_sentence=   1222 ms  done=   2556 ms  tokens=251  tok/s= 184.1
50 first_event=    158 ms  first_token=    953 ms  first_sentence=   1005 ms  done=   2828 ms  tokens=337  tok/s= 179.7
51 first_event=    188 ms  first_token=   1059 ms  first_sentence=   1313 ms  done=   1313 ms  tokens= 51  tok/s= 200.7
52 first_event=    167 ms  first_token=   1017 ms  first_sentence=   1045 ms  done=   1295 ms  tokens= 51  tok/s= 183.4
53 first_event=    185 ms  first_token=   1423 ms  first_sentence=   1584 ms  done=   2066 ms  tokens= 63  tok/s=  97.9
54 first_event=    151 ms  first_token=    880 ms  first_sentence=   1450 ms  done=   1506 ms  tokens= 91  tok/s= 145.4
55 first_event=    163 ms  first_token=    575 ms  first_sentence=    661 ms  done=    881 ms  tokens= 47  tok/s= 153.6
56 first_event=    141 ms  first_token=    869 ms  first_sentence=    959 ms  done=   1212 ms  tokens= 37  tok/s= 107.9
57 first_event=    139 ms  first_token=    751 ms  first_sentence=   1159 ms  done=   3227 ms  tokens=399  tok/s= 161.2
58 first_event=    156 ms  first_token=   1006 ms  first_sentence=   1405 ms  done=   2463 ms  tokens=203  tok/s= 139.3
59 first_event=    131 ms  first_token=    671 ms  first_sentence=    872 ms  done=   2818 ms  tokens=415  tok/s= 193.3
60 first_event=    172 ms  first_token=    890 ms  first_sentence=    960 ms  done=   3418 ms  tokens=337  tok/s= 133.3
61 first_event=    151 ms  first_token=    810 ms  first_sentence=   1196 ms  done=   1268 ms  tokens= 86  tok/s= 187.9
62 first_event=    177 ms  first_token=    881 ms  first_sentence=    988 ms  done=   1311 ms  tokens= 51  tok/s= 118.6
63 first_event=    172 ms  first_token=    789 ms  first_sentence=   1005 ms  done=   1073 ms  tokens= 23  tok/s=  81.0
64 first_event=    152 ms  first_token=    918 ms  first_sentence=   1958 ms  done=   1958 ms  tokens=100  tok/s=  96.2
65 first_event=    169 ms  first_token=    749 ms  first_sentence=    845 ms  done=   1010 ms  tokens= 23  tok/s=  88.0
66 first_event=    146 ms  first_token=    788 ms  first_sentence=   1438 ms  done=   1483 ms  tokens= 39  tok/s=  56.1
67 first_event=    170 ms  first_token=   1158 ms  first_sentence=   1600 ms  done=   3726 ms  tokens=436  tok/s= 169.8
68 first_event=    164 ms  first_token=    819 ms  first_sentence=   1130 ms  done=   2872 ms  tokens=221  tok/s= 107.6
69 first_event=    142 ms  first_token=    700 ms  first_sentence=    872 ms  done=   2622 ms  tokens=297  tok/s= 154.5
70 first_event=    154 ms  first_token=    756 ms  first_sentence=    865 ms  done=   2611 ms  tokens=361  tok/s= 194.6
71 first_event=    159 ms  first_token=    636 ms  first_sentence=    835 ms  done=    850 ms  tokens= 38  tok/s= 177.5
72 first_event=    208 ms  first_token=   1000 ms  first_sentence=   1088 ms  done=   1375 ms  tokens= 43  tok/s= 114.8
73 first_event=    177 ms  first_token=    675 ms  first_sentence=    833 ms  done=    849 ms  tokens= 24  tok/s= 137.8
74 first_event=    178 ms  first_token=    710 ms  first_sentence=   1063 ms  done=   1077 ms  tokens= 71  tok/s= 193.1
75 first_event=    150 ms  first_token=    909 ms  first_sentence=   1159 ms  done=   1237 ms  tokens= 36  tok/s= 109.9
76 first_event=    169 ms  first_token=    705 ms  first_sentence=    810 ms  done=    907 ms  tokens= 32  tok/s= 158.5
77 first_event=    170 ms  first_token=    708 ms  first_sentence=    808 ms  done=   3362 ms  tokens=434  tok/s= 163.5
78 first_event=    152 ms  first_token=    574 ms  first_sentence=    953 ms  done=   2368 ms  tokens=320  tok/s= 178.3
79 first_event=    170 ms  first_token=    675 ms  first_sentence=    935 ms  done=   2223 ms  tokens=233  tok/s= 150.5
80 first_event=    165 ms  first_token=   2024 ms  first_sentence=   2130 ms  done=   3919 ms  tokens=346  tok/s= 182.6
81 first_event=    178 ms  first_token=    701 ms  first_sentence=    997 ms  done=    997 ms  tokens= 62  tok/s= 209.2
82 first_event=    223 ms  first_token=    988 ms  first_sentence=   1115 ms  done=   1802 ms  tokens= 47  tok/s=  57.7
83 first_event=    175 ms  first_token=   1031 ms  first_sentence=   1282 ms  done=   1399 ms  tokens= 23  tok/s=  62.6
84 first_event=    152 ms  first_token=    597 ms  first_sentence=    840 ms  done=   1215 ms  tokens= 54  tok/s=  87.5
85 first_event=    166 ms  first_token=    778 ms  first_sentence=    851 ms  done=   1151 ms  tokens= 51  tok/s= 136.8
86 first_event=    133 ms  first_token=    655 ms  first_sentence=    850 ms  done=    861 ms  tokens= 39  tok/s= 189.1
87 first_event=    132 ms  first_token=    903 ms  first_sentence=   1234 ms  done=   3553 ms  tokens=413  tok/s= 155.9
88 first_event=    145 ms  first_token=    584 ms  first_sentence=    933 ms  done=   2187 ms  tokens=273  tok/s= 170.3
89 first_event=    125 ms  first_token=    906 ms  first_sentence=   1063 ms  done=   2548 ms  tokens=262  tok/s= 159.6
90 first_event=    159 ms  first_token=   1562 ms  first_sentence=   1598 ms  done=   3519 ms  tokens=304  tok/s= 155.3
91 first_event=    156 ms  first_token=    809 ms  first_sentence=   1428 ms  done=   1428 ms  tokens=106  tok/s= 171.2
92 first_event=    172 ms  first_token=   2916 ms  first_sentence=   3008 ms  done=   3243 ms  tokens= 47  tok/s= 143.9
93 first_event=    159 ms  first_token=    802 ms  first_sentence=   1027 ms  done=   1090 ms  tokens= 22  tok/s=  76.3
94 first_event=    163 ms  first_token=   1411 ms  first_sentence=   1494 ms  done=   1543 ms  tokens= 97  tok/s= 735.1
95 first_event=    168 ms  first_token=    644 ms  first_sentence=    768 ms  done=    779 ms  tokens= 23  tok/s= 170.4
96 first_event=    152 ms  first_token=    594 ms  first_sentence=    782 ms  done=    793 ms  tokens= 39  tok/s= 195.6
97 first_event=    162 ms  first_token=   1243 ms  first_sentence=   1580 ms  done=   3435 ms  tokens=346  tok/s= 157.8
98 first_event=    144 ms  first_token=    752 ms  first_sentence=   1129 ms  done=   2524 ms  tokens=222  tok/s= 125.2
99 first_event=    135 ms  first_token=    865 ms  first_sentence=   1046 ms  done=   2244 ms  tokens=220  tok/s= 159.5
100 first_event=    166 ms  first_token=    840 ms  first_sentence=    912 ms  done=   2495 ms  tokens=334  tok/s= 201.8
summary (ms)
  first SSE event  p50     163  p90     181  p95     188  p99     208  max     223  n 100/100
  first token      p50     789  p90    1243  p95    1562  p99    2949  max    4761  n 100/100
  first sentence   p50    1049  p90    1580  p95    2001  p99    4925  max    5702  n 100/100
  done             p50    1506  p90    3435  p95    3726  p99    5702  max    6613  n 100/100
first-token histogram
      0-500   ms     0
    500-1000  ms    76 ##############################
   1000-1500  ms    18 #######
   1500-2000  ms     1 #
   2000-3000  ms     4 ##
   3000-5000  ms     1 #
   5000-10000 ms     0
  10000-20000 ms     0
  >=20000    ms     0
generation (lexical-token proxy; words, numbers, and punctuation)
  output tokens/s  p50 159.1  p10 78.4  n 100
  first-sentence tokens mean 36.8  n 100/100
  first-sentence p50 decomposition  TTFT 789 ms + generation 197 ms
raw JSON written to latency_evidence/phase3_gemini_history_N100.json
```

### `python3 eval.py`

```text
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.

Loading weights:   0%|          | 0/391 [00:00<?, ?it/s]
Loading weights: 100%|██████████| 391/391 [00:00<00:00, 23140.25it/s]
Set                     RegArt@1     RegArt@3     RegArt@5     RegArt@10    RegID@1      RegID@3      RegID@5      RegID@10     RegDoc@1     RegDoc@3     RegDoc@5     RegDoc@10     p50 ms   p95 ms   Miss   R?Bk  Trap refusals   Trap recall (secondary)
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
old (buggy)             -  -  -  -  -  -  -  -  -  -  -  -      198      236      0      7              -                         -
generated               -  -  -  -  -  -  -  -  -  -  -  -      208      241      1      0              -                         -
handwritten             0.550  0.550  0.650  0.650  0.400  0.400  0.500  0.500  0.800  0.950  0.950  1.000      185      225     10      0            3/5  R@1=1/5/R@3=1/5/R@5=1/5/R@10=1/5


  generated — misses (1):
    rate_0111      Sa eshte terheqje cash nga terminalet e bankes per karte debiti n
                   top: ['rate_0099', 'rate_0102', 'rate_0113']

  handwritten — misses (10):
    reg_00538      Kush e administron Regjistrin e Kredive dhe cfare lloj te dhenash
                   top: ['reg_03469', 'reg_00007', 'reg_03468']
    reg_00203      Cilat jane kerkesat per licencimin e nje banke ne Shqiperi?
                   top: ['reg_00196', 'reg_00193', 'reg_02453']
    reg_02157      Cfare informacioni duhet te publikojne bankat per produktet e tyr
                   top: ['reg_01778', 'reg_02145', 'reg_01777']
    reg_03181      Cilat jane kriteret per dhenien e licenses per institucionet e pa
                   top: ['reg_03622', 'reg_03189', 'reg_03173']
    reg_00662      Si funksionon tregu sekondar i titujve ne sistemin AFISaR?
                   top: ['reg_00663', 'reg_00656', 'reg_00664']
    reg_03642      Cili eshte raporti neto i financimit te qendrueshem NSFR?
                   top: ['reg_03708', 'reg_03759', 'reg_03631']
    ... (4 more)

--- Trap gate results (headline: refusals; target 0) ---
  handwritten: refusal rate 3/5
    reason breakdown: wrong_chunk_family=3
    refused (wrong_chunk_family): Si percaktohet norma e interesit per kredite konsumatore ne rregulloren Nr 48?
    refused (wrong_chunk_family): Cila eshte norma rregullatore e kapitalit qe bankat duhet te mbajne?
    refused (wrong_chunk_family): Si administrohet rreziku i normes se interesit ne librin bankar?

--- Recall breakdown by prefix ---
  old (buggy)             rate=40/40  reg=-
  generated               rate=39/40  reg=-
  handwritten             rate=20/20  reg=10/20
```

### `python3 eval_calls.py`

```text
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.

Loading weights:   0%|          | 0/391 [00:00<?, ?it/s]
Loading weights: 100%|██████████| 391/391 [00:00<00:00, 15054.46it/s]
call-policy eval passed: 16/16
```
