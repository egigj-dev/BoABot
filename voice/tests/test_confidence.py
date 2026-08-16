"""Schema 1 §5 calibrated confidence decisions."""

from voice.events import Transcript
from voice.schema1 import ConfidenceAction, ConfidencePolicy


def test_confidence_policy_proceed_clarify_handoff() -> None:
    policy = ConfidencePolicy()
    assert policy.evaluate(Transcript("Më tregoni rregulloren", True, 0.90)).action is ConfidenceAction.PROCEED
    assert policy.evaluate(Transcript("Më tregoni rregulloren", True, 0.70)).action is ConfidenceAction.CLARIFY
    assert policy.evaluate(Transcript("Më tregoni rregulloren", True, 0.40)).action is ConfidenceAction.HANDOFF


def test_critical_span_needs_point_85() -> None:
    transcript = Transcript("Norma është 10 EUR", True, 0.95,
                            critical_confidences={"10": 0.83, "EUR": 0.99})
    assert ConfidencePolicy().evaluate(transcript).action is ConfidenceAction.CLARIFY


def test_critical_span_gate_bypass_is_explicit_and_per_call(monkeypatch) -> None:
    transcript = Transcript("Norma është 10 EUR", True, 0.95)
    policy = ConfidencePolicy()

    monkeypatch.delenv("VOICE_CONFIDENCE_CRITICAL_DISABLED", raising=False)
    assert policy.evaluate(transcript).reason == "critical-span confidence unavailable"

    monkeypatch.setenv("VOICE_CONFIDENCE_CRITICAL_DISABLED", "1")
    decision = policy.evaluate(transcript)
    assert decision.action is ConfidenceAction.PROCEED
    assert decision.reason == "critical-span gate bypassed: provider confidence proven constant"


def test_safety_keyword_disagreement_handoffs() -> None:
    transcript = Transcript("Kam humbur kodin", True, 0.95, alternatives=("Kam humbur PIN",))
    assert ConfidencePolicy().evaluate(transcript).action is ConfidenceAction.HANDOFF


def test_pin_substring_inside_albanian_word_is_not_a_safety_keyword() -> None:
    transcript = Transcript(
        "Kredi për shtëpi", True, 0.95,
        alternatives=("Kredi për shtëpinë",),
    )
    assert ConfidencePolicy().evaluate(transcript).action is ConfidenceAction.PROCEED
