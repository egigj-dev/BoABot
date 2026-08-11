"""Provider-neutral streaming ASR adapters for Schema 1."""

from collections.abc import Iterable

from ..config import VoiceSettings
from ..events import Transcript
from .base import StreamingASR
from .fake_adapter import FakeStreamingASR


def configured_asr(settings: VoiceSettings,
                   fake_script: Iterable[Transcript] = ()) -> StreamingASR:
    """Select the configured adapter without importing any provider SDK."""
    if settings.asr_provider == "fake":
        return FakeStreamingASR(fake_script)
    if settings.asr_provider == "azure":
        from .azure_adapter import AzureStreamingASR
        return AzureStreamingASR(settings)
    if settings.asr_provider == "chirp":
        from .chirp_adapter import ChirpStreamingASR
        return ChirpStreamingASR(settings)
    raise ValueError("BOABOT_ASR_PROVIDER must be azure, chirp, or fake")


__all__ = ["StreamingASR", "FakeStreamingASR", "configured_asr"]
