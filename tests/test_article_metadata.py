"""Article metadata hygiene for maintained chunk rebuild paths."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rebuild_chunks  # noqa: E402
import rebuild_chunks_dedup  # noqa: E402


def _row(article, text, row_id="reg_test"):
    return rebuild_chunks.SourceRow(
        id=row_id,
        doc="Rregullore_test.pdf",
        article=article,
        status="canonical",
        section="rregullore_mbik",
        url="https://example.test/doc.pdf",
        text=text,
        embedding="[0.1,0.2]",
    )


def test_standalone_year_like_article_identifier_is_rejected():
    assert not rebuild_chunks.valid_article_identifier("2015")
    assert not rebuild_chunks.valid_article_identifier("1912")
    assert not rebuild_chunks.valid_article_identifier("2038")
    assert rebuild_chunks.valid_article_identifier("15")
    assert rebuild_chunks.valid_article_identifier("15/1")


def test_base_rebuild_clears_year_like_article_and_synthetic_header():
    row = _row(
        "2015",
        "Rregullore_test.pdf — Neni 2015\n"
        "Neni 2015\nMasat mbikëqyrëse dhe ndëshkimore",
    )

    output, mapping, _stats = rebuild_chunks.build_output([row])

    assert mapping == {"reg_test": "reg_test"}
    assert len(output) == 1
    assert output[0].article is None
    assert not output[0].text.startswith("Rregullore_test.pdf — Neni 2015")
    assert output[0].embedding is None


def test_dedup_rebuild_applies_same_year_sanitization():
    row = _row(
        "2015",
        "Rregullore_test.pdf — Neni 2015\nTeksti i aneksit",
    )

    output, mapping, _stats = rebuild_chunks_dedup.build_output([row])

    assert mapping == {"reg_test": "reg_test"}
    assert output[0].article is None
    assert output[0].text == "Teksti i aneksit"
    assert output[0].embedding is None


def test_false_body_year_heading_is_removed_but_legitimate_text_is_preserved():
    row = _row(
        "2015",
        "Rregullore_test.pdf — Neni 2015\nNeni 2015 \nTeksti i aneksit "
        "për vitin 2015 dhe neni 15.",
    )
    output, _mapping, _stats = rebuild_chunks.build_output([row])
    assert output[0].text == "Teksti i aneksit për vitin 2015 dhe neni 15."
