# Temporal audit review: marker split and corrected conflict floor

> This is a read-only follow-up. Family grouping remains a normalization-based proposal, not ground truth.

## Headline decision

| Measure | Distinct base docs |
|---|---:|
| Total base docs | 88 |
| Title marked | 3 |
| Body only | 55 |

The earlier combined **58 of 88** statement obscured two materially different signals. Only **3 of 88** base filenames (3.41%) contain `ndryshuar|konsoliduar|integruar`; these are strong label-mismatch signals. Another **55** have body-only matches. Of those, **54** have at least one deterministic citation-cue suggestion and **1** have none. Body occurrences therefore are predominantly body noise/cross-reference evidence, not a sound basis for declaring the status heuristic broadly untrustworthy.

The cue classifier is deliberately only an auto-suggestion. `unclassified` means a human must inspect the bounded context; it does not prove self-description.

### Title-marked base documents

- `Doc_No_2_VARIANTI_I_MIRATUAR_ME_DATE_TE_NDRYSHUAR_1_Korri_Projektudhezimi_i_fiksit_Final_19109.pdf` — marker(s): ndryshuar
- `RREG_62_date_14_09_2011_P_R_ADM_E_RREZIKUT_T_KREDIS_e_ndryshuar_1_1_2022_doc_20177.pdf` — marker(s): ndryshuar
- `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` — marker(s): ndryshuar

### Body-only classification evidence

Windows show up to 1 distinct occurrences per document, with about 90 characters before and 30 after the bracketed marker. Documents and distinct occurrences are deterministically sorted by database `doc`, chunk `id`, and text position; when a document has a likely-citation suggestion, its first such occurrence is displayed, otherwise its first unclassified occurrence is displayed.

#### `Kushtet_e_vecanta_te_punes_se_Bankes_se_Shqiperise_1306_1_6032.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01756` / `ndryshuar` / `likely_citation` / cues: nr

  >  detyruar. 7.2 Llogaritë në valutë, që nuk përdoren për mbajtjen e rezervës së detyruar 2 [[Ndryshuar]] me vendimin nr.51, date 25.06

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Marreveshja_BSH_MF_per_emetimin_e_titujve_te_shtetit_shqiptar_6197.pdf`

Raw occurrences: 19; distinct: 19; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00334` / `ndryshuar` / `likely_citation` / cues: nr

  > krijuar me ligjin nr. 9572, datë 03.07.2006 “Për Autoritetin e Mbikëqyrjes Financiare”, i [[ndryshuar]]; f) “Bankat” - janë subjektet

- Evidence cap omitted 18 additional distinct occurrence(s).

#### `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_Bonove_te_Thesarit_2501_1_6086.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00295` / `ndryshuar` / `likely_citation` / cues: nr

  > . Ligji për Bankën Nënkupton Ligjin Nr.8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]. Ligji për Letrat me Vlerë Në

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_obligacioneve_3273_1_6111.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02971` / `ndryshuar` / `likely_citation` / cues: nr

  > ” Ligji për Bankën Nënkupton Ligjin Nr.8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]. Ligji për Letrat me Vlerë Në

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Nr_02_2013_Rregullore_per_adm_rrezikut_te_SFJB_29319.pdf`

Raw occurrences: 51; distinct: 51; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03530` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) nenit 126 të ligjit nr. 9

- Evidence cap omitted 50 additional distinct occurrence(s).

#### `Nr_03_date_19_01_2011_RREGULLORJA_PER_ADMINISTRIMIN_E_RREZIKUT_OPERACIONAL_30265.pdf`

Raw occurrences: 42; distinct: 40; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01883` / `ndryshuar` / `likely_citation` / cues: ligjit, neni, nr

  > dhe neni 43, shkronja “c” të ligjit nr. 8269 datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) 3 nenit 126 të ligjit nr.

- Evidence cap omitted 39 additional distinct occurrence(s).

#### `Nr_104_dt_05_10_2016_RREG_Per_licencimin_e_SHKK_ve_dhe_Unioneve_te_tyre_25062.pdf`

