#!/usr/bin/env python3
"""One-off, fail-closed audience classification for the chunks corpus."""
from __future__ import annotations

import os

import psycopg


DSN = os.environ.get("BOABOT_DSN", "postgresql://boa:boa@127.0.0.1:5433/boa")
VALID_SCOPES = frozenset({"public", "internal", "supervisory"})

PUBLIC_DOCS = frozenset({
    "Doc_No_2_VARIANTI_I_MIRATUAR_ME_DATE_TE_NDRYSHUAR_1_Korri_Projektudhezimi_i_fiksit_Final_19109.pdf",
    "Dokumentacioni_shoqerues_18228.pdf",
    "_fare_eshte_Raporti_mbi_Kredimarresin_6153_1_7213.pdf",
    "Formulari_i_kerkeses_per_raportin_e_kredimarresit_18226.pdf",
    "Formulari_per_rishikimin_e_te_dhenave_18227.pdf",
    "Informacioni_per_leximin_e_Raportit_per_Kredimarresin_18225.pdf",
    "Komisionet për biznese",
    "Komisionet për individë",
    "Lista_e_sherbimeve_me_perfaqesuese_23385.pdf",
    "Normat e interesit të depozitave",
    "Normat nominale dhe NEI për bizneset",
    "Normat nominale dhe NEI për individë",
    "Nr_28_date_30_03_2005_Rreg_TRANS_BANK_NE_RRUGE_ELEKTRONIKE_ndrysh_16277.pdf",
    "pyetje_9041.pdf",
    "Regjistri_i_Kredive_3289_1_6834.pdf",
    "Regjistri_i_kredive_6152_1_7212.pdf",
    "Rregullore_Nr_29_2022_Per_autentifikimin_e_thelluar_te_klientit_26017.pdf",
    "Rregullore_Nr_48_2015_29703.pdf",
    "Rregullore_nr_51_date_3_7_2019_Per_trajtimin_jashtegjyqesor_nga_bankat_15068.pdf",
    "Rregullore_Nr_59_2008_Mbi_transparencen_per_produktet_bankare_e_financiare_30366.pdf",
    "Rregullore_Nr_59_2022_Per_krahasueshmerine_e_tarifave_27387.pdf",
    "Rregullorja_Per_kerkesat_minimale_te_publikimit_te_informacionit_nga_bankat_dhe_deget_e_bankave_te_huaja_3437_1_6128.pdf",
    "Shembull_Raport_Kredimarresi_18224.pdf",
})

INTERNAL_DOCS = frozenset({
    "Kushtet_e_pergjithshme_te_punes_te_Bankes_se_Shqiperise_1305_1_6031.pdf",
    "Kushtet_e_vecanta_te_punes_se_Bankes_se_Shqiperise_1306_1_6032.pdf",
    "Marreveshja_BSH_MF_per_emetimin_e_titujve_te_shtetit_shqiptar_6197.pdf",
    "Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_Bonove_te_Thesarit_2501_1_6086.pdf",
    "Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_obligacioneve_3273_1_6111.pdf",
    "Marreveshjet_e_Bankes_se_Shqiperise_me_Qeverine_e_Republikes_se_Shqiperise_2498_1_6085.pdf",
    "Ndryshim_ne_marreveshjen_mbi_emetimin_e_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_3366_1_6116.pdf",
    "Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_Bonove_te_Thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5993_1_6162.pdf",
    "Ndryshim_ne_marreveshjen_mbi_emetimin_e_obligacioneve_afatgjata_te_qeverise_ne_forme_regjistrimi_3367_1_6117.pdf",
    "Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_obligacioneve_afatgjata_te_Qeverise_ne_forme_regjistrimi_5992_1_6161.pdf",
    "Ndryshim_ne_marreveshjen_per_emetimin_nga_Qeveria_e_Republikes_se_Shqiperise_te_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5616_1_6146.pdf",
    "Nr_80_2020_18196.pdf",
    "Per_nje_ndryshim_ne_marreveshjen_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_3272_1_6110.pdf",
    "Rregullat_Per_bashkepunimin_me_studente_te_cilet_vazhdojne_studimet_brenda_ose_jashte_Shqiperise_dhe_kane_arritur_rezultate_te_larta_ne_studime_1356_1_6061.pdf",
    "Rregullore_mbi_depoziten_nje_ditore_1340_1_6053.pdf",
    "Rregullore_mbi_garancite_ne_operacionet_kredituese_te_Bankes_se_Shqiperise_1694_1_6063.pdf",
    "Rregullore_mbi_kredine_njeditore_1342_1_6055.pdf",
    "Rregullore_Mbi_marredheniet_e_Bankes_se_Shqiperise_me_bashkepunetore_te_jashtem_5798_1_6151.pdf",
    "Rregullore_Mbi_transaksionet_e_shitblerjeve_me_te_drejta_te_plota_5786_1_6150.pdf",
    "Rregullore_Nr_21_Mbi_marredheniet_e_punes_te_personelit_te_Bankes_se_Shqiperise_2840_1_6095.pdf",
    "Rregullore_Nr_37_Mbi_rregullat_proceduriale_te_Bankes_se_Shqiperise_2913_1_6096.pdf",
    "Rregullore_Nr_99_Mbi_llogaritjen_e_normave_fikse_te_interesit_ne_tregun_nderbankar_te_parase_3162_1_6101.pdf",
    "Rregullore_per_Repo_dhe_Rev_Repo_01_06_2016_11860.pdf",
    "Statuti_i_Bankes_se_Shqiperise_7396_1_6217.pdf",
    "Udhezimi_per_obligacionet_MF_12325.pdf",
    "Vendimi_Nr_52_Per_nje_ndryshim_ne_rregulloren_Per_depoziten_njeditore_7497_1_6222.pdf",
    "Vendimi_nr_53_Per_nje_ndryshim_ne_rregulloren_Per_kredine_njeditore_7498_1_6223.pdf",
    "Vendim_Nr_15_date_10_03_2010_per_miratimin_e_Kontrates_tip_per_blerjen_e_titujve_2503_1_6087.pdf",
})

