"""Dependency-light energy VAD and endpoint controller for Schema 1 §3."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum


class VADEvent(str, Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    MAX_TURN = "max_turn"


@dataclass(slots=True)
class EnergyVAD:
    """Endpoint PCM16 frames; this component never makes answer-safety decisions."""

    energy_threshold: int = 450
    min_speech_ms: int = 120
    silence_end_ms: int = 500
    max_turn_ms: int = 20_000
    frame_ms: int = 20
    speaking: bool = False
    _speech_ms: int = 0
    _silence_ms: int = 0
    _turn_ms: int = 0

    def process(self, pcm16: bytes) -> list[VADEvent]:
        sample_bytes = len(pcm16) - (len(pcm16) % 2)
        if not sample_bytes:
            return []
        samples = tuple(value[0] for value in struct.iter_unpack("<h", pcm16[:sample_bytes]))
        energy = int(math.sqrt(sum(value * value for value in samples) / len(samples)))
        voiced = energy >= self.energy_threshold
        events: list[VADEvent] = []
        self._turn_ms += self.frame_ms
        if voiced:
            self._speech_ms += self.frame_ms
            self._silence_ms = 0
            if not self.speaking and self._speech_ms >= self.min_speech_ms:
                self.speaking = True
                events.append(VADEvent.SPEECH_START)
        else:
            self._speech_ms = 0 if not self.speaking else self._speech_ms
            if self.speaking:
                self._silence_ms += self.frame_ms
                if self._silence_ms >= self.silence_end_ms:
                    self._reset()
                    events.append(VADEvent.SPEECH_END)
        if self.speaking and self._turn_ms >= self.max_turn_ms:
            self._reset()
            events.append(VADEvent.MAX_TURN)
        return events

    def _reset(self) -> None:
        self.speaking = False
        self._speech_ms = self._silence_ms = self._turn_ms = 0

    def reset(self) -> None:
        self._reset()