Raw occurrences: 20; distinct: 20; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01171` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > të nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë” i [[ndryshuar]], si edhe të ligjit nr. 9917, 

- Evidence cap omitted 19 additional distinct occurrence(s).

#### `Nr_105_dt_05_10_2016_RREG_PER_ADM_E_RREZ_NE_VEP_E_SHKKve_UNIONEVE_TE_TYRE_16868.pdf`

Raw occurrences: 18; distinct: 18; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02630` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; c) nenit 44 dhe 45 të ligjit

- Evidence cap omitted 17 additional distinct occurrence(s).

#### `Nr_14_dat_11_03_2009_RREGULLORJA_PER_LICENCIMIN_E_VEP_SE_BANKAVE_DHE_DBH_NE_RSH_22806.pdf`

Raw occurrences: 62; distinct: 58; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00189` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > rorizmit”. - 3Nenit 21 të ligjit nr.55/2020, datë 30.4.2020 “Për shërbimet e pagesave”. 1 [[Ndryshuar]] me vendimin nr. 55, datë 01.1

- Evidence cap omitted 57 additional distinct occurrence(s).

#### `Nr_31_date_06_06_2007_Per_licencimin_e_zyrave_te_kembimit_valutor_31609.pdf`

Raw occurrences: 83; distinct: 81; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02246` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > he nenit 61, shkronja “b” të ligjit nr. 8269 datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) nenit 126 të ligjit nr. 9

- Evidence cap omitted 80 additional distinct occurrence(s).

#### `Nr_44_dat_10_06_2009_Rregullorja_per_Parandalimin_e_Pastrimit_te_Parave_dhe_FT_16278.pdf`

Raw occurrences: 43; distinct: 43; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02756` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43, shkronja “c” të ligjit nr.8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; të nenit 9 dhe nenit 126, pi

- Evidence cap omitted 42 additional distinct occurrence(s).

#### `Nr_45_date_10_06_2009_RREGULLORE_MBI_RAPORTIMET_NE_BANKEN_E_SHQIPERISE_30074.pdf`

Raw occurrences: 27; distinct: 26; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02579` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > t 70 pika 1 dhe nenit 71 të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]], 2. nenit 47 pika 4 dhe nenit

- Evidence cap omitted 25 additional distinct occurrence(s).

#### `Nr_69_date_18_12_2014_Rregullorja_per_Kapitalin_Rregullator_23012.pdf`

Raw occurrences: 10; distinct: 10; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03824` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) nenit 59, pika 2 dhe 8, t

- Evidence cap omitted 9 additional distinct occurrence(s).

#### `Nr_6_2020_16690_16698.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02293` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja ”c”, të ligjit nr. 8269, datë 23.12.1997, "Për Bankën e Shqipërisë", i [[ndryshuar]]; dhe b) nenit 16 të ligjit nr

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Nr_72_2020_Per_funksionimin_e_Regjistrit_te_Kredive_ne_BSH_amended_19781.pdf`

Raw occurrences: 1; distinct: 1; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00541` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  në nenin 4 të ligjit nr.9662, datë 18.12.2006 “Për bankat në Republikën e Shqipërisë”, i [[ndryshuar]], (ligji për bankat) si dhe në


#### `Nr_72_dt_06_12_2017_Rregullore_Per_planet_e_rimekembjes_se_bankave_9779.pdf`

Raw occurrences: 4; distinct: 4; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00116` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > he nenit 43, shkronja ”c” të ligjit nr.8269, datë 23.12.1997 "Për Bankën e Shqipërisë", i [[ndryshuar]]; b) nenit 7, pika 7, nenit 8,

- Evidence cap omitted 3 additional distinct occurrence(s).

#### `Nr_80_2020_18196.pdf`

