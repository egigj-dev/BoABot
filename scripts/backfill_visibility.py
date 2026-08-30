#!/usr/bin/env python3
"""v2 audience taxonomy: visibility x document_type (2026-08-30 user decision).

`doc_scope` (public/internal/supervisory) collapsed two independent concepts:
WHO may access a document and WHAT KIND of document it is. It is now
DEPRECATED but kept populated for back-compat. The retrieval contract is:

    retrievable = visibility IN ('public')     (restricted never reaches a caller)

Taxonomy (fail-closed; every corpus doc must be classified, no overlaps):

    public  + customer                 fee/rate/deposit tables (BoA published
                                       customer-facing figures)
    public  + supervisory_regulation   BoA regulations/directives (published
                                       acts: Rregullore / Udhezim / Vendim /
                                       RREG / rishikime per publikim)
    public  + published_instrument     other published BoA instruments:
                                       BSH statute, BSH-MoF treasury
                                       agreements + amendments, credit-registry
                                       guides/forms/sample reports, model-
                                       contract approvals
    restricted + internal              BSH staff / operational / procedural
                                       material (HR terms, internal regs,
                                       one-day deposit / repo / garanti ops)

Provenance rule used to decide `public` vs `restricted`: visibility comes from
the BoA site itself — every corpus doc carries a bankofalbania.org URL and the
v1 scope was a per-document judgment. Public = published on the site;
restricted = BSH-internal subject matter (staff, agreements already covered,
operational instruments). Document type comes from the document's own kind
(rate table / regulation / instrument / internal), never from query text.

Run WITHOUT --apply = dry-run (prints the full mapping and assertions).
Run WITH --apply to ALTER TABLE (idempotent) and UPDATE every row.
"""
from __future__ import annotations

import os
import sys

import psycopg

# Reuse the v1 provenance lists as the base (they are verified against the
# live corpus: 103 docs, no missing/extra). `scripts/` is on sys.path when this
# script runs from the repo, so a sibling module imports directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill_doc_scope as _bf  # noqa: E402  (path set above)

# -- document_type: customer (BoA customer-facing rate/fee/deposit tables) ----
CUSTOMER_DOCS = frozenset({
    "Komisionet për individë",
    "Komisionet për biznese",
    "Normat e interesit të depozitave",
    "Normat nominale dhe NEI për individë",
    "Normat nominale dhe NEI për bizneset",
    "Lista_e_sherbimeve_me_perfaqesuese_23385.pdf",
})

# -- document_type: supervisory_regulation  -----------------------------------
# The v1 `supervisory` bucket (52 docs, incl. the NSFR regulation) plus the
# regulation-family docs from the v1 `public` bucket.
PUBLIC_BUCKET_REGULATIONS = frozenset({
    "Nr_28_date_30_03_2005_Rreg_TRANS_BANK_NE_RRUGE_ELEKTRONIKE_ndrysh_16277.pdf",
    "Rregullore_Nr_29_2022_Per_autentifikimin_e_thelluar_te_klientit_26017.pdf",
    "Rregullore_Nr_48_2015_29703.pdf",
    "Rregullore_nr_51_date_3_7_2019_Per_trajtimin_jashtegjyqesor_nga_bankat_15068.pdf",
    "Rregullore_Nr_59_2008_Mbi_transparencen_per_produktet_bankare_e_financiare_30366.pdf",
    "Rregullore_Nr_59_2022_Per_krahasueshmerine_e_tarifave_27387.pdf",
    "Rregullorja_Per_kerkesat_minimale_te_publikimit_te_informacionit_nga_bankat_dhe_deget_e_bankave_te_huaja_3437_1_6128.pdf",
})
SUPERVISORY_REGULATION_DOCS = frozenset(_bf.SUPERVISORY_DOCS) | PUBLIC_BUCKET_REGULATIONS

