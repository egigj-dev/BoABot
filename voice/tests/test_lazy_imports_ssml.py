"""Offline importability and deterministic SSML rendering."""

import importlib

from voice.tts.ssml import canonicalize


def test_provider_modules_import_without_provider_sdks() -> None:
    for name in ("voice.asr.azure_adapter", "voice.asr.chirp_adapter",
                 "voice.tts.azure_tts", "voice.schema2"):
        importlib.import_module(name)


def test_ssml_preserves_approved_values_and_marks_albanian_readings() -> None:
    text = "Banka OTP Albania ka normë 2,5% dhe tarifë 10 EUR më 11/08/2026."
    ssml = canonicalize(text)
    for exact in ("Banka OTP Albania", "2,5%", "10 EUR", "11/08/2026"):
        assert exact in ssml
    assert "për qind" in ssml
    assert 'xml:lang="sq-AL"' in ssml
