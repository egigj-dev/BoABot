# Corpus temporal-validity audit

> **Grouping caveat:** Every document family in this report is a normalization-based proposal, not ground truth. Human review is required.

This is a read-only survey. Retrieval uses `retrieve.retrieve(query, k=5)` with its default statuses.

## Headline numbers

- Proposed multi-document families: **7**
- Live-vs-amended conflict families: **6**
- Eval queries with intra-family collisions: **3** of **80**
- Collision rate: **3.75%**

## 1. Status inventory

| Status | Chunks | Distinct docs |
|---|---:|---:|
| amendment | 107 | 12 |
| base | 3804 | 88 |
| canonical | 189 | 2 |
| superseded | 68 | 1 |

## 2. Proposed document families

Normalization rules:

- lowercase and Unicode NFKD diacritic stripping
- replace underscores/punctuation with spaces and remove a trailing .pdf suffix
- remove Albanian i/e ndryshuar, i/e konsoliduar, i/e integruar phrases and standalone version-marker equivalents
- remove labelled dates, four-digit years, decision/archive number tokens, and normalize whitespace
- remove generic regulation/agreement/amendment preambles to expose the subject
- join exact, whitespace-equivalent, subject-prefix, or informative-token-subset subjects
- fallback: join the same generic regulation number only when one subject is unusable and explicit years do not conflict

### family-001: depoziten njeditore

Grouping rule(s): whitespace-equivalent normalized subject.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf` | base | 7 | `depoziten nje ditore` |
| `Vendimi_Nr_52_Per_nje_ndryshim_ne_rregulloren_Per_depoziten_njeditore_7497_1_6222.pdf` | amendment | 2 | `depoziten njeditore` |

### family-002: emetimin e bonove te thesarit — **UNCERTAIN**

Grouping rule(s): exact normalized subject, informative-token subset, normalized subject prefix.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_Bonove_te_Thesarit_2501_1_6086.pdf` | base | 39 | `emetimin e bonove te thesarit` |
| `Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_Bonove_te_Thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5993_1_6162.pdf` | amendment | 4 | `emetimin e bonove te thesarit ne forme regjistrimi dhe mbajtjen e regjistrit ne dy nivele` |
| `Ndryshim_ne_marreveshjen_mbi_emetimin_e_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_3366_1_6116.pdf` | amendment | 7 | `emetimin e bonove te thesarit ne forme regjistrimi dhe mbajtjen e regjistrit ne dy nivele` |
| `Ndryshim_ne_marreveshjen_per_emetimin_nga_Qeveria_e_Republikes_se_Shqiperise_te_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5616_1_6146.pdf` | amendment | 6 | `emetimin nga qeveria e republikes se shqiperise te bonove te thesarit ne forme regjistrimi dhe mbajtjen e regjistrit ne dy nivele` |

### family-003: emetimin e obligacioneve

Grouping rule(s): exact normalized subject, normalized subject prefix.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_obligacioneve_3273_1_6111.pdf` | base | 39 | `emetimin e obligacioneve` |
| `Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_obligacioneve_afatgjata_te_Qeverise_ne_forme_regjistrimi_5992_1_6161.pdf` | amendment | 6 | `emetimin e obligacioneve afatgjata te qeverise ne forme regjistrimi` |
| `Ndryshim_ne_marreveshjen_mbi_emetimin_e_obligacioneve_afatgjata_te_qeverise_ne_forme_regjistrimi_3367_1_6117.pdf` | amendment | 4 | `emetimin e obligacioneve afatgjata te qeverise ne forme regjistrimi` |

### family-004: funksionimin e sistemit qendror te regjistrimit dhe shlyerjes se titujve afisar

Grouping rule(s): exact normalized subject.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` | base | 249 | `funksionimin e sistemit qendror te regjistrimit dhe shlyerjes se titujve afisar` |
| `Vendimi_Nr_126_Per_miratimin_e_disa_ndryshimeve_ne_rregulloren_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_7640_1_6235.pdf` | amendment | 7 | `funksionimin e sistemit qendror te regjistrimit dhe shlyerjes se titujve afisar` |

### family-005: kredine njeditore

