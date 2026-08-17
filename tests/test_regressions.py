"""Source-level guards for previously fitted trust-boundary workarounds."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_eval_literals_are_not_encoded_in_policy_modules() -> None:
    policy_source = "\n".join(
        (ROOT / "core" / name).read_text(encoding="utf-8")
        for name in ("callcenter.py", "trust.py")
    ).casefold()
    for fitted_literal in (
        "kjo rregullore", "banka me e mire", "deklaroj qirane", "tatimet",
    ):
        assert fitted_literal not in policy_source


def test_no_non_retriever_code_assigns_a_generic_score_field() -> None:
    for path in (ROOT / "core" / "rag.py", ROOT / "core" / "trust.py", ROOT / "core" / "api.py"):
        source = path.read_text(encoding="utf-8")
        assert '["score"] =' not in source
        assert "['score'] =" not in source
