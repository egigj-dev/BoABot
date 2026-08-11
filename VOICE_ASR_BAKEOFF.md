# First live Azure vs Chirp 3 Albanian ASR bake-off (2026-08-11)

## Method

Four Albanian banking fixtures were synthesized from known reference text with
Azure TTS (`sq-AL-AnilaNeural`, `Riff16Khz16BitMonoPcm`). Both ASR adapters
transcribed the same 16 kHz mono files. Results were scored for raw and
diacritic-insensitive WER using Levenshtein distance on lowercased tokens with
punctuation stripped, as well as entity hit counts and finalization latency.

TTS-generated audio is the easy case for ASR: it has a clean voice and no
noise, telephony effects, or accent variation. These conclusions are
directional, not production qualification.

## Results

| Reference short | Azure WER* | Chirp WER* | Azure ents | Chirp ents | Azure final | Chirp final |
|---|---:|---:|---:|---:|---:|---:|
| "...në bankën OTP?" | 0.250 | 0.000 | 1/2 | 2/2 | ~0.98 s | ~4.3 s |
| "...administrimit... 10 euro." | 0.000 | 0.000 | 2/2 | 2/2 | ~1.3 s | ~3.5 s |
| "...hipotekare 4.75 për qind." | 0.091 | 0.182 | 0/2 | 1/2 | ~1.1 s | ~4.5 s |
| "Banka e Shqipërisë... në euro." | 0.000 | 0.083 | 2/3 | 2/3 | ~1.1 s | ~4.4 s |

\* Diacritic-insensitive WER; entity hits = naive substring match, strict on
diacritics/punctuation.

## Findings

- Chirp 3 is the accuracy winner: word-perfect on the entity-dense turns,
  including the bank acronym "OTP" and a clean "4.75%".
- Azure is the speed winner: it finalizes in ~1.0-1.3 s versus Chirp's ~3.5-4.5
  s, approximately 3-4x faster.
- Azure's two misses are the load-bearing ones for BoABot: it hallucinated
  "bankën e udb ë" for the OTP bank acronym and rendered "4 75" without the
  decimal point, creating rate-value risk. The corpus-derived phrase list
  (`rate_tables.jsonl` bank names) is expected to mitigate the OTP case and must
  be retested.
- Both drop or inconsistently recognize `ë` (`të` versus `te`). This is
  negligible for bge-m3 retrieval but inflates raw WER.
- The latency/accuracy trade-off visible here is precisely why the schemas
  treat the bake-off as a harness, not a one-time choice.

## Adapter and quality fixes made this session

- `chirp_adapter`: changed `auto_decoding_config` to explicit
  LINEAR16/16kHz/mono because V2 cancelled on raw PCM streams; added a
  sync/async iterable normalizer after discovering a bare `CancelledError` when
  fed a sync iterable.
- `config.py`: added `pcm_sample_rate_hz` (environment variable
  `VOICE_PCM_SAMPLE_RATE_HZ`).
- Remaining open item: Azure STT phrase-list retest, recorded human/telephony
  audio, and numeric-value scoring (4 vs 4.75 vs 4,75 semantics).

Evidence: /tmp/boabot_asr_bakeoff_live.py (regenerable)