Raw occurrences: 5; distinct: 5; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03472` / `ndryshuar` / `likely_citation` / cues: ligjit, rregullore

  > et në përputhje me kërkesat e akteve të mëposhtme: a) ligjit “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) rregullores së Këshillit 

- Evidence cap omitted 4 additional distinct occurrence(s).

#### `RREGULLORJA_67_2015_PER_SISTEMIN_E_KONTROLLIT_TE_BRENDSHEM_11250.pdf`

Raw occurrences: 6; distinct: 6; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02184` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  3 dhe 4, i ligjit nr. 9662, datë 18.12.2006, “Për bankat në Republikën e Shqipërisë’’, i [[ndryshuar]], i cili këtu e më poshtë në k

- Evidence cap omitted 5 additional distinct occurrence(s).

#### `Rreg_per_licencimin_ushtrimin_e_veprimtarise_revokimin_dhe_likuid_portofolit_te_SFJB_28127.pdf`

Raw occurrences: 168; distinct: 166; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02424` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > të nenit 43 shkronja “c”, të ligjit nr. 8269 datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; 1 Ndryshuar titulli i rregul

- Evidence cap omitted 165 additional distinct occurrence(s).

#### `Rregullore_Mbi_marredheniet_e_Bankes_se_Shqiperise_me_bashkepunetore_te_jashtem_5798_1_6151.pdf`

Raw occurrences: 1; distinct: 1; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00408` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  bazë dhe në zbatim të: a) Ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) Statutit të Bankës së Shq


#### `Rregullore_Mbi_transaksionet_e_shitblerjeve_me_te_drejta_te_plota_5786_1_6150.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00392` / `ndryshuar` / `likely_citation` / cues: nr

  > azë dhe në përputhje me: a) ligjin nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) vendimin nr. 64, datë 20.

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Rregullore_Nr_29_2022_Per_autentifikimin_e_thelluar_te_klientit_26017.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03767` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; dhe të nenit 90 dhe nenit 91

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Rregullore_Nr_29_Mbi_minimumin_e_rezerves_se_detyruar_te_mbajtur_ne_Banken_e_Shqiperise_nga_bankat_1349_1_6057.pdf`

Raw occurrences: 5; distinct: 5; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01146` / `ndryshuar` / `likely_citation` / cues: nr

  > azë dhe në përputhje me: a) ligjin nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) ligjin nr. 9662, datë 18.

- Evidence cap omitted 4 additional distinct occurrence(s).

#### `Rregullore_Nr_42_Per_licencimin_rregullimin_dhe_mbikeqyrjen_e_operatoreve_te_skemave_kombetare_te_pagesave_me_karte_6817_1_6193.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01804` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > ”, nenit 53, paragrafi 4 të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; dhe b) nenit 2, paragrafi 1,

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Rregullore_Nr_48_2015_29703.pdf`

Raw occurrences: 29; distinct: 29; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02517` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; 2 b) të nenit 53, pikat 4 dh

- Evidence cap omitted 28 additional distinct occurrence(s).

#### `Rregullore_Nr_48_2024_28622.pdf`

Raw occurrences: 7; distinct: 7; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01028` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > ” dhe nenit 70, pika 1, të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; të neneve 57, pika 2, 58 dhe

- Evidence cap omitted 6 additional distinct occurrence(s).

#### `Rregullore_Nr_48_date_31_07_2013_Per_RMK_31077.pdf`

Raw occurrences: 72; distinct: 70; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01285` / `ndryshuar` / `likely_citation` / cues: ligjit, neni, nr

  > e neni 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) nenit 58, shkronjat “a”, 

- Evidence cap omitted 69 additional distinct occurrence(s).

#### `Rregullore_Nr_51_2024_31241.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02363` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > he nenit 43, shkronja “c” të ligjit nr. 8269 datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) nenit 57, pika 4, nenit 5

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Rregullore_Nr_57_Per_administrimin_e_rrezikut_ne_veprimtarine_e_degeve_te_bankave_te_huaja_6759_1_6185.pdf`

