# BoABot Arm A vs Arm B live end-to-end evaluation

Generated: 2026-08-14T06:50:09.054365+00:00

Each question was run once per arm against the `api:app` snapshot loaded from the current tree at its 06:21:49 UTC restart. No failed question was retried. Concurrent workspace edits appeared later but were not loaded because uvicorn was not running with `--reload`; exact runtime hashes and `pii_redacted` provenance are explained below.

## Summary matrix

| Arm | Section | Pass | Fail | Error | Total |
|---|---:|---:|---:|---:|---:|
| A | answer | 1 | 23 | 0 | 24 |
| A | clarify | 1 | 0 | 0 | 1 |
| A | unsupported | 1 | 0 | 0 | 1 |
| A | handoff | 3 | 1 | 0 | 4 |
| A | overall | 6 | 24 | 0 | 30 |
| B | answer | 0 | 24 | 0 | 24 |
| B | clarify | 0 | 0 | 1 | 1 |
| B | unsupported | 1 | 0 | 0 | 1 |
| B | handoff | 4 | 0 | 0 | 4 |
| B | overall | 5 | 24 | 1 | 30 |

## Arm A: per-question results

| # | QA | Expected | Actual | Result | Handoff / PII | Audio bytes / frames | Figure | Transcript |
|---:|---|---|---|---|---|---|---|---|
| 1 | qa-001 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa është interesi për 1 depozitë pa afat raiffeisen. |
| 2 | qa-002 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana? |
| 3 | qa-003 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa jep credins për depozite 12 mujore në shumën minimale. |
| 4 | qa-004 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Po 1 depozitë 36 mujore në shumën maksimale TOTP sa e ka normën. |
| 5 | qa-005 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Për depozita 12 mujore në shumën minimale sa janë të p dhe union. |
| 6 | qa-006 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë. |
| 7 | qa-007 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Çfarë përqindje ka atë për shlyerjen e parakohshme pjesërisht ose totalisht të kredisë së shtëpisë. |
| 8 | qa-008 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë të bkt. |
| 9 | qa-009 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar të intesa sanpaolo. |
| 10 | qa-010 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Tepër kredit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar? |
| 11 | qa-011 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa është komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të raiffeisen. |
| 12 | qa-012 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Sa kushton dhënia e 1 i tëri për kartë krediti biznesi të bkt. |
| 13 | qa-013 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Çfarë vlere ka lëshimi i kartës së debitit për biznes teutë p. |
| 14 | qa-014 | answer | handoff | FAIL | True / False | 322844 / 161400 | N | Për 1 kartë debiti biznesi të raiffeisen sa është mirëmbajtja vjetore. |
| 15 | qa-015 | answer | answer | PASS | False / False | 360844 / 180400 | Y | Sa është komisioni minimal për tërheqjen me kartë debiti biznesi në atm të bankave të tjera të Union Bank. |
| 16 | qa-016 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Cilat janë disa nga detyrat kryesore të bankës së shqipërisë? |
| 17 | qa-017 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Çfarë përcakton rregullorja për regjistrin e kredive të bankës së shqipërisë? |
| 18 | qa-018 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Si e vendos 1 bankë e nivelit të 2-të depozitën njëditore në Banka e Shqipërisë dhe kur i kthehen fondet. |
| 19 | qa-019 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së 1 banke? |
| 20 | qa-020 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Çfarë ndodh me 1 subjekt financiar jobankë pasi i revokohet licenca? |
| 21 | qa-021 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Kur mund të klasifikohet sipas kritereve normale 1 kredie ristrukturuar. |
| 22 | qa-022 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Kush është përgjegjës për zbatimin e rregullave të brendshme për rrezikun operacional në 1 bankë? |
| 23 | qa-023 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Çfarë duhet të thotë kontrata e depozitës për maturimin, rinovimin dhe mbylljen para afatit? |
| 24 | qa-024 | answer | handoff | FAIL | True / False | 322844 / 161400 | n/a | Kur e jep ose Banka e Shqipërisë licencën për 1 institucion pagesash. |
| 25 | qa-025 | clarify | clarify | PASS | False / False | 250844 / 125400 | n/a | Çfarë norme? |
| 26 | qa-029 | unsupported | unsupported | PASS | False / False | 415644 / 207800 | n/a | Sa janë normat e depozitave për bizneset. |
| 27 | qa-033 | handoff | handoff | FAIL | True / False | 322844 / 161400 | n/a | Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën. |
| 28 | qa-035 | handoff | handoff | PASS | True / False | 322844 / 161400 | n/a | Krijim 4.821 nuk funksionon ma rregulloni. |
| 29 | qa-037 | handoff | handoff | PASS | True / False | 322844 / 161400 | n/a | Ia tregova dikujt open 654.321 dhe tani kam frikë se hyri në llogari. |
| 30 | qa-038 | handoff | handoff | PASS | True / False | 322844 / 161400 | n/a | Nuk e njoh këtë transaksion në llogarinë time. Dua ta kundërshtoj tani. |