Grouping rule(s): exact normalized subject.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Rregullore_mbi_kredine_njeditore_1342_1_6055.pdf` | base | 9 | `kredine njeditore` |
| `Vendimi_nr_53_Per_nje_ndryshim_ne_rregulloren_Per_kredine_njeditore_7498_1_6223.pdf` | amendment | 2 | `kredine njeditore` |

### family-006: raportin e leves financiare — **UNCERTAIN**

Grouping rule(s): regulation-number fallback with missing subject and no conflicting year.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Rreg_nr_63_e_rishikuar_versioni_i_integruar_per_publikim_final_21573.pdf` | canonical | 57 | `rreg nr e versioni per` |
| `Rregullore_nr_63_date_4_11_2020_Per_raportin_e_leves_financiare_20187.pdf` | superseded | 68 | `raportin e leves financiare` |

### family-007: regjistri i kredive

Grouping rule(s): exact normalized subject.

| Document | Status | Chunks | Member normalization |
|---|---|---:|---|
| `Regjistri_i_Kredive_3289_1_6834.pdf` | base | 4 | `regjistri i kredive` |
| `Regjistri_i_kredive_6152_1_7212.pdf` | base | 17 | `regjistri i kredive` |

## 3. Live-vs-amended conflicts

Conflict family count: **6**.

- `family-001` — depoziten njeditore (amendment, base)
- `family-002` — emetimin e bonove te thesarit (amendment, base)
- `family-003` — emetimin e obligacioneve (amendment, base)
- `family-004` — funksionimin e sistemit qendror te regjistrimit dhe shlyerjes se titujve afisar (amendment, base)
- `family-005` — kredine njeditore (amendment, base)
- `family-006` — raportin e leves financiare (canonical, superseded)

## 4. Version-marker coverage

Counts are distinct documents. `text:any` means at least one chunk for the document contains a marker.

| Status | Docs | Title:any | Title:ndryshuar | Title:konsoliduar | Title:integruar | Text:any | Text:ndryshuar | Text:konsoliduar | Text:integruar |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| amendment | 12 | 0 | 0 | 0 | 0 | 6 | 6 | 0 | 0 |
| base | 88 | 3 | 3 | 0 | 0 | 58 | 58 | 26 | 14 |
| canonical | 2 | 2 | 0 | 1 | 1 | 2 | 2 | 2 | 1 |
| superseded | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 0 |

Canonical docs with no consolidation marker anywhere: **0**.

- None

Base docs carrying at least one consolidation marker: **58**.