Raw occurrences: 5; distinct: 5; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02865` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  të nenit 12, shkronja “a” të ligjit nr.8269, datë 23.12.1997 "Për Bankën e Shqipërisë" i [[ndryshuar]] (më poshtë ligji “Për Bankën”

- Evidence cap omitted 4 additional distinct occurrence(s).

#### `Rregullore_Nr_59_2008_Mbi_transparencen_per_produktet_bankare_e_financiare_30366.pdf`

Raw occurrences: 36; distinct: 36; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02140` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > të nenit 12, shkronja “a” të ligjit nr.8269, datë 23.12.1997 "Për Bankën e Shqipërisë", i [[ndryshuar]] (më poshtë ligji “Për Bankën”

- Evidence cap omitted 35 additional distinct occurrence(s).

#### `Rregullore_Nr_59_2022_Per_krahasueshmerine_e_tarifave_27387.pdf`

Raw occurrences: 14; distinct: 14; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03944` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”, i [[ndryshuar]]; të nenit 53, pika 4, nenit 5

- Evidence cap omitted 13 additional distinct occurrence(s).

#### `Rregullore_Per_Raportin_Neto_te_Financimit_te_Qendrueshem_22898.pdf`

Raw occurrences: 27; distinct: 25; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03629` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 ''Për Bankën e Shqipërisë'', i [[ndryshuar]]; si dhe b) të nenit 26, pika 

- Evidence cap omitted 24 additional distinct occurrence(s).

#### `Rregullore_Per_Veprimtarine_Valutore_5608_1_6144.pdf`

Raw occurrences: 18; distinct: 17; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01103` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > 3, shkronja “d” dhe “dh” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; 3 b) ligjit nr. 9662, datë 1

- Evidence cap omitted 16 additional distinct occurrence(s).

#### `Rregullore_Per_administrimin_e_rrezikut_nga_pozicionet_e_hapura_valutore_5812_1_6152.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01052` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43 shkronja “c” të ligjit nr.8269, datë 23.12.1997 ‘’Për Bankën e Shqipërisë’’, i [[ndryshuar]], si dhe të nenit 57 pikat 2, 

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Rregullore_Per_ekspertin_kontabel_te_autorizuar_te_bankave_dhe_degeve_te_bankave_te_huaja_1294_1_6029.pdf`

Raw occurrences: 3; distinct: 3; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01235` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > të nenit 43 shkronja “c”, të ligjit nr. 8269 datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]] (këtu e më poshtë në këtë rre

- Evidence cap omitted 2 additional distinct occurrence(s).

#### `Rregullore_Per_ushtrimin_e_veprimtarise_dhe_mbikeqyrjen_e_IP_23811.pdf`

Raw occurrences: 5; distinct: 5; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03596` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]] dhe të neneve 10, 11, 12, 18,

- Evidence cap omitted 4 additional distinct occurrence(s).

#### `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf`

Raw occurrences: 1; distinct: 1; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00087` / `ndryshuar` / `likely_citation` / cues: nr, rregullore, vendim

  > minuta përpara cut-off përfundimtar, të vendosur në rregulloren “Rregulla dhe procedura 1 [[Ndryshuar]] me vendim të KM nr.44 dt.30.0


#### `Rregullore_mbi_kredine_njeditore_1342_1_6055.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00533` / `ndryshuar` / `likely_citation` / cues: nr, vendim

  > ë riblerjeve, që përcaktohet me vendim të Këshillit Mbikëqyrës të Bankës së Shqipërisë. 1 [[Ndryshuar]] me vendim të KM nr 45, dt.30.

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Rregullore_nr_10_date_26_2_2014_per_Ekspozimet_e_me_dha_31081.pdf`