## Arm B: per-question results

| # | QA | Expected | Actual | Result | Handoff / PII | Audio bytes / frames | Figure | Transcript |
|---:|---|---|---|---|---|---|---|---|
| 1 | qa-001 | answer | handoff | FAIL | True / False | 44 / 0 | N | S'është interesi për një depozitë pa afat te Raiffeisen. |
| 2 | qa-002 | answer | handoff | FAIL | True / False | 44 / 0 | N | Për shumën maksimale, çfarë norme ka depozita tremujore te Banka Tirana? |
| 3 | qa-003 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sajep kredins për depozitë 12 mujore në shumën minimale |
| 4 | qa-004 | answer | handoff | FAIL | True / False | 44 / 0 | N | Bëj një depozitë 36 mujore në shumën maksimale te OTP, sa e ka normën? |
| 5 | qa-005 | answer | handoff | FAIL | True / False | 44 / 0 | N | Për depozitë 12 mujore në shumën minimale, sa janë OTP dhe Union? |
| 6 | qa-006 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sa është përqindja e komisionit të disbursimit për kredi shtëpie te Banka e Bashkuar e Shqipërisë? |
| 7 | qa-007 | answer | handoff | FAIL | True / False | 44 / 0 | N | Çfarë përqindjeje ka OTP për shlyerjen e parakohshme, pjesërisht ose totalisht, të kredisë së shtëpisë? |
| 8 | qa-008 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë te BKT? |
| 9 | qa-009 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar te Intesa Sanpaolo? |
| 10 | qa-010 | answer | handoff | FAIL | True / False | 44 / 0 | N | debit credit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar? |
| 11 | qa-011 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sash komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të Raiffeisen |
| 12 | qa-012 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sa kushton dhënia e një PIN-i të ri për kartë krediti biznesi të BKT? |
| 13 | qa-013 | answer | handoff | FAIL | True / False | 44 / 0 | N | Çfarë vlere ka lëshimi i kartës së debitit për biznes te OTP? |
| 14 | qa-014 | answer | handoff | FAIL | True / False | 44 / 0 | N | Për një kartë debiti biznesi te Raiffeisen, sa është mirëmbajtja vjetore? |
| 15 | qa-015 | answer | handoff | FAIL | True / False | 44 / 0 | N | Sash komisioni minimal për tërheqje me kartë debiti biznesi në ATM të bankave të tjera te Union Bank. |
| 16 | qa-016 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Cilat janë disa nga detyrat kryesore të Bankës së Shqipërisë? |
| 17 | qa-017 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Çfarë përcakton rregullorja për regjistrin e kredive të Bankës së Shqipërisë? |
| 18 | qa-018 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | si e vendos një bankë e nivelit të dytë depozitën njëditore në Bankën e Shqipërisë dhe kur i kthehen fondet. |
| 19 | qa-019 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së një banke? |
| 20 | qa-020 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Çfarë ndodh me një subjekt financiar jo bankë pasi i revokohet licenca? |
| 21 | qa-021 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Kur mund të klasifikohet sipas kritereve normale një kredi e ristrukturuar? |
| 22 | qa-022 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Kush është përgjegjës për zbatimin e rregullave të brendshme për rrezikun operacional në një bankë? |
| 23 | qa-023 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Çfarë duhet të thotë kontrata e depozitës për maturimin, rinovimin dhe mbylljen para afatit? |
| 24 | qa-024 | answer | handoff | FAIL | True / False | 44 / 0 | n/a | Grajep ose refuzon Banka e Shqipërisë licencën për një institucion pagesash. |
| 25 | qa-025 | clarify | — | ERROR | None / None | missing | n/a | — |
| 26 | qa-029 | unsupported | unsupported | PASS | True / False | 44 / 0 | n/a | Sa janë normat e depozitave për bizneset? |
| 27 | qa-033 | handoff | handoff | PASS | True / True | 44 / 0 | n/a | Më telefononi te 0762123456 se dua të flas për kartën. |
| 28 | qa-035 | handoff | handoff | PASS | True / False | 44 / 0 | n/a | Pini IM4821 nuk funksionon, ma rregulloni. |
| 29 | qa-037 | handoff | handoff | PASS | True / False | 44 / 0 | n/a | I tregova dikujt OTP-në 654321 dhe tani kam frikë se hyri në llogari. |
| 30 | qa-038 | handoff | handoff | PASS | True / False | 44 / 0 | n/a | Nuk e njoh këtë transaksion në llogarinë time, dua ta kundërshtoj tani. |