- `Doc_No_2_VARIANTI_I_MIRATUAR_ME_DATE_TE_NDRYSHUAR_1_Korri_Projektudhezimi_i_fiksit_Final_19109.pdf`
- `Kushtet_e_vecanta_te_punes_se_Bankes_se_Shqiperise_1306_1_6032.pdf`
- `Marreveshja_BSH_MF_per_emetimin_e_titujve_te_shtetit_shqiptar_6197.pdf`
- `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_Bonove_te_Thesarit_2501_1_6086.pdf`
- `Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_obligacioneve_3273_1_6111.pdf`
- `Nr_02_2013_Rregullore_per_adm_rrezikut_te_SFJB_29319.pdf`
- `Nr_03_date_19_01_2011_RREGULLORJA_PER_ADMINISTRIMIN_E_RREZIKUT_OPERACIONAL_30265.pdf`
- `Nr_104_dt_05_10_2016_RREG_Per_licencimin_e_SHKK_ve_dhe_Unioneve_te_tyre_25062.pdf`
- `Nr_105_dt_05_10_2016_RREG_PER_ADM_E_RREZ_NE_VEP_E_SHKKve_UNIONEVE_TE_TYRE_16868.pdf`
- `Nr_14_dat_11_03_2009_RREGULLORJA_PER_LICENCIMIN_E_VEP_SE_BANKAVE_DHE_DBH_NE_RSH_22806.pdf`
- `Nr_31_date_06_06_2007_Per_licencimin_e_zyrave_te_kembimit_valutor_31609.pdf`
- `Nr_44_dat_10_06_2009_Rregullorja_per_Parandalimin_e_Pastrimit_te_Parave_dhe_FT_16278.pdf`
- `Nr_45_date_10_06_2009_RREGULLORE_MBI_RAPORTIMET_NE_BANKEN_E_SHQIPERISE_30074.pdf`
- `Nr_69_date_18_12_2014_Rregullorja_per_Kapitalin_Rregullator_23012.pdf`
- `Nr_6_2020_16690_16698.pdf`
- `Nr_72_2020_Per_funksionimin_e_Regjistrit_te_Kredive_ne_BSH_amended_19781.pdf`
- `Nr_72_dt_06_12_2017_Rregullore_Per_planet_e_rimekembjes_se_bankave_9779.pdf`
- `Nr_80_2020_18196.pdf`
- `RREGULLORJA_67_2015_PER_SISTEMIN_E_KONTROLLIT_TE_BRENDSHEM_11250.pdf`
- `RREG_62_date_14_09_2011_P_R_ADM_E_RREZIKUT_T_KREDIS_e_ndryshuar_1_1_2022_doc_20177.pdf`
- `Rreg_per_licencimin_ushtrimin_e_veprimtarise_revokimin_dhe_likuid_portofolit_te_SFJB_28127.pdf`
- `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf`
- `Rregullore_Mbi_marredheniet_e_Bankes_se_Shqiperise_me_bashkepunetore_te_jashtem_5798_1_6151.pdf`
- `Rregullore_Mbi_transaksionet_e_shitblerjeve_me_te_drejta_te_plota_5786_1_6150.pdf`
- `Rregullore_Nr_29_2022_Per_autentifikimin_e_thelluar_te_klientit_26017.pdf`
- `Rregullore_Nr_29_Mbi_minimumin_e_rezerves_se_detyruar_te_mbajtur_ne_Banken_e_Shqiperise_nga_bankat_1349_1_6057.pdf`
- `Rregullore_Nr_42_Per_licencimin_rregullimin_dhe_mbikeqyrjen_e_operatoreve_te_skemave_kombetare_te_pagesave_me_karte_6817_1_6193.pdf`
- `Rregullore_Nr_48_2015_29703.pdf`
- `Rregullore_Nr_48_2024_28622.pdf`
- `Rregullore_Nr_48_date_31_07_2013_Per_RMK_31077.pdf`
- `Rregullore_Nr_51_2024_31241.pdf`
- `Rregullore_Nr_57_Per_administrimin_e_rrezikut_ne_veprimtarine_e_degeve_te_bankave_te_huaja_6759_1_6185.pdf`
- `Rregullore_Nr_59_2008_Mbi_transparencen_per_produktet_bankare_e_financiare_30366.pdf`
- `Rregullore_Nr_59_2022_Per_krahasueshmerine_e_tarifave_27387.pdf`
- `Rregullore_Per_Raportin_Neto_te_Financimit_te_Qendrueshem_22898.pdf`
- `Rregullore_Per_Veprimtarine_Valutore_5608_1_6144.pdf`
- `Rregullore_Per_administrimin_e_rrezikut_nga_pozicionet_e_hapura_valutore_5812_1_6152.pdf`
- `Rregullore_Per_ekspertin_kontabel_te_autorizuar_te_bankave_dhe_degeve_te_bankave_te_huaja_1294_1_6029.pdf`
- `Rregullore_Per_ushtrimin_e_veprimtarise_dhe_mbikeqyrjen_e_IP_23811.pdf`
- `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf`
- `Rregullore_mbi_kredine_njeditore_1342_1_6055.pdf`
- `Rregullore_nr_10_date_26_2_2014_per_Ekspozimet_e_me_dha_31081.pdf`
- `Rregullore_nr_43_2024_29291.pdf`
- `Rregullore_nr_51_date_3_7_2019_Per_trajtimin_jashtegjyqesor_nga_bankat_15068.pdf`
- `Rregullore_nr_59_date_24_11_2021_Per_licencimin_e_IP_IPE_dhe_regjistrimin_e_ofruesve_20007.pdf`
- `Rregullore_nr_71_2009_Per_administrimin_e_rrezikut_te_likuiditetit_10947.pdf`
- `Rregullore_per_Repo_dhe_Rev_Repo_01_06_2016_11860.pdf`
- `Rregullorja_Per_kerkesat_minimale_te_publikimit_te_informacionit_nga_bankat_dhe_deget_e_bankave_te_huaja_3437_1_6128.pdf`
- `Statuti_i_Bankes_se_Shqiperise_7396_1_6217.pdf`
- `Udhezim_Mbi_administrimin_e_rrezikut_te_normes_se_interesit_ne_librin_e_bankes_1335_1_6049.pdf`
- `Udhezim_per_raportimin_e_incidenteve_madhore_Nr_10_2024_26494.pdf`
- `Udhezimi_Mbi_procesin_e_vleresimit_te_brendshem_te_mjaftueshmerise_se_kapitalit_7835_1_6242.pdf`
- `Udhezimi_Nr_1_2022_20714.pdf`
- `Udhezimi_Nr_2_2021_ILAAP_20137_12_20137.pdf`
- `Udhezimi_nr_60_2019_Per_stress_test_et_e_bankave_16677.pdf`
- `Udhezimi_per_obligacionet_MF_12325.pdf`
- `Udhezuesi_Mbi_drejtimin_e_brendshem_dhe_efektiv_te_bankave_per_publikim_21094.pdf`
- `Urdher_nr_1883_dt_22_04_2015_per_njohjen_e_ECAIve_23172.pdf`