SUPERVISORY_DOCS = frozenset({
    "Manuali_i_veprimeve_korrigjuese_ndaj_bankave_dhe_degeve_te_bankave_te_huaja_ne_Republiken_e_Shqiperise_1317_1_6037.pdf",
    "Nr_02_2013_Rregullore_per_adm_rrezikut_te_SFJB_29319.pdf",
    "Nr_03_date_19_01_2011_RREGULLORJA_PER_ADMINISTRIMIN_E_RREZIKUT_OPERACIONAL_30265.pdf",
    "Nr_104_dt_05_10_2016_RREG_Per_licencimin_e_SHKK_ve_dhe_Unioneve_te_tyre_25062.pdf",
    "Nr_105_dt_05_10_2016_RREG_PER_ADM_E_RREZ_NE_VEP_E_SHKKve_UNIONEVE_TE_TYRE_16868.pdf",
    "Nr_14_dat_11_03_2009_RREGULLORJA_PER_LICENCIMIN_E_VEP_SE_BANKAVE_DHE_DBH_NE_RSH_22806.pdf",
    "Nr_31_date_06_06_2007_Per_licencimin_e_zyrave_te_kembimit_valutor_31609.pdf",
    "Nr_33_2024_27481.pdf",
    "Nr_44_dat_10_06_2009_Rregullorja_per_Parandalimin_e_Pastrimit_te_Parave_dhe_FT_16278.pdf",
    "Nr_45_date_10_06_2009_RREGULLORE_MBI_RAPORTIMET_NE_BANKEN_E_SHQIPERISE_30074.pdf",
    "Nr_4_date_01_02_2017_Rregullore_Per_Mbikeqyrjen_e_Konsoliduar_31083.pdf",
    "Nr_6_2020_16690_16698.pdf",
    "Nr_69_date_18_12_2014_Rregullorja_per_Kapitalin_Rregullator_23012.pdf",
    "Nr_72_2020_Per_funksionimin_e_Regjistrit_te_Kredive_ne_BSH_amended_19781.pdf",
    "Nr_72_dt_06_12_2017_Rregullore_Per_planet_e_rimekembjes_se_bankave_9779.pdf",
    "RREG_62_date_14_09_2011_P_R_ADM_E_RREZIKUT_T_KREDIS_e_ndryshuar_1_1_2022_doc_20177.pdf",
    "Rreg_nr_63_e_rishikuar_versioni_i_integruar_per_publikim_final_21573.pdf",
    "Rreg_per_licencimin_ushtrimin_e_veprimtarise_revokimin_dhe_likuid_portofolit_te_SFJB_28127.pdf",
    "Rregullore_76_2014_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_e_ndryshuar_6207.pdf",
    "Rregullore_nr_10_date_26_2_2014_per_Ekspozimet_e_me_dha_31081.pdf",
    "Rregullore_Nr_29_Mbi_minimumin_e_rezerves_se_detyruar_te_mbajtur_ne_Banken_e_Shqiperise_nga_bankat_1349_1_6057.pdf",
    "Rregullore_Nr_32_Per_perdorimin_e_teknologjise_se_informacionit_dhe_komunikimit_ne_subjektet_e_licencuara_nga_Banka_e_Shqiperise_3186_1_6108.pdf",
    "Rregullore_Nr_42_Per_licencimin_rregullimin_dhe_mbikeqyrjen_e_operatoreve_te_skemave_kombetare_te_pagesave_me_karte_6817_1_6193.pdf",
    "Rregullore_nr_43_2024_29291.pdf",
    "Rregullore_Nr_48_2024_28622.pdf",
    "Rregullore_Nr_48_date_31_07_2013_Per_RMK_31077.pdf",
    "Rregullore_Nr_51_2024_31241.pdf",
    "Rregullore_Nr_57_Per_administrimin_e_rrezikut_ne_veprimtarine_e_degeve_te_bankave_te_huaja_6759_1_6185.pdf",
    "Rregullore_nr_59_date_24_11_2021_Per_licencimin_e_IP_IPE_dhe_regjistrimin_e_ofruesve_20007.pdf",
    "Rregullore_nr_63_date_4_11_2020_Per_raportin_e_leves_financiare_20187.pdf",
    "Rregullore_nr_71_2009_Per_administrimin_e_rrezikut_te_likuiditetit_10947.pdf",
    "Rregullore_Per_administrimin_e_rrezikut_nga_pozicionet_e_hapura_valutore_5812_1_6152.pdf",
    "Rregullore_Per_ekspertin_kontabel_te_autorizuar_te_bankave_dhe_degeve_te_bankave_te_huaja_1294_1_6029.pdf",
    "Rregullore_Per_investimet_nga_bankat_ne_kapitalin_e_shoqerive_tregtare_1331_1_6044.pdf",
    "Rregullore_Per_kredine_per_mbeshtetje_me_likuiditet_6396_1_6176.pdf",
    "Rregullore_Per_Raportin_Neto_te_Financimit_te_Qendrueshem_22898.pdf",
    "Rregullore_Per_ushtrimin_e_veprimtarise_dhe_mbikeqyrjen_e_IP_23811.pdf",
    "Rregullore_Per_Veprimtarine_Valutore_5608_1_6144.pdf",
    "RREGULLORJA_67_2015_PER_SISTEMIN_E_KONTROLLIT_TE_BRENDSHEM_11250.pdf",
    "Udhezimi_Mbi_procesin_e_vleresimit_te_brendshem_te_mjaftueshmerise_se_kapitalit_7835_1_6242.pdf",
    "Udhezimi_Nr_1_2022_20714.pdf",
    "Udhezimi_Nr_2_2021_ILAAP_20137_12_20137.pdf",
    "Udhezimi_nr_60_2019_Per_stress_test_et_e_bankave_16677.pdf",
    "Udhezimi_Per_raportimin_e_veprimeve_te_kembimeve_valutore_NR_3_date_22_12_2021_20138.pdf",
    "Udhezim_Mbi_administrimin_e_rrezikut_te_normes_se_interesit_ne_librin_e_bankes_1335_1_6049.pdf",
    "Udhezim_Per_certifikatat_e_depozitave_1292_1_6027.pdf",
    "Udhezim_per_raportimin_e_incidenteve_madhore_Nr_10_2024_26494.pdf",
    "Udhezuesi_Mbi_drejtimin_e_brendshem_dhe_efektiv_te_bankave_per_publikim_21094.pdf",
    "Urdher_nr_1883_dt_22_04_2015_per_njohjen_e_ECAIve_23172.pdf",
    "Vendimi_Nr_126_Per_miratimin_e_disa_ndryshimeve_ne_rregulloren_Per_funksionimin_e_sistemit_qendror_te_regjistrimit_dhe_shlyerjes_se_titujve_AFISaR_7640_1_6235.pdf",
    "Vendim_nr_61_date_4_11_2020_17990.pdf",
    "Vendim_nr_62_date_4_11_2020_6179.pdf",
})