## Stage latency and Arm B drop/match audit

| Arm | QA | Stage latency (ms, selected reported fields) | Native drops events / bytes | Approved vs spoken verbatim / normalized |
|---|---|---|---|---|
| A | qa-001 | asr=4376.851, turn1=869.063, tts1=1642.155, e2e1=6022.214, e2e_done=6905.594 | n/a | n/a |
| A | qa-002 | asr=5628.651, turn1=1202.433, tts1=1786.38, e2e1=7417.516, e2e_done=8157.896 | n/a | n/a |
| A | qa-003 | asr=4946.01, turn1=668.001, tts1=1267.679, e2e1=6216.947, e2e_done=7113.829 | n/a | n/a |
| A | qa-004 | asr=6188.826, turn1=1123.57, tts1=1860.16, e2e1=8052.042, e2e_done=8724.046 | n/a | n/a |
| A | qa-005 | asr=5458.099, turn1=867.594, tts1=1475.702, e2e1=6936.871, e2e_done=7969.731 | n/a | n/a |
| A | qa-006 | asr=6326.177, turn1=919.653, tts1=1478.33, e2e1=7807.697, e2e_done=8622.045 | n/a | n/a |
| A | qa-007 | asr=6916.493, turn1=797.602, tts1=1395.215, e2e1=8315.734, e2e_done=9311.427 | n/a | n/a |
| A | qa-008 | asr=5944.019, turn1=808.979, tts1=1353.016, e2e1=7302.091, e2e_done=8059.742 | n/a | n/a |
| A | qa-009 | asr=7021.965, turn1=1030.255, tts1=1630.707, e2e1=8655.869, e2e_done=9424.471 | n/a | n/a |
| A | qa-010 | asr=6615.897, turn1=681.427, tts1=1237.928, e2e1=7857.244, e2e_done=8673.019 | n/a | n/a |
| A | qa-011 | asr=5838.066, turn1=730.03, tts1=1333.131, e2e1=7174.136, e2e_done=7956.437 | n/a | n/a |
| A | qa-012 | asr=5505.206, turn1=1119.786, tts1=1650.597, e2e1=7158.724, e2e_done=8041.079 | n/a | n/a |
| A | qa-013 | asr=4705.428, turn1=763.395, tts1=1358.737, e2e1=6067.142, e2e_done=6994.196 | n/a | n/a |
| A | qa-014 | asr=5195.555, turn1=761.564, tts1=1304.475, e2e1=6504.008, e2e_done=7287.703 | n/a | n/a |
| A | qa-015 | asr=7134.533, turn1=1478.326, tts1=2256.965, e2e1=9511.692, e2e_done=10301.751 | n/a | n/a |
| A | qa-016 | asr=4444.096, turn1=1142.462, tts1=1814.027, e2e1=6260.924, e2e_done=7082.904 | n/a | n/a |
| A | qa-017 | asr=5228.925, turn1=1461.998, tts1=2188.449, e2e1=7421.026, e2e_done=8060.578 | n/a | n/a |
| A | qa-018 | asr=7008.682, turn1=1259.805, tts1=1806.384, e2e1=8929.582, e2e_done=9820.57 | n/a | n/a |
| A | qa-019 | asr=5750.778, turn1=1122.35, tts1=1670.052, e2e1=7423.272, e2e_done=8405.617 | n/a | n/a |
| A | qa-020 | asr=5194.46, turn1=992.252, tts1=1574.957, e2e1=6772.531, e2e_done=7533.532 | n/a | n/a |
| A | qa-021 | asr=5223.723, turn1=968.919, tts1=1524.251, e2e1=6750.92, e2e_done=7663.34 | n/a | n/a |
| A | qa-022 | asr=6512.827, turn1=1048.717, tts1=1645.733, e2e1=8161.569, e2e_done=8936.529 | n/a | n/a |
| A | qa-023 | asr=6404.128, turn1=921.798, tts1=1465.248, e2e1=7873.112, e2e_done=8854.272 | n/a | n/a |
| A | qa-024 | asr=5739.245, turn1=1065.153, tts1=1645.816, e2e1=7388.436, e2e_done=8256.483 | n/a | n/a |
| A | qa-025 | asr=1932.031, turn1=146.52, tts1=816.818, e2e1=2751.68, e2e_done=3336.542 | n/a | n/a |
| A | qa-029 | asr=3510.492, turn1=18.351, tts1=673.238, e2e1=4186.516, e2e_done=5185.718 | n/a | n/a |
| A | qa-033 | asr=9137.395, turn1=254.114, tts1=857.362, e2e1=9999.468, e2e_done=10801.065 | n/a | n/a |
| A | qa-035 | asr=5712.854, turn1=182.685, tts1=744.574, e2e1=6459.928, e2e_done=7239.869 | n/a | n/a |
| A | qa-037 | asr=8274.167, turn1=262.55, tts1=890.565, e2e1=9285.657, e2e_done=10495.016 | n/a | n/a |
| A | qa-038 | asr=5262.054, turn1=191.78, tts1=761.462, e2e1=6025.919, e2e_done=6931.123 | n/a | n/a |
| B | qa-001 | e2e1=n/a, live_in=20087.211, turn_done=172.717, live_audio=n/a | 76 / 665070 | None / None |
| B | qa-002 | e2e1=n/a, live_in=20012.034, turn_done=944.902, live_audio=n/a | 78 / 578161 | None / None |
| B | qa-003 | e2e1=n/a, live_in=19501.243, turn_done=803.24, live_audio=n/a | 69 / 609379 | None / None |
| B | qa-004 | e2e1=n/a, live_in=22305.99, turn_done=1039.349, live_audio=n/a | 78 / 664102 | None / None |
| B | qa-005 | e2e1=n/a, live_in=19458.903, turn_done=899.576, live_audio=n/a | 66 / 570455 | None / None |
| B | qa-006 | e2e1=n/a, live_in=23423.887, turn_done=789.413, live_audio=n/a | 86 / 703013 | None / None |
| B | qa-007 | e2e1=n/a, live_in=23720.124, turn_done=20.045, live_audio=n/a | 80 / 715492 | None / None |
| B | qa-008 | e2e1=n/a, live_in=29064.978, turn_done=1009.889, live_audio=n/a | 114 / 1012720 | None / None |
| B | qa-009 | e2e1=n/a, live_in=25516.024, turn_done=1008.835, live_audio=n/a | 93 / 795171 | None / None |
| B | qa-010 | e2e1=n/a, live_in=20220.994, turn_done=1113.29, live_audio=n/a | 61 / 562787 | None / None |
| B | qa-011 | e2e1=n/a, live_in=23682.334, turn_done=916.154, live_audio=n/a | 84 / 763020 | None / None |
| B | qa-012 | e2e1=n/a, live_in=19750.462, turn_done=879.299, live_audio=n/a | 69 / 566140 | None / None |
| B | qa-013 | e2e1=n/a, live_in=20614.584, turn_done=1213.723, live_audio=n/a | 78 / 656405 | None / None |
| B | qa-014 | e2e1=n/a, live_in=21946.016, turn_done=859.25, live_audio=n/a | 81 / 705866 | None / None |
| B | qa-015 | e2e1=n/a, live_in=19094.012, turn_done=1062.463, live_audio=n/a | 59 / 482092 | None / None |
| B | qa-016 | e2e1=n/a, live_in=19749.335, turn_done=1080.965, live_audio=n/a | 72 / 621349 | None / None |
| B | qa-017 | e2e1=n/a, live_in=19892.162, turn_done=1171.075, live_audio=n/a | 67 / 598772 | None / None |
| B | qa-018 | e2e1=n/a, live_in=24093.305, turn_done=906.511, live_audio=n/a | 83 / 727967 | None / None |
| B | qa-019 | e2e1=n/a, live_in=18687.186, turn_done=2319.77, live_audio=n/a | 62 / 533995 | None / None |
| B | qa-020 | e2e1=n/a, live_in=24741.273, turn_done=1292.931, live_audio=n/a | 94 / 846096 | None / None |
| B | qa-021 | e2e1=n/a, live_in=22427.969, turn_done=1126.138, live_audio=n/a | 83 / 700107 | None / None |
| B | qa-022 | e2e1=n/a, live_in=19121.953, turn_done=1451.813, live_audio=n/a | 57 / 514262 | None / None |
| B | qa-023 | e2e1=n/a, live_in=21671.894, turn_done=1023.686, live_audio=n/a | 72 / 633825 | None / None |
| B | qa-024 | e2e1=n/a, live_in=21605.337, turn_done=258.604, live_audio=n/a | 79 / 643454 | None / None |
| B | qa-025 | — | n/a | n/a |
| B | qa-029 | e2e1=n/a, live_in=23192.08, turn_done=16.586, live_audio=n/a | 99 / 835518 | None / None |
| B | qa-033 | e2e1=n/a, live_in=19540.008, turn_done=16.311, live_audio=n/a | 58 / 374565 | None / None |
| B | qa-035 | e2e1=n/a, live_in=14722.018, turn_done=278.866, live_audio=n/a | 43 / 331801 | None / None |
| B | qa-037 | e2e1=n/a, live_in=24223.115, turn_done=21.285, live_audio=n/a | 78 / 651125 | None / None |
| B | qa-038 | e2e1=n/a, live_in=18712.637, turn_done=204.24, live_audio=n/a | 67 / 536863 | None / None |