Raw occurrences: 34; distinct: 34; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02825` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43, shkronja ”c” të ligjit nr. 8269, datë 23.12.1997 "Për Bankën e Shqipërisë", i [[ndryshuar]]; dhe b) nenit 57, pika 2, të 

- Evidence cap omitted 33 additional distinct occurrence(s).

#### `Rregullore_nr_43_2024_29291.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03498` / `ndryshuar` / `likely_citation` / cues: ligjit, nr, rregullore

  > b) nenit 9 të ligjit nr. 9662, datë 18.12.2006 “Për bankat në Republikën e Shqipërisë”, i [[ndryshuar]], i cili në këtë rregullore do

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Rregullore_nr_51_date_3_7_2019_Per_trajtimin_jashtegjyqesor_nga_bankat_15068.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02676` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; b) neneve 57, pika 2, 58, sh

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Rregullore_nr_59_date_24_11_2021_Per_licencimin_e_IP_IPE_dhe_regjistrimin_e_ofruesve_20007.pdf`

Raw occurrences: 15; distinct: 14; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03174` / `ndryshuar` / `likely_citation` / cues: ligjit, neni, nr

  > nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë”', i [[ndryshuar]]; b) nenit 4, pika 46 dhe neni

- Evidence cap omitted 13 additional distinct occurrence(s).

#### `Rregullore_nr_71_2009_Per_administrimin_e_rrezikut_te_likuiditetit_10947.pdf`

Raw occurrences: 5; distinct: 5; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00489` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43 shkronja “c” të ligjit nr.8269, datë 23.12.1997 ''Për Bankën e Shqipërisë'', i [[ndryshuar]], si dhe të nenit 26 pika 1 sh

- Evidence cap omitted 4 additional distinct occurrence(s).

#### `Rregullore_per_Repo_dhe_Rev_Repo_01_06_2016_11860.pdf`

Raw occurrences: 23; distinct: 23; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01069` / `ndryshuar` / `likely_citation` / cues: nr

  > – Tip për marrëveshjet e riblerjes dhe të anasjellta të riblerjes së letrave me vlerë”; 1 [[Ndryshuar]] me VKM nr. 78, dt. 18.12.2014

- Evidence cap omitted 22 additional distinct occurrence(s).

#### `Rregullorja_Per_kerkesat_minimale_te_publikimit_te_informacionit_nga_bankat_dhe_deget_e_bankave_te_huaja_3437_1_6128.pdf`

Raw occurrences: 15; distinct: 15; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_01775` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > ronjës ”c”, të nenit 43, të ligjit nr. 8269, datë 23.12.1997 "Për Bankën e Shqipërisë", i [[ndryshuar]] (më poshtë ligji “Për Bankën”

- Evidence cap omitted 14 additional distinct occurrence(s).

#### `Statuti_i_Bankes_se_Shqiperise_7396_1_6217.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00442` / `ndryshuar` / `likely_citation` / cues: nr

  > blikës së Shqipërisë dhe me ligjin nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]], i cili referohet në vijim si

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Udhezim_Mbi_administrimin_e_rrezikut_te_normes_se_interesit_ne_librin_e_bankes_1335_1_6049.pdf`

Raw occurrences: 4; distinct: 4; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00046` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; si dhe të nenit 58, pika 1, 

- Evidence cap omitted 3 additional distinct occurrence(s).

#### `Udhezim_per_raportimin_e_incidenteve_madhore_Nr_10_2024_26494.pdf`

Raw occurrences: 19; distinct: 17; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03104` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > e nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë”, i [[ndryshuar]]; dhe të nenit 89 të ligjit nr

- Evidence cap omitted 16 additional distinct occurrence(s).

#### `Udhezimi_Mbi_procesin_e_vleresimit_te_brendshem_te_mjaftueshmerise_se_kapitalit_7835_1_6242.pdf`

Raw occurrences: 16; distinct: 16; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02880` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > të nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 “Për Bankën e Shqipërisë” i [[ndryshuar]], si dhe nenit 57, pika 1, 2, 

- Evidence cap omitted 15 additional distinct occurrence(s).

#### `Udhezimi_Nr_1_2022_20714.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02001` / `ndryshuar` / `likely_citation` / cues: ligjit, neni, nr

  > nenit 43, shkronja “c”, të ligjit nr. 8269, datë 23.12.1997, “Për Bankën e Shqipërisë’, i [[ndryshuar]]; të pikave 2, 3 dhe 4 të neni

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Udhezimi_Nr_2_2021_ILAAP_20137_12_20137.pdf`