# -- document_type: published_instrument --------------------------------------
# Credit-registry guides/forms/samples that were in the v1 public bucket, plus
# the BSH-MoF treasury agreements/statute/gov-debt instruments that v1 called
# `internal` but are published BoA instruments (NOT restricted subject matter).
PUBLISHED_INSTRUMENT_DOCS = frozenset({
    # v1 public: registry guides / forms / sample reports / draft directive
    "Doc_No_2_VARIANTI_I_MIRATUAR_ME_DATE_TE_NDRYSHUAR_1_Korri_Projektudhezimi_i_fiksit_Final_19109.pdf",
    "Dokumentacioni_shoqerues_18228.pdf",
    "_fare_eshte_Raporti_mbi_Kredimarresin_6153_1_7213.pdf",
    "Formulari_i_kerkeses_per_raportin_e_kredimarresit_18226.pdf",
    "Formulari_per_rishikimin_e_te_dhenave_18227.pdf",
    "Informacioni_per_leximin_e_Raportit_per_Kredimarresin_18225.pdf",
    "pyetje_9041.pdf",
    "Regjistri_i_Kredive_3289_1_6834.pdf",
    "Regjistri_i_kredive_6152_1_7212.pdf",
    "Shembull_Raport_Kredimarresi_18224.pdf",
    # v1 internal -> published instruments (BSH-MoF treasury agreements,
    # statute, gov-debt directive, model-contract approval)
    "Marreveshja_BSH_MF_per_emetimin_e_titujve_te_shtetit_shqiptar_6197.pdf",
    "Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_Bonove_te_Thesarit_2501_1_6086.pdf",
    "Marreveshje_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_per_emetimin_e_obligacioneve_3273_1_6111.pdf",
    "Marreveshjet_e_Bankes_se_Shqiperise_me_Qeverine_e_Republikes_se_Shqiperise_2498_1_6085.pdf",
    "Ndryshim_ne_marreveshjen_mbi_emetimin_e_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_3366_1_6116.pdf",
    "Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_Bonove_te_Thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5993_1_6162.pdf",
    "Ndryshim_ne_Marreveshjen_Mbi_emetimin_e_obligacioneve_afatgjata_te_Qeverise_ne_forme_regjistrimi_5992_1_6161.pdf",
    "Ndryshim_ne_marreveshjen_mbi_emetimin_e_obligacioneve_afatgjata_te_qeverise_ne_forme_regjistrimi_3367_1_6117.pdf",
    "Ndryshim_ne_marreveshjen_per_emetimin_nga_Qeveria_e_Republikes_se_Shqiperise_te_bonove_te_thesarit_ne_forme_regjistrimi_dhe_mbajtjen_e_regjistrit_ne_dy_nivele_5616_1_6146.pdf",
    "Nr_80_2020_18196.pdf",
    "Per_nje_ndryshim_ne_marreveshjen_midis_Bankes_se_Shqiperise_dhe_Ministrise_se_Financave_3272_1_6110.pdf",
    "Statuti_i_Bankes_se_Shqiperise_7396_1_6217.pdf",
    "Udhezimi_per_obligacionet_MF_12325.pdf",
    "Vendim_Nr_15_date_10_03_2010_per_miratimin_e_Kontrates_tip_per_blerjen_e_titujve_2503_1_6087.pdf",
})

# -- restricted: BSH staff / operational / procedural material ----------------
INTERNAL_RESTRICTED_DOCS = frozenset(_bf.INTERNAL_DOCS) - PUBLISHED_INSTRUMENT_DOCS

GROUPS = {
    ("public", "customer"): CUSTOMER_DOCS,
    ("public", "supervisory_regulation"): SUPERVISORY_REGULATION_DOCS,
    ("public", "published_instrument"): PUBLISHED_INSTRUMENT_DOCS,
    ("restricted", "internal"): INTERNAL_RESTRICTED_DOCS,
}


def main() -> None:
    apply = "--apply" in sys.argv
    dsn = os.environ.get("BOABOT_DSN", "postgresql://boa:***@127.0.0.1:5433/boa")

    mapped = set().union(*GROUPS.values())
    if sum(map(len, GROUPS.values())) != len(mapped):
        raise RuntimeError("A document appears in more than one taxonomy group")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT doc FROM chunks")
            corpus = set(row[0] for row in cur.fetchall())
            if missing := sorted(corpus - mapped):
                raise RuntimeError(f"Unclassified corpus documents: {missing}")
            if extra := sorted(mapped - corpus):
                raise RuntimeError(f"Mapping documents absent from corpus: {extra}")

            if apply:
                cur.execute(
                    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS visibility text")
                cur.execute(
                    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS document_type text")
                for (visibility, document_type), docs in GROUPS.items():
                    cur.execute(
                        "UPDATE chunks SET visibility = %s, document_type = %s "
                        "WHERE doc = ANY(%s)",
                        (visibility, document_type, list(docs)),
                    )
                cur.execute(
                    "SELECT visibility, document_type, count(*) FROM chunks "
                    "GROUP BY visibility, document_type ORDER BY 1, 2")
                rows = cur.fetchall()
                cur.execute(
                    "SELECT count(*) FROM chunks WHERE visibility IS NULL "
                    "OR document_type IS NULL")
                nulls = cur.fetchone()[0]
            else:
                cur.execute(
                    "SELECT doc, doc_scope, count(*) FROM chunks "
                    "GROUP BY doc, doc_scope ORDER BY doc_scope, doc")
                rows = cur.fetchall()
                nulls = None
        conn.commit()

    if not apply:
        print(f"DRY-RUN — corpus {len(corpus)} docs; mapping {len(mapped)} docs; "
              f"run with --apply to write.\n")
        for doc, scope, n in rows:
            target = next(
                (f"{v}/{t}" for (v, t), docs in GROUPS.items() if doc in docs))
            print(f"  [{n:4d}] doc_scope={scope:12s} -> {target:38s} {doc}")
        print(f"\nTotals by target group:")
        doc_rows = {row[0]: row for row in rows}
        for (v, t), docs in GROUPS.items():
            n = sum(int(doc_rows[doc][2]) for doc in docs)
            print(f"  visibility={v:10s} document_type={t:22s} {n} chunks")
        return

    print(f"APPLIED — {len(rows)} groups, NULL cells: {nulls}")
    for visibility, document_type, n in rows:
        print(f"  visibility={visibility:10s} document_type={document_type:22s} "
              f"{n} chunks")
    if nulls:
        raise RuntimeError("NULL visibility/document_type cells must not exist")


if __name__ == "__main__":
    main()