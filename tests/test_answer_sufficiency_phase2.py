"""Requested-fact answer-sufficiency regressions."""
import core.answerability as answerability
import core.rag as rag


def _hit(text):
    return {
        "id": "reg_x", "doc": "Rregullore", "article": "16", "url": "u",
        "text": text, "dense_score": 0.9,
    }


def test_disclosure_rule_does_not_establish_fee_amount():
    evidence = _hit(
        "Rregullorja nr. 59 e vitit 2022 kërkon që tarifat e transfertave "
        "të publikohen sipas nenit 16."
    )
    assert answerability.lexical_verdict(
        "Sa është tarifa për transfertën?", [evidence],
    ) == (False, "abstain_price_without_value")


def test_same_disclosure_evidence_can_answer_regulatory_fact():
    evidence = _hit(
        "Banka duhet t'i publikojë tarifat e transfertave për klientët."
    )
    assert answerability.lexical_verdict(
        "A duhet banka ta publikojë tarifën e transfertës?", [evidence],
    ) == (True, "")


def test_requested_fact_categories_are_explicit():
    cases = {
        "Sa është tarifa për transfertën?": answerability.RequestedFact.FEE_AMOUNT,
        "Cilat janë tarifat për transfertat?": answerability.RequestedFact.FEE_AMOUNT,
        "Sa është norma e interesit?": answerability.RequestedFact.INTEREST_RATE,
        "Cila bankë ka normën më të ulët?":
            answerability.RequestedFact.COMPARISON_RANKING,
        "A ofron banka kredi për udhëtime?":
            answerability.RequestedFact.PRODUCT_AVAILABILITY,
        "A duhet banka ta publikojë tarifën?":
            answerability.RequestedFact.REGULATORY_RULE,
        "Çfarë është kapitali rregullator?":
            answerability.RequestedFact.DEFINITION,
        "Si bëhet një ankesë?": answerability.RequestedFact.PROCEDURE,
        "Cilat banka operojnë në Shqipëri?":
            answerability.RequestedFact.INSTITUTION_IDENTITY,
    }
    for question, expected in cases.items():
        assert answerability.requested_fact(question) is expected


def test_answerability_prompt_receives_requested_fact(monkeypatch):
    payloads = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(answerability, "_enabled", lambda: True)
    monkeypatch.setattr(rag, "_post", lambda payload: (
        payloads.append(payload)
        or {"choices": [{"message": {"content": "NO"}}]}
    ))

    verdict = answerability._answerability_verdict(
        "Sa është tarifa për transfertën?",
        [_hit("Tarifat duhet të publikohen.")],
    )

    assert verdict == "NO"
    user_prompt = payloads[0]["messages"][1]["content"]
    assert "fakti i kërkuar: FEE_AMOUNT" in user_prompt
    system_prompt = payloads[0]["messages"][0]["content"]
    assert "Lidhja tematike nuk mjafton" in system_prompt
    assert "nuk vendos shumën e tarifës" in system_prompt


def test_generator_contract_forbids_related_information_substitution():
    assert "Mos e zëvendëso informacionin e kërkuar" in rag.SYSTEM


def test_partial_support_instruction_names_supported_and_unknown():
    messages = rag.grounded_messages(
        "pyetje", [], [], support_level="PARTIALLY_SUPPORTED",
    )
    partial = messages[2]["content"]
    assert "çfarë mbështetet" in partial
    assert "çfarë mbetet e panjohur" in partial


def test_price_fact_outranks_offer_and_possession_verbs():
    assert answerability.requested_fact(
        "Sa është norma që ofron BKT?"
    ) is answerability.RequestedFact.INTEREST_RATE
    assert answerability.requested_fact(
        "Sa është tarifa që ka BKT?"
    ) is answerability.RequestedFact.FEE_AMOUNT
    assert answerability.requested_fact(
        "Cilat janë tarifat e publikuara nga BKT?"
    ) is answerability.RequestedFact.FEE_AMOUNT


def test_definition_outranks_fee_amount_shape():
    assert answerability.requested_fact(
        "Çfarë është tarifa e transferimit?"
    ) is answerability.RequestedFact.DEFINITION


def test_unrelated_amount_in_next_sentence_does_not_satisfy_fee_fact():
    evidence = _hit(
        "Tarifa duhet të publikohet. Shuma e transfertës është 500 euro."
    )
    assert answerability.lexical_verdict(
        "Sa është tarifa për transfertën?", [evidence],
    ) == (False, "abstain_price_without_value")


def test_decimal_fee_value_survives_sentence_splitting():
    evidence = _hit("Komisioni i transfertës është 2.00%. Kushtet vijojnë.")
    assert answerability.lexical_verdict(
        "Sa është komisioni i transfertës?", [evidence],
    ) == (True, "")
