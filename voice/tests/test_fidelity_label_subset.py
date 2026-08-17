"""Regression coverage for FidelityGuard label-subset compatibility."""

import pytest

from voice.shared.fidelity_guard import FidelityGuard


@pytest.mark.parametrize(
    ("case", "sentence", "evidence", "expected_approved"),
    [
        (
            "identical_labels",
            "Komisioni i administrimit të llogarisë është 10 EUR.",
            "Komisioni i administrimit të llogarisë është 10 EUR.",
            True,
        ),
        (
            "sentence_label_subset_of_evidence_label",
            "Komisioni i administrimit të llogarisë është 10 EUR.",
            "Komisioni mujor i administrimit të llogarisë është 10 EUR.",
            True,
        ),
        (
            "shared_token_but_different_label",
            "Komisioni i administrimit të llogarisë është 10 EUR.",
            "Komisioni i administrimit të kredisë është 10 EUR.",
            False,
        ),
        (
            "distinguishing_token_removed",
            "Komisioni i administrimit është 10 EUR.",
            "Komisioni i administrimit të kredisë është 10 EUR.",
            True,
        ),
    ],
)
def test_fidelity_guard_uses_label_subset_semantics(
    case: str,
    sentence: str,
    evidence: str,
    expected_approved: bool,
) -> None:
    del case
    result = FidelityGuard().verify_sources(
        sentence, [{"passage_text": evidence}]
    )
    assert result.approved is expected_approved
