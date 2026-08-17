"""Azure transcript alignment and corpus-entity correction regressions."""

from voice.arm_a.asr.azure_adapter import (
    _canonicalize_bank_entity,
    _critical_confidences,
)


def test_formatted_36_aligns_to_spoken_albanian_number_words() -> None:
    words = [
        {"Word": "depozitën", "Confidence": 0.94},
        {"Word": "tridhjetë", "Confidence": 0.93},
        {"Word": "e", "Confidence": 0.92},
        {"Word": "gjashtë", "Confidence": 0.91},
        {"Word": "mujore", "Confidence": 0.90},
    ]

    assert "36" not in _critical_confidences("depozitën 36 mujore", words)


def test_uniquely_repairs_observed_otp_bank_variants() -> None:
    phrases = (
        "Banka Credins", "Banka OTP Albania", "Banka Tirana",
        "Banka e Parë e Investimeve Albania",
    )
    synthetic, synthetic_audit = _canonicalize_bank_entity(
        "te banka auto albania", phrases
    )
    browser, browser_audit = _canonicalize_bank_entity(
        "dhe pa nga top albania", phrases
    )

    assert synthetic == "te Banka OTP Albania"
    assert browser == "dhe Banka OTP Albania"
    assert synthetic_audit[0]["raw"] == "banka auto albania"
    assert browser_audit[0]["canonical"] == "Banka OTP Albania"


def test_does_not_repair_an_unrelated_albania_phrase() -> None:
    text = "normat mesatare në Shqipëri dhe Albania"
    corrected, audit = _canonicalize_bank_entity(
        text, ("Banka OTP Albania", "Banka Tirana")
    )
    assert corrected == text
    assert audit == ()