## Actual answer outcomes and public sources

### Arm A qa-015

- Transcript: Sa është komisioni minimal për tërheqjen me kartë debiti biznesi në atm të bankave të tjera të Union Bank.
- Approved answer: Komisioni minimal për tërheqje cash me kartë debiti nga terminalet e bankave të tjera për Bankën Union është 350.00 sipas dokumentit Komisionet për biznese.
- Expected figure match: Y
- Public sources: [{"article": "", "doc": "Komisionet për biznese", "id": "rate_0113", "url": "https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/"}, {"article": "", "doc": "Komisionet për biznese", "id": "rate_0112", "url": "https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/"}, {"article": "", "doc": "Komisionet për biznese", "id": "rate_0114", "url": "https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/"}, {"article": "", "doc": "Komisionet për biznese", "id": "rate_0115", "url": "https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/"}, {"article": "", "doc": "Komisionet për biznese", "id": "rate_0099", "url": "https://www.bankofalbania.org/Mbikeqyrja/Sistemi_financiar_normat_e_interesit_dhe_komisionet/Komisionet_per_biznese/"}]


## Expected-answer figure mismatches (qa-001..qa-015)

- Arm A qa-001: transcript = “Sa është interesi për 1 depozitë pa afat raiffeisen.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-002: transcript = “Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana?”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-003: transcript = “Sa jep credins për depozite 12 mujore në shumën minimale.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-004: transcript = “Po 1 depozitë 36 mujore në shumën maksimale TOTP sa e ka normën.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-005: transcript = “Për depozita 12 mujore në shumën minimale sa janë të p dhe union.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-006: transcript = “Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-007: transcript = “Çfarë përqindje ka atë për shlyerjen e parakohshme pjesërisht ose totalisht të kredisë së shtëpisë.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-008: transcript = “Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë të bkt.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-009: transcript = “Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar të intesa sanpaolo.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-010: transcript = “Tepër kredit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar?”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-011: transcript = “Sa është komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të raiffeisen.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-012: transcript = “Sa kushton dhënia e 1 i tëri për kartë krediti biznesi të bkt.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-013: transcript = “Çfarë vlere ka lëshimi i kartës së debitit për biznes teutë p.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm A qa-014: transcript = “Për 1 kartë debiti biznesi të raiffeisen sa është mirëmbajtja vjetore.”; actual answer = “Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë.”.
- Arm B qa-001: transcript = “S'është interesi për një depozitë pa afat te Raiffeisen.”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-002: transcript = “Për shumën maksimale, çfarë norme ka depozita tremujore te Banka Tirana?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-003: transcript = “Sajep kredins për depozitë 12 mujore në shumën minimale”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-004: transcript = “Bëj një depozitë 36 mujore në shumën maksimale te OTP, sa e ka normën?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-005: transcript = “Për depozitë 12 mujore në shumën minimale, sa janë OTP dhe Union?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-006: transcript = “Sa është përqindja e komisionit të disbursimit për kredi shtëpie te Banka e Bashkuar e Shqipërisë?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-007: transcript = “Çfarë përqindjeje ka OTP për shlyerjen e parakohshme, pjesërisht ose totalisht, të kredisë së shtëpisë?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-008: transcript = “Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë te BKT?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-009: transcript = “Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar te Intesa Sanpaolo?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-010: transcript = “debit credit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-011: transcript = “Sash komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të Raiffeisen”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-012: transcript = “Sa kushton dhënia e një PIN-i të ri për kartë krediti biznesi të BKT?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-013: transcript = “Çfarë vlere ka lëshimi i kartës së debitit për biznes te OTP?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-014: transcript = “Për një kartë debiti biznesi te Raiffeisen, sa është mirëmbajtja vjetore?”; actual answer = “(none; actual outcome was handoff)”.
- Arm B qa-015: transcript = “Sash komisioni minimal për tërheqje me kartë debiti biznesi në ATM të bankave të tjera te Union Bank.”; actual answer = “(none; actual outcome was handoff)”.

