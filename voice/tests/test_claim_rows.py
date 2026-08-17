"""Claim labels in newline-delimited tables stay within their own data row."""

from decimal import Decimal

from voice.shared.fidelity_guard import Claim, FidelityGuard


TABLE = """Tarifat e paketës Aurora — shërbimi Delta — kufiri maksimal
Institucioni Ylli: 11.11 EUR
Institucioni Hëna: 22.22 EUR
Institucioni Lumi: 33.33 EUR
Institucioni Mali: 44.44 EUR"""
INSTITUTIONS = ("ylli", "hena", "lumi", "mali")


def _table_claims() -> tuple[Claim, ...]:
    return FidelityGuard().extract_claims(TABLE)


def test_four_row_table_yields_one_claim_with_its_own_institution_per_row() -> None:
    claims = _table_claims()
    assert len(claims) == 4
    assert all(name in claim.label for name, claim in zip(INSTITUTIONS, claims, strict=True))


def test_table_claim_labels_contain_no_digits_from_other_rows() -> None:
    assert all(not any(character.isdigit() for character in claim.label)
               for claim in _table_claims())


def test_table_claim_labels_exclude_every_other_institution() -> None:
    for own_name, claim in zip(INSTITUTIONS, _table_claims(), strict=True):
        assert all(name not in claim.label for name in INSTITUTIONS if name != own_name)


def test_each_table_claim_retains_header_and_product_context() -> None:
    assert all(
        {"aurora", "delta", "maksimal"} <= set(claim.label.split())
        for claim in _table_claims()
    )


def test_single_line_non_tabular_claim_is_unchanged() -> None:
    text = "Tarifa e paketës Boreal është 7.25 EUR."
    assert FidelityGuard().extract_claims(text) == (
        Claim(Decimal("7.25"), "eur", "tarifa e paketes boreal eshte"),
    )


def test_natural_inflections_match_credins_table_claim() -> None:
    evidence = """Komisionet për individë — Kredi konsumatore te pasiguruara — Komisione per shlyerje te parakoheshme MIN
Banka Credins: 0.50
Banka Procredit: 0.00"""
    sentence = (
        "Komisioni minimal për shlyerjen e parakohshme të kredisë konsumatore "
        "të pasiguruar në Bankën Credins është 0.50."
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved


def test_model_style_credins_claim_retains_product_context() -> None:
    evidence = """Komisionet për individë — Kredi konsumatore te pasiguruara — Komisione per shlyerje te parakoheshme MIN
Banka Credins: 0.50
Banka Procredit: 0.00"""
    sentence = (
        "Për kredinë konsumatore të pasiguruar, Banka Credins aplikon një "
        "komision minimal për shlyerje të parakohshme prej 0.50 sipas "
        "dokumentit Komisionet për individë."
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved


def test_model_style_claim_can_name_product_after_single_value() -> None:
    evidence = """Komisionet për individë — Kredi konsumatore te pasiguruara — Komisione per shlyerje te parakoheshme MIN
Banka Credins: 0.50
Banka Procredit: 0.00"""
    sentence = (
        "Sipas dokumentit Komisionet për individë, Banka Credins aplikon një "
        "komision minimal prej 0.50 për shlyerje të parakohshme të kredisë "
        "konsumatore të pasiguruara."
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved


def test_document_category_does_not_block_home_loan_claim() -> None:
    evidence = """Komisionet për individë — Kredi per shtepi — Komision per shlyerje te parakohshme te kredise (pjesore/totale) MIN
Banka Credins: 0.00
Banka Procredit: 0.00"""
    sentence = (
        "Komisioni minimal për shlyerje të parakohshme të kredisë për shtëpi "
        "në Bankën Credins është 0.00 sipas dokumentit Komisionet për individë."
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved


def test_same_credins_value_under_wrong_product_still_fails_closed() -> None:
    evidence = """Komisionet për individë — Kredi konsumatore te pasiguruara — Komisione per shlyerje te parakoheshme MIN
Banka Credins: 0.50
Banka Procredit: 0.00"""
    sentence = "Komisioni minimal për shtëpi në Bankën Credins është 0.50."
    assert not FidelityGuard().verify(sentence, (evidence,)).approved


def test_numbered_deposit_header_and_bank_row_are_verified_together() -> None:
    evidence = """Normat e interesit të depozitave — Depozita për individë — DEPOZITA ME AFAT 36 mujor(Ne shumen maksimale)
Banka Credins: 2.50
Banka OTP Albania: 2.60"""
    sentence = (
        "Për depozitën 36-mujore të individëve në shumën maksimale, "
        "Banka OTP Albania ka normën e regjistruar 2.60%."
    )

    assert FidelityGuard().verify(sentence, (evidence,)).approved
    assert not FidelityGuard().verify(
        sentence.replace("OTP Albania", "Credins"), (evidence,)
    ).approved

    generated = (
        "Sipas dokumentit Normat e interesit të depozitave, norma e interesit "
        "për depozitën me afat 36 mujor në shumën maksimale te "
        "Banka OTP Albania është 2.60."
    )
    assert FidelityGuard().verify(generated, (evidence,)).approved


def test_word_form_three_month_term_matches_numeric_table_header() -> None:
    evidence = """Normat e interesit — Depozita me afat 3 mujor (Ne shumen maksimale)
Banka Tirana: 0.70"""
    sentence = (
        "Banka Tirana ofron normën 0.70 për depozitën me afat "
        "tre-mujor në shumën maksimale."
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved
