"""Safety precedence and privacy regressions for the call-center router."""

import re

import numpy as np
import pytest

import core.callcenter as callcenter
from core.callcenter import DecisionReason, Outcome, decide


def _positive_classifier(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_encode_question", lambda _text: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _embedding: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _embedding: 1.0)


def test_price_shaped_account_questions_cannot_bypass_classifier(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    for question in (
        "Sa është gjendja e llogarisë sime në bankë?",
        "Sa është limiti i kartës sime të kreditit?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_exported_classifier_routes_the_two_audited_account_probes() -> None:
    for question in (
        "Sa është gjendja e llogarisë sime në bankë?",
        "Sa është limiti i kartës sime të kreditit?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_offline_smalltalk_accepts_combined_and_elliptical_greetings() -> None:
    from core.callcenter import _is_smalltalk
    combined = (
        "pershendetje si je",
        "pershendetje, si je?",
        "si je pershendetje",
        "miredita, si po shkon?",
        "hello, si jeni",
        "faleminderit lamtumire",
        "pershendetje, si po shkon?",
    )
    for text in combined:
        assert _is_smalltalk(text), f"expected smalltalk for {text!r}"


def test_offline_smalltalk_rejects_banking_intent_alongside_greeting() -> None:
    from core.callcenter import _is_smalltalk
    not_smalltalk = (
        "pershendetje, cila banke ka komision me te ulet?",
        "pershendetje, sa kushton kredia?",
        "si je, a ofron depozita kjo banke?",
        "pershendetje tarifat e kartes",
    )
    for text in not_smalltalk:
        assert not _is_smalltalk(text), f"expected not smalltalk for {text!r}"


def test_pan_is_redacted_before_card_disambiguation(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Sa kushton mirëmbajtja e kartës 4111111111111111 te BKT?", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.PII_DETECTED
    assert not re.search(r"\d{8,}", decision.question)


def test_account_action_outranks_business_deposit_coverage(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Mbylle llogarinë time të depozitës së biznesit.", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_repeat_preserves_a_pending_transfer_flag() -> None:
    decision = decide(
        "Përsërite.", "Po jua kaloj një agjenti.", [],
        Outcome.HANDOFF, True,
    )
    assert decision.outcome is Outcome.REPEAT
    assert decision.handoff


def test_repeat_word_requires_a_word_boundary() -> None:
    assert not callcenter._is_repeat("repeatedly")
    assert callcenter._is_repeat("repeat")


@pytest.mark.parametrize(
    ("question", "reason", "handoff"),
    (
        (
            "OTP qe me derguat eshte 99120",
            DecisionReason.CREDENTIAL_DISCLOSURE,
            True,
        ),
        (
            "OTP që më dërguat është 99120",
            DecisionReason.CREDENTIAL_DISCLOSURE,
            True,
        ),
        (
            "sa eshte komisioni i dergimit te parave?",
            DecisionReason.DENSE_RETRIEVAL,
            False,
        ),
    ),
)
def test_credential_fast_path_send_verb_floor(
        monkeypatch, question, reason, handoff) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)

    decision = decide(question, "", [])
    assert decision.reason is reason
    assert decision.handoff is handoff


def test_deictic_bare_np_after_rate_clarify_stays_informational(
        monkeypatch) -> None:
    first_question = "Sa eshte komisioni i mirembajtjes se karta?"
    continuation = "Per karte debiti, per person fizik"
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, *_a: (
            "clarify" if "mirembajtjes" in callcenter.fold(question) else "answer"
        ),
    )
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    first = decide(first_question, "", [])
    second = decide(
        continuation, first.message,
        [
            {"role": "user", "content": first_question},
            {"role": "assistant", "content": first.message},
        ],
        last_outcome=first.outcome,
    )

    assert first.outcome is Outcome.CLARIFY
    assert second.outcome is None
    assert second.reason is DecisionReason.DENSE_RETRIEVAL
    assert not second.handoff


def test_bare_np_after_incident_handoff_still_reaches_backstop(
        monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    decision = decide(
        "Per karte debiti", "Po jua kaloj biseden nje agjenti.",
        [
            {"role": "user", "content": "Me humbi karta."},
            {"role": "assistant", "content": "Po jua kaloj biseden nje agjenti."},
        ],
        last_outcome=Outcome.HANDOFF, last_handoff=True,
    )

    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.INCIDENT_BACKSTOP


def test_incident_bare_np_does_not_enter_informational_floor(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    decision = decide(
        "Humbi kartela", "Mund ta sqaroni pak pyetjen?",
        [
            {"role": "user", "content": "Sa eshte komisioni i kartes?"},
            {"role": "assistant", "content": "Mund ta sqaroni pak pyetjen?"},
        ],
        last_outcome=Outcome.CLARIFY,
    )

    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.INCIDENT_BACKSTOP


def test_benign_transfers_do_not_trigger_incident_probe(monkeypatch):
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    for question in (
        "Dua të bëj një transfertë jashtë vendit.",
        "Dua të bëj një transfertë.",
        "Dua të bëj një transfertë brenda vendit.",
        "Dua të transferoj 500 euro në Itali.",
    ):
        decision = decide(question, "", [])
        assert decision.reason is DecisionReason.TRANSFER_CONTEXT_ESTABLISHED
        assert not decision.handoff

    procedure = decide("Si mund të bëj një transfertë jashtë vendit?", "", [])
    assert procedure.reason is DecisionReason.DENSE_RETRIEVAL
    assert not procedure.handoff


def test_transaction_actions_outrank_incident(monkeypatch):
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)
    for question in (
        "Anulo transfertën time.",
        "Dua të ndryshoj transfertën që bëra dje.",
        "Ndalo pagesën që sapo bëra.",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.ACCOUNT_ACTION_BACKSTOP


def test_unknown_transfer_outranks_fee_and_benign_process_phrase_does_not():
    for question in (
        "Më ka dalë një transfertë që nuk e njoh. Çfarë të bëj?",
        "Më ka dalë një transfertë që nuk e njoh.",
        "Nuk e njoh këtë transfertë.",
        "Nuk e kam bërë unë këtë transfertë.",
        "Kush e bëri këtë transfertë nga llogaria ime?",
        "Më janë marrë 500 euro dhe nuk e njoh pagesën.",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.INCIDENT_BACKSTOP
    benign = decide("Nuk e njoh mirë procesin e transfertës.", "", [])
    assert benign.reason is DecisionReason.DENSE_RETRIEVAL


def test_credential_declaration_outranks_transfer_fee():
    for question in (
        "PIN-i im është 1234. Sa kushton një transfertë jashtë vendit?",
        "PIN është 1234, sa kushton transferta?",
        "OTP-ja ime është 654321.",
        "Kodi OTP është 654321. Dua të transferoj para.",
        "Fjalëkalimi im është test123. Si bëj transfertë?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.CREDENTIAL_DISCLOSURE
        assert "1234" not in (decision.question or "")
        assert "654321" not in (decision.question or "")


def test_four_digit_transfer_amount_is_not_credential():
    decision = decide("Dua të transferoj 1234 euro.", "", [])
    assert decision.reason is not DecisionReason.CREDENTIAL_DISCLOSURE

def test_colloquial_transfer_does_not_trigger_incident(monkeypatch):
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    for question in (
        "Po me çu lekë brenda Shqipnisë?",
        "Po me çu 500 euro Itali sa mban banka?",
        "Nëse çoj lekë jashtë sa më kushton?",
        "Kam me çu 300 euro në Gjermani, sa më mban?",
        "Sa mban banka me çu lekë jashtë?",
    ):
        decision = decide(question, "", [])
        assert decision.reason is not DecisionReason.INCIDENT_BACKSTOP
        assert not decision.handoff


def test_credential_declaration_separator_boundaries():
    positives = (
        "PIN: 1234", "pin im: 1234", "PIN = 1234",
        "OTP: 654321", "OTP = 654321", "Password: test123",
        "Password = test123", "PASSWORD-i im është test123",
        "CVV: 123", "CVC = 456",
    )
    for question in positives:
        decision = decide(question, "", [])
        assert decision.reason is DecisionReason.CREDENTIAL_DISCLOSURE
        assert decision.handoff
    negatives = (
        "PIN-i sa shifra ka?", "Si mund ta ndryshoj PIN-in?",
        "Çfarë është OTP?", "Kam harruar password-in.",
        "Si funksionon CVV?", "Dua të transferoj 1234 euro.",
        "Komisioni është 1234 lekë?",
    )
    for question in negatives:
        decision = decide(question, "", [])
        assert decision.reason is not DecisionReason.CREDENTIAL_DISCLOSURE


def test_unknown_transfer_type_questions_are_not_incidents(monkeypatch):
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "incident")
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    for question in (
        "Nuk e njoh këtë lloj transferte.",
        "Si trajtohet një transfertë që klienti nuk e njeh?",
        "Çfarë duhet të bëjë një klient kur nuk njeh një pagesë?",
    ):
        decision = decide(question, "", [])
        assert decision.reason is not DecisionReason.SEMANTIC_INCIDENT
        assert decision.outcome is not Outcome.HANDOFF


# ---- Deictic follow-up regressions (live transcript 2026-09-07) ----
# "cfare produktesh ofrojne ato?" follows the bank catalog; "ato" refers to the
# banks just listed. The capability gate must not require a literal "bank"
# word when a deictic plural subject is present.
def test_deictic_product_capability_followup_after_catalog() -> None:
    from core.callcenter import _is_product_capability_speech
    assert _is_product_capability_speech("cfare produktesh ofrojne ato?")
    assert _is_product_capability_speech("cfare sherbimesh ofrojne ato?")
    assert not _is_product_capability_speech("cfare tarifash aplikon Banka BKT?")
    assert not _is_product_capability_speech("cfare produktesh ka me normen me te ulet?")


def test_deictic_product_capability_maps_to_product_capability_decision(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    decision = decide("cfare produktesh ofrojne ato?", "", [])
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is DecisionReason.PRODUCT_CAPABILITY
    assert "kredi" in decision.message


# "dua te informohem per normat e interesit" is an open rate request; it must
# resolve to the deterministic missing-product path (answerable), NOT fall to
# dense and claim the rates are not published (self-contradiction with the
# deposit table that follows).
def test_open_intent_rate_ask_is_missing_product_not_unrepresented() -> None:
    from core.comparison import parse_rate_intent
    parsed = parse_rate_intent("dua te informohem per normat e interesit")
    assert parsed.status == "unsupported"
    assert parsed.reason == "missing_product"
    assert parsed.intent is not None
    assert parsed.intent.metric == "interest_rate"


# "cila prej bankave ofron interesin me te mire?" uses the genitive plural
# bank form. It must reach the same comparison-dimensions CLARIFY surface as
# the tested "cila banke ofron interesin me te mire?" form — and because no
# product word is present, the resolver correctly infers the deposit family
# from the interest-rate rows, so the missing dimensions are the deposit
# comparison dims (currency/term/amount/segment), never a generic refusal.
def test_deictic_prej_bankave_superlative_clarifies() -> None:
    from core.comparison import parse_rate_intent
    parsed = parse_rate_intent("cila prej bankave ofron interesin me te mire?")
    assert parsed.status == "unsupported"
    assert parsed.reason == "comparison_dimensions_missing"
    assert parsed.coverage is not None
    assert "term_months" in parsed.coverage.unresolved_qualifiers


# ---- Bare courtesy fragments ----
# "te lutem" / "ju lutem" alone is a polite courtesy, not a banking question.
# It must route to the fragment-meta keep-helping reply, NEVER a retrieval
# attempt (which previously rewrote it into a registry follow-up and abstained).
# Polite OPENERS ("ju lutem, sa kushton...?") must be untouched.
def test_bare_courtesy_fragments_are_meta_floor(monkeypatch) -> None:
    from core.callcenter import COURTESY_MESSAGE, _fragment_meta_preflight
    for phrase in ("te lutem", "ju lutem", "lutem", "te lutem?", "Ju lutem"):
        decision = _fragment_meta_preflight(phrase)
        assert decision is not None
        assert decision.outcome is Outcome.ANSWER
        assert decision.reason is DecisionReason.FRAGMENT_META
        assert decision.message == COURTESY_MESSAGE


def test_bare_courtesy_openers_do_not_swallow_banking_questions(monkeypatch) -> None:
    from core.callcenter import _fragment_meta_preflight
    for question in (
        "ju lutem, sa kushton transferta brenda vendit?",
        "ju lutem, cila banke ka normen me te mire?",
        "te lutem, po per depozitat?",
        "ju lutem dergoni nje pyetje te qarte",
    ):
        assert _fragment_meta_preflight(question) is None


def test_bare_courtesy_never_reaches_retrieval(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question",
                        lambda _t: pytest.fail("courtesy must not reach retrieval"))
    decision = decide("te lutem", "", [])
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is DecisionReason.FRAGMENT_META


# "keto banka" (these banks) is a deictic plural reference to the banks just
# listed — never an unknown bank name. It must not produce the
# "unknown_bank" catalog refusal (the transcript's "me cfare normash interesi
# i ofrojne keto banka kredite konsumatore?").
def test_deictic_keto_banka_is_not_unknown_bank() -> None:
    from core.comparison import parse_rate_intent
    parsed = parse_rate_intent(
        "me cfare normash interesi i ofrojne keto banka kredite konsumatore?"
    )
    assert parsed.status != "unsupported" or parsed.reason != "unknown_bank"
    assert parsed.intent is None or parsed.intent.bank_scope == "all"


# ---- Deictic bank-scoping follow-ups (live transcript 2026-09-07) ----
# "banka raiffeisen" / "banka credins" right after a structured rate listing
# are bare-bank continuations — they must scope the frame to that bank, not
# fall through to dense and abstain.
def test_bare_bank_followup_scopes_deposit_frame(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    from core.comparison import parse_rate_intent
    frame = parse_rate_intent("cilat jane normat e interesit?").intent
    assert frame is not None
    decision = callcenter._structured_rate_decision(
        "banka raiffeisen", frame=frame,
    )
    assert decision is not None
    assert decision.rate_intent is not None
    assert decision.rate_intent.banks == ("Banka Raiffeisen",)
    assert decision.rate_intent.bank_scope == "named"
    assert decision.reason is not DecisionReason.DENSE_RETRIEVAL


def test_bare_bank_followup_outranks_dense(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    from core.comparison import parse_rate_intent
    frame = parse_rate_intent("cilat jane normat e interesit?").intent
    decision = callcenter.decide(
        "banka credins", "", [], last_structured_frame=frame,
    )
    assert decision.reason not in (
        DecisionReason.DENSE_RETRIEVAL,
        DecisionReason.DENSE_NO_TRUSTED_HITS,
    )


# "per cilen banke behet fjale?" after a bank-less credit listing must answer
# with the attribution boundary (honest), never a generic abstain.
def test_deictic_which_bank_after_bankless_listing_answers_boundary(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    from core.comparison import parse_rate_intent
    from core.callcenter import _deictic_bank_scope_preflight
    frame = parse_rate_intent("po per kredi?").intent or parse_rate_intent(
        "cilat jane normat e interesit?").intent
    # force the credit family frame (bankless)
    credit_frame = frame._replace(family="credit", product=None,
                                  banks=(), bank_scope="all")
    decision = _deictic_bank_scope_preflight(
        "per cilen banke behet fjale?", credit_frame,
    )
    assert decision is not None
    assert decision.outcome is Outcome.ANSWER
    assert "nuk i atribuon çdo shifër një banke" in decision.message


# The deictic question must run as a decide() preflight on the ORIGINAL
# question (before the LLM rewrite into a fuller rate ask, which parses
# unknown_bank and abstains). Deposit context: per-bank rows -> CLARIFY which
# bank. Credit context: bankless rows -> attribution boundary. No frame ->
# normal path untouched.
def test_deictic_which_bank_deposit_frame_clarifies_via_decide(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    from core.comparison import parse_rate_intent
    frame = parse_rate_intent("cilat jane normat e interesit?").intent
    assert frame is not None
    decision = callcenter.decide(
        "per cilen banke behet fjale?", "", [], last_structured_frame=frame,
    )
    assert decision is not None
    assert decision.outcome is Outcome.CLARIFY
    assert decision.reason is DecisionReason.CATALOG_UNKNOWN_BANK
    assert "Për cilën bankë dëshironi" in decision.message


def test_deictic_which_bank_credit_frame_answers_boundary_via_decide(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    from core.comparison import parse_rate_intent
    frame = parse_rate_intent("po per kredi?").intent or parse_rate_intent(
        "cilat jane normat e interesit?").intent
    credit_frame = frame._replace(family="credit", product=None,
                                  banks=(), bank_scope="all")
    decision = callcenter.decide(
        "per cilen banke behet fjale?", "", [], last_structured_frame=credit_frame,
    )
    assert decision is not None
    assert decision.outcome is Outcome.ANSWER
    assert "nuk i atribuon çdo shifër një banke" in decision.message


def test_deictic_which_bank_without_frame_is_normal_path(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    decision = callcenter.decide("per cilen banke behet fjale?", "", [])
    assert decision.reason is DecisionReason.DENSE_RETRIEVAL