Raw occurrences: 8; distinct: 8; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03027` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  >  nenit 43, shkronja “c”, të ligjit nr.8269, datë 23.12.1997, “Për Bankën e Shqipërisë’, i [[ndryshuar]]; të neneve 57, 58 dhe 66 të l

- Evidence cap omitted 7 additional distinct occurrence(s).

#### `Udhezimi_nr_60_2019_Per_stress_test_et_e_bankave_16677.pdf`

Raw occurrences: 4; distinct: 4; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_03330` / `ndryshuar` / `likely_citation` / cues: ligjit, nr

  > nenit 43, shkronja “c” të ligjit nr. 8269, datë 23.12.1997 ''Për Bankën e Shqipërisë'', i [[ndryshuar]]; dhe të nenit 58, pika 1, shk

- Evidence cap omitted 3 additional distinct occurrence(s).

#### `Udhezimi_per_obligacionet_MF_12325.pdf`

Raw occurrences: 2; distinct: 2; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_02230` / `ndryshuar` / `likely_citation` / cues: nr

  > lidhet në përputhje me Ligjin nr. 8438, Datë 28.12.1998, “Për tatimin mbi të ardhurat”, i [[ndryshuar]]. 1. Tatimi për të ardhurat e 

- Evidence cap omitted 1 additional distinct occurrence(s).

#### `Udhezuesi_Mbi_drejtimin_e_brendshem_dhe_efektiv_te_bankave_per_publikim_21094.pdf`

Raw occurrences: 10; distinct: 10; displayed: 1; document suggestion: `unclassified_only`.

- `reg_01917` / `konsoliduar` / `unclassified` / cues: none

  > e paraqet një administrim efektiv dhe të kujdesshëm të bankës, në nivel individual dhe të [[konsoliduar]]. Linjat e raportimit dhe shpë

- Evidence cap omitted 9 additional distinct occurrence(s).

#### `Urdher_nr_1883_dt_22_04_2015_per_njohjen_e_ECAIve_23172.pdf`

Raw occurrences: 1; distinct: 1; displayed: 1; document suggestion: `has_likely_citation`.

- `reg_00035` / `ndryshuar` / `likely_citation` / cues: nr

  > kës së Shqipërisë, miratuar me vendimin e Këshillit Mbikëqyrës nr.100, datë 19.12.2000, i [[ndryshuar]], me propozim të Departamentit


## Corrected conflict count and floor

Corrected conflict count: **5** (family-001, family-002, family-003, family-004, family-005).

Drop family-006: its canonical consolidated Rreg. nr. 63 is the current text and its 2020 member is superseded by that consolidated version, so the pair reflects correct pipeline behavior rather than an outage.

This is a **floor, not an estimate**: **82** base+canonical documents are singletons under the proposed normalization. A singleton is indistinguishable between a document that genuinely has no sibling and one whose sibling exists but the normalization missed it. Therefore the true conflict count is **>= 5**, not exactly 5.

The credit-registry slice (`family-007`) remains at **0 conflicts** because both members are `base`; neither is an amendment.

## Coverage: the dominant risk

| Status | Chunks | Distinct docs |
|---|---:|---:|
| amendment | 107 | 12 |
| base | 3804 | 88 |
| canonical | 189 | 2 |
| superseded | 68 | 1 |

The corpus has **107 amendment + 68 superseded chunks** (175 combined) against **3804 base chunks**. That is only **4.60%** as many non-live chunks as base chunks.

On the served side, **83 of 88** base documents have no amendment/superseded sibling in any proposed family. AMENDMENTS ARE LARGELY ABSENT FROM THE CORPUS. That coverage gap is the dominant limitation; no relabeling and no retrieval change fixes it.

## Recommendation (not implemented)

Threshold: treat title marking as non-trivial when at least 2 base documents and at least 2.0% of base documents are title-marked.

The title signal is non-trivial under this threshold. As a separate, approved change, add `status` to retrieve()'s SELECT and surface it in vetted sources so the answer can disclose which version it quotes. Do not infer that body-only marker matches are mislabeled documents.

No serving code was changed.
