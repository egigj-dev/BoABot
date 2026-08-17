"""Approved-text-only renderers shared by both voice schemas."""

from .base import TTS
from .fake_tts import FakeTTS

__all__ = ["TTS", "FakeTTS"]