## 5. Retrieval ambiguity rate

Queries run: **80**. Queries with collisions: **3** (**3.75%**).

Eval schema inspection:

- `eval_handwritten.jsonl`: recognized `question`; 40 rows; common string fields: gold_id, gold_url, question.
- `eval_retrieval.jsonl`: recognized `question`; 40 rows; common string fields: gold_id, gold_url, question.

Ten worst collision examples:

1. **Kush e administron Regjistrin e Kredive dhe cfare lloj te dhenash permban?** (`eval_handwritten.jsonl:22`)

   - family-007 / regjistri i kredive — 5 hits
     - rank 1: `Regjistri_i_Kredive_3289_1_6834.pdf` / base / `reg_03469`
     - rank 2: `Regjistri_i_kredive_6152_1_7212.pdf` / base / `reg_00007`
     - rank 3: `Regjistri_i_Kredive_3289_1_6834.pdf` / base / `reg_03468`
     - rank 4: `Regjistri_i_kredive_6152_1_7212.pdf` / base / `reg_00009`
     - rank 5: `Regjistri_i_kredive_6152_1_7212.pdf` / base / `reg_00003`

2. **Si funksionon tregu sekondar i titujve ne sistemin AFISaR?** (`eval_handwritten.jsonl:31`)

   - family-004 / funksionimin e sistemit qendror te regjistrimit dhe shlyerjes se titujve afisar — 5 hits
     - rank 1: `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` / base / `reg_00663`
     - rank 2: `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` / base / `reg_00656`
     - rank 3: `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` / base / `reg_00664`
     - rank 4: `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` / base / `reg_00683`
     - rank 5: `Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf` / base / `reg_00616`

3. **Si behet depozita njeditore nga bankat e nivelit te dyte?** (`eval_handwritten.jsonl:23`)

   - family-001 / depoziten njeditore — 3 hits
     - rank 1: `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf` / base / `reg_00087`
     - rank 2: `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf` / base / `reg_00086`
     - rank 4: `Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf` / base / `reg_00085`

## 6. Credit-registry slice

Derivation: rank observed title tokens beginning with a generic credit concept stem by document-frequency of co-occurrence with observed registry-concept tokens, then by overall document frequency; derive the registry match stem as the longest common prefix of those observed companion tokens.

Selected observed credit token: `kredive`; selected companion stem: `regjistri`.

Matching proposed families: `family-007`.

Credit-registry conflict families: **0**.

## Uncertain proposed groupings

- `family-002` — emetimin e bonove te thesarit: informative-token subset.
- `family-006` — raportin e leves financiare: regulation-number fallback with missing subject and no conflicting year.

## Data observations that qualify the prompt framing

- 58 base-labelled documents carry a specified consolidation marker in their title or chunk text, so filename-derived base labels do not cleanly track marker evidence.
- The amendment/superseded status establishes an ingest label, not chronology by itself; conflict families identify risk candidates rather than proving that every live member is older.
