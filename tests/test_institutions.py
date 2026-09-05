from __future__ import annotations

import core.callcenter as callcenter
from core.institutions import canonicalize_institution, institution_catalog_message, institution_match_terms
from core.trust import trusted_hits


def test_bkt_resolves_to_one_canonical_institution_everywhere():
    assert canonicalize_institution("BKT") == "Banka Kombëtare Tregtare"
    assert "bkt" in institution_match_terms()


def test_catalog_renders_complete_canonical_names_not_tokens():
    message = institution_catalog_message()
    assert message == callcenter._catalog_message()
    assert "Banka Kombëtare Tregtare" in message
    assert "Banka Amerikane e Investimeve Shqiperi" in message
    catalog = message.split(". Burimi:", 1)[0].split(": ", 1)[1].split(", ")
    for fragment in ("Amerikane", "Investimeve", "Kombëtare", "Tregtare"):
        assert fragment not in catalog


def test_bkt_rate_query_rejects_regulation_only_evidence():
    result = trusted_hits(
        "Cilat janë normat e interesit të BKT?",
        [{"id": "reg_001", "text": "Rregullore për interesin.", "dense_score": 0.99}],
    )
    assert not result.allowed
    assert result.reason == "wrong_chunk_family"


def test_rate_issuer_uses_canonical_institution_name():
    from core.trust import issuer_of

    assert issuer_of("rate_0000", "Banka Kombëtare Tregtare: 0.50") == "Banka Kombëtare Tregtare"