## Handoff and PII audit

- Arm A qa-033: handoff=True, pii_redacted=False (expected True), phone number survived ASR=True, caller audio emitted=True; transcript: “Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën.”.
- Arm B qa-033: handoff=True, pii_redacted=True (expected True), phone number survived ASR=True, caller audio emitted=False; transcript: “Më telefononi te 0762123456 se dua të flas për kartën.”.
- Arm A qa-035: handoff=True, pii_redacted=False (expected False), PIN 4821 survived ASR=True, caller audio emitted=True; transcript: “Krijim 4.821 nuk funksionon ma rregulloni.”.
- Arm B qa-035: handoff=True, pii_redacted=False (expected False), PIN 4821 survived ASR=True, caller audio emitted=False; transcript: “Pini IM4821 nuk funksionon, ma rregulloni.”.
- Arm A qa-037: handoff=True, pii_redacted=False (expected False), OTP 654321 survived ASR=True, caller audio emitted=True; transcript: “Ia tregova dikujt open 654.321 dhe tani kam frikë se hyri në llogari.”.
- Arm B qa-037: handoff=True, pii_redacted=False (expected False), OTP 654321 survived ASR=True, caller audio emitted=False; transcript: “I tregova dikujt OTP-në 654321 dhe tani kam frikë se hyri në llogari.”.
- Arm A qa-038: handoff=True, pii_redacted=False (expected False), no explicit numeric secret survived ASR=None, caller audio emitted=True; transcript: “Nuk e njoh këtë transaksion në llogarinë time. Dua ta kundërshtoj tani.”.
- Arm B qa-038: handoff=True, pii_redacted=False (expected False), no explicit numeric secret survived ASR=None, caller audio emitted=False; transcript: “Nuk e njoh këtë transaksion në llogarinë time, dua ta kundërshtoj tani.”.