def main() -> None:
    groups = {
        "public": PUBLIC_DOCS,
        "internal": INTERNAL_DOCS,
        "supervisory": SUPERVISORY_DOCS,
    }
    assert set(groups) == VALID_SCOPES
    mapped = set().union(*groups.values())
    if sum(map(len, groups.values())) != len(mapped):
        raise RuntimeError("A document appears in more than one scope")

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT doc, count(*) FROM chunks GROUP BY doc ORDER BY doc")
            corpus = dict(cur.fetchall())
            if missing := sorted(set(corpus) - mapped):
                raise RuntimeError(f"Unclassified corpus documents: {missing}")
            if extra := sorted(mapped - set(corpus)):
                raise RuntimeError(f"Mapped documents absent from corpus: {extra}")
            for scope, docs in groups.items():
                cur.execute(
                    "UPDATE chunks SET doc_scope = %s WHERE doc = ANY(%s)",
                    (scope, list(docs)),
                )
            cur.execute(
                "SELECT doc, doc_scope, count(*) FROM chunks "
                "GROUP BY doc, doc_scope ORDER BY doc"
            )
            rows = cur.fetchall()
        conn.commit()

    if len(rows) != len(mapped):
        raise RuntimeError("A document has more than one stored scope")
    for doc, scope, count in rows:
        print(f"{doc}\t{scope}\t{count}")


if __name__ == "__main__":
    main()
