"""LLM turn-router: label routing + offline fallback behavior.

The router (core/router.py) is OFF by default (BOABOT_LLM_ROUTER unset). These
tests exercise the decide() seam with injected labels (router ON) and the
lexical fallback (router OFF), so the whole matrix is deterministic and offline.
"""

import numpy as np
import pytest

import core.callcenter as callcenter
import core.router as router
from core.callcenter import Outcome, decide


@pytest.fixture(autouse=True)
def _offline_stubs(monkeypatch):
    # Deterministic retrieval-path stubs; not exercised for terminal routes.
    monkeypatch.setattr(callcenter, "_encode_question", lambda _t: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)


def _inject(monkeypatch, label):
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, last_outcome=None, last_handoff=False: label,
    )


def test_router_labels_map_to_terminal_outcomes(monkeypatch) -> None:
    expectations = (
        ("smalltalk",      Outcome.ANSWER,      False, "pershendetje"),
        ("out_of_domain",  Outcome.UNSUPPORTED, False, "cfare eshte moti sot?"),
        ("legal_advice",   Outcome.UNSUPPORTED, False, "a eshte e ligjshme kjo gjobë?"),
        ("account_action", Outcome.HANDOFF,     True,  "mbyll llogarinë time"),
        ("incident",       Outcome.HANDOFF,     True,  "kam humbur kartën"),
        ("clarify",        Outcome.CLARIFY,     False, "cfare karte?"),
    )
    for label, outcome, handoff, question in expectations:
        _inject(monkeypatch, label)
        decision = decide(question, "", [])
        assert decision.outcome is outcome, label
        assert decision.handoff is handoff, label
        assert decision.reason is not None, label


def test_router_account_action_without_lexical_vocabulary_does_not_escalate(monkeypatch) -> None:
    # Fail-closed: an LLM "account_action" on a turn with no deterministic
    # account-action vocabulary ("nuk kam karte" is a negation, not an action)
    # must NOT hand off to a human.
    _inject(monkeypatch, "account_action")
    decision = decide("nuk kam karte", "", [])
    assert decision.outcome is Outcome.ANSWER  # negation floor fires first
    assert decision.reason == "negation_statement"
    # A genuinely action-y turn with the label still escalates.
    _inject(monkeypatch, "account_action")
    decision = decide("mbyll llogarinë time", "", [])
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason == "account_action"


def test_router_clarify_uses_generic_not_card_message(monkeypatch) -> None:
    # A general "clarify" label must ask the user to restate generically — it
    # must NOT trigger the card-debit/credit script (that stays on the lexical
    # is_ambiguous_card_maintenance path only).
    _inject(monkeypatch, "clarify")
    decision = decide("cfare do te thuash?", "", [])
    assert decision.outcome is Outcome.CLARIFY
    assert decision.message == callcenter.CLARIFY_MESSAGE
    assert decision.message != callcenter.CARD_CLARIFY_MESSAGE


def test_router_answer_falls_through_to_retrieval(monkeypatch) -> None:
    _inject(monkeypatch, "answer")
    decision = decide("cila eshte norma e interesit per depozita?", "", [])
    assert decision.outcome is None
    assert decision.question  # clean question survives for retrieval


def test_router_meta_followup_uses_prior_handoff(monkeypatch) -> None:
    _inject(monkeypatch, "meta_followup")
    decision = decide("pse duhet te trajtohet nga nje agjent?", "", [],
                      Outcome.HANDOFF, True)
    assert decision.outcome is Outcome.ANSWER
    assert decision.handoff is True
    assert decision.reason == "meta_followup"


def test_router_off_uses_lexical_fallback(monkeypatch) -> None:
    # Router OFF: seam returns None -> old lexical routing must still work.
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *a, **k: None)
    assert decide("si je?", "", []).outcome is Outcome.ANSWER
    assert decide("si je?", "", []).reason == "smalltalk"
    decision = decide("mbyll llogarinë time të depozitës së biznesit.", "", [])
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason == "account_action"


def test_meta_help_questions_route_to_meta_not_retrieval(monkeypatch) -> None:
    # Meta/help turns ("what can I ask?", "help") must deterministically get the
    # continue-helping meta response — never retrieval, never the card script,
    # even with the router OFF (the floor fires before the enable check).
    for question in (
        "cfare mund te te pyes per shembull?",
        "si te pyes?",
        "ndihme",
        "cfare di te bej?",
        "help",
    ):
        assert router.is_meta_help(question) is True, question
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.ANSWER, question
        assert decision.reason == "meta_followup", question


def test_meta_help_does_not_capture_banking_questions() -> None:
    # A real banking question must NOT be swallowed by the meta-help floor.
    for question in (
        "cfare eshte norma e interesit per depozita?",
        "cfare ben Banka e Shqiperise per mbrojtjen e konsumatorit?",
    ):
        assert router.is_meta_help(question) is False, question


def test_hypothetical_rights_account_question_not_handed_off(monkeypatch) -> None:
    # Even when the router botches (says answer) for a rights question that
    # contains account vocabulary, the hypothetical carve-out must NOT hand off.
    _inject(monkeypatch, "answer")
    decision = decide(
        "A garanton Banka e Shqipërisë që banka ime nuk mund të më mbyllë llogarinë?",
        "", [],
    )
    assert decision.outcome is None  # fall through to retrieval


def test_genuine_account_request_backstops_even_if_router_botches(monkeypatch) -> None:
    _inject(monkeypatch, "answer")  # router fails to see the account request
    decision = decide("mbyll llogarinë time të depozitës së biznesit.", "", [])
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason == "account_action_backstop"


# --- conversational-fragment floor (core/router.py, both ON and OFF) ---------

def test_conversational_fragments_never_reach_retrieval() -> None:
    # No seam mock: goes through the real classify_turn, whose fragment floor
    # fires before the enabled/discarded checks — so even router-OFF (unset env)
    # these must route to meta_followup and never fall through to retrieval.
    for question in (
        "pse?", "perse", "nuk te kuptoj", "nuk kuptoj",
        "kjo nuk ishte pyetja ime", "c'behet ne pergjithesi?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.ANSWER, question
        assert decision.reason == "meta_followup", question
        assert decision.question, question


def test_fragment_floor_preserves_prior_handoff_context() -> None:
    decision = decide("pse?", "", [], Outcome.HANDOFF, True)
    assert decision.outcome is Outcome.ANSWER
    assert decision.handoff is True
    assert decision.reason == "meta_followup"


def test_fragment_floor_does_not_swallow_domain_questions(monkeypatch) -> None:
    # A "why" fragment that names a banking subject is a real query, not meta.
    assert not callcenter_router_fragment("pse u rrit interesi i kredise?")
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *a, **k: None)
    decision = decide("pse u rrit interesi i kredise?", "", [])
    assert decision.outcome is None  # falls through to retrieval


def callcenter_router_fragment(question: str) -> bool:
    from core.router import is_conversational_fragment
    return is_conversational_fragment(question)