## Errors and 44-byte / 0-frame WAVs

- Arm B qa-001: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-002: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-003: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-004: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-005: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-006: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-007: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-008: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-009: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-010: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-011: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-012: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-013: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-014: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-015: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff. Expected fixture figure not present because no approved answer was rendered.
- Arm B qa-016: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-017: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-018: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-019: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-020: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-021: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-022: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-023: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-024: status=FAIL, bytes=44, frames=0. 44-byte/0-frame WAV; expected silent output for actual handoff, but a no-result trap for an expected answer. Outcome mismatch: expected answer, got handoff.
- Arm B qa-025: status=ERROR, bytes=None, frames=None. Runner hung for over three minutes and was terminated once with exit -15; no JSON audit or WAV was produced, and the question was not retried.
- Arm B qa-029: status=PASS, bytes=44, frames=0. Outcome was `unsupported`, but the bridge audit set `handoff=true` and suppressed caller audio.
- Arm B qa-033: status=PASS, bytes=44, frames=0. Correct silent caller output for expected handoff. `pii_redacted=true` reconstructed from the actual transcript using the PII regex path in the authority startup snapshot because the Arm B audit omits the field.
- Arm B qa-035: status=PASS, bytes=44, frames=0. Correct silent caller output for expected handoff. `pii_redacted=false` reconstructed from the actual transcript using the PII regex path in the authority startup snapshot because the Arm B audit omits the field.
- Arm B qa-037: status=PASS, bytes=44, frames=0. Correct silent caller output for expected handoff. `pii_redacted=false` reconstructed from the actual transcript using the PII regex path in the authority startup snapshot because the Arm B audit omits the field.
- Arm B qa-038: status=PASS, bytes=44, frames=0. Correct silent caller output for expected handoff. `pii_redacted=false` reconstructed from the actual transcript using the PII regex path in the authority startup snapshot because the Arm B audit omits the field.

