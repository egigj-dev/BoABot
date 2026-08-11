"""Environment configuration for Schema 1 §3 and Schema 2 §3 components."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    """Raised only when a selected real adapter lacks required configuration."""


def _float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < 0:
        raise ConfigurationError(f"{name} must be non-negative")
    return value


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Settings loaded without validating unused provider credentials."""

    turn_base_url: str
    first_token_budget_ms: int
    asr_provider: str
    pcm_sample_rate_hz: int
    azure_speech_key: str | None
    azure_speech_region: str | None
    azure_tts_key: str | None
    azure_tts_region: str | None
    azure_tts_voice: str
    gcp_project: str | None
    gcp_speech_region: str
    chirp_model: str
    gemini_api_key: str | None
    gemini_live_model: str
    confidence_proceed: float
    confidence_critical: float
    confidence_handoff: float
    latency_p50_target_ms: float
    latency_p95_target_ms: float
    redis_url: str | None
    telephony_mode: str

    @classmethod
    def from_env(cls) -> "VoiceSettings":
        """Read configuration; real adapters perform their own fail-fast checks."""
        settings = cls(
            turn_base_url=os.getenv("BOABOT_TURN_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            first_token_budget_ms=int(os.getenv("BOABOT_FIRST_TOKEN_BUDGET_MS", "6000")),
            asr_provider=os.getenv("BOABOT_ASR_PROVIDER", "fake").lower(),
            pcm_sample_rate_hz=int(os.getenv("VOICE_PCM_SAMPLE_RATE_HZ", "16000")),
            azure_speech_key=os.getenv("AZURE_SPEECH_KEY"),
            azure_speech_region=os.getenv("AZURE_SPEECH_REGION"),
            azure_tts_key=os.getenv("AZURE_TTS_KEY") or os.getenv("AZURE_SPEECH_KEY"),
            azure_tts_region=os.getenv("AZURE_TTS_REGION") or os.getenv("AZURE_SPEECH_REGION"),
            azure_tts_voice=os.getenv("AZURE_TTS_VOICE", "sq-AL-AnilaNeural"),
            gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            gcp_speech_region=os.getenv("GOOGLE_SPEECH_REGION", "europe-west4"),
            chirp_model=os.getenv("GOOGLE_CHIRP_MODEL", "chirp_3"),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_live_model=os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview"),
            confidence_proceed=_float("VOICE_CONFIDENCE_PROCEED", 0.75),
            confidence_critical=_float("VOICE_CONFIDENCE_CRITICAL", 0.85),
            confidence_handoff=_float("VOICE_CONFIDENCE_HANDOFF", 0.55),
            latency_p50_target_ms=_float("VOICE_LATENCY_P50_TARGET_MS", 1500),
            latency_p95_target_ms=_float("VOICE_LATENCY_P95_TARGET_MS", 2500),
            redis_url=os.getenv("VOICE_REDIS_URL"),
            telephony_mode=os.getenv("VOICE_TELEPHONY_MODE", "simulated").lower(),
        )
        if not (settings.confidence_handoff < settings.confidence_proceed <= settings.confidence_critical):
            raise ConfigurationError("confidence thresholds must satisfy handoff < proceed <= critical")
        return settings

    @staticmethod
    def _require(label: str, values: dict[str, str | None]) -> dict[str, str]:
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigurationError(f"{label} requires: {', '.join(missing)}")
        return {name: str(value) for name, value in values.items()}

    def require_azure_asr(self) -> dict[str, str]:
        return self._require("Azure ASR", {"AZURE_SPEECH_KEY": self.azure_speech_key,
                                           "AZURE_SPEECH_REGION": self.azure_speech_region})

    def require_azure_tts(self) -> dict[str, str]:
        return self._require("Azure TTS", {"AZURE_TTS_KEY": self.azure_tts_key,
                                           "AZURE_TTS_REGION": self.azure_tts_region})

    def require_chirp(self) -> dict[str, str]:
        return self._require("Chirp ASR", {"GOOGLE_CLOUD_PROJECT": self.gcp_project})

    def require_gemini_live(self) -> dict[str, str]:
        return self._require("Gemini Live", {"GEMINI_API_KEY": self.gemini_api_key})