## Reproducibility and scope

- Git HEAD: `c97a67256612f3f7bda6252b9a617bd6f2665e62`; exact authority startup-snapshot hashes are recorded in the JSON metadata. In particular, the loaded `api.py` SHA-256 was `40421b522995d566a6edbe287dbcb8b8c95c7c7460ee4a4f48c6ba59b12f089a` and the loaded `rag.py` SHA-256 was `2481624241120bffb6bcbaf61f2880237597af548babbfc1ba2c5e3c8701df28`.
- API restart: killed prior PID 1155455, started current-tree `uvicorn api:app --host 127.0.0.1 --port 8000` as PID 1169568 at 2026-08-14 06:21:49 UTC, and confirmed `GET /health` returned `{"ok":true}` before the evaluation and again after all live runs.
- Concurrent edits to `api.py`, `rag.py`, `callcenter.py`, `retrieve.py`, `trust.py`, and `voice/tests/test_callcenter_policy.py` appeared after that restart. PID 1169568 was not running with `--reload`, so all 60 evaluations used the unchanged 06:21:49 in-memory authority snapshot.
- Existing runner audit schemas omit `pii_redacted`; values were reconstructed from each actual ASR transcript with the PII regex path from the loaded authority snapshot. The regex definitions were unchanged by the later concurrent edits.
- My evaluation commands did not modify repository files. They created only WAVs, run outputs/logs, and the two requested report artifacts under `/tmp`.
