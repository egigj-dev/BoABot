"""Provider-neutral transcript confidence policy shared by both voice arms."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .events import Transcript

CRITICAL_RE = re.compile(
    r"\b(?:PIN|CVV|CVC|OTP)\b|\b\d+(?:[,.]\d+)?\s*(?:%|ALL|EUR|USD|lek(?:ë|e)?)\b",
    re.IGNORECASE,
)
SAFETY_TERMS = {"pin", "cvv", "cvc", "otp"}


def _safety_terms(text: str) -> set[str]:
    """Return exact credential-safety words, never substrings of Albanian words."""
    return set(re.findall(r"[^\W_]+", text.casefold())) & SAFETY_TERMS


class ConfidenceAction(str, Enum):
    PROCEED = "proceed"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class ConfidenceDecision:
    action: ConfidenceAction
    reason: str


@dataclass(slots=True)
class ConfidencePolicy:
    """Schema 1 §5 initial thresholds; calibrate separately for each provider."""

    proceed: float = 0.75
    critical: float = 0.85
    handoff: float = 0.55
    failed_clarifications: int = 0
    enabled: bool = True

    def evaluate(self, transcript: Transcript) -> ConfidenceDecision:
        # probe_confidence.py proves this provider confidence is constant, so the bypass is explicit.
        alternative_terms = {
            term for alternative in transcript.alternatives
            for term in _safety_terms(alternative)
        }
        primary_terms = _safety_terms(transcript.text)
        if alternative_terms != primary_terms and (alternative_terms or primary_terms):
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "safety-keyword ambiguity")
        if not self.enabled:
            return ConfidenceDecision(ConfidenceAction.PROCEED, "confidence gate explicitly disabled")
        if transcript.confidence is None:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "provider confidence unavailable")
        if transcript.confidence < self.handoff:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "overall confidence below handoff threshold")
        critical_spans = CRITICAL_RE.findall(transcript.text)
        if critical_spans:
            if not transcript.critical_confidences:
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "critical-span confidence unavailable")
            if any(transcript.critical_confidences.get(span, 0.0) < self.critical for span in critical_spans):
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "critical span below threshold")
            normalized_alternatives = {alternative.casefold().strip() for alternative in transcript.alternatives}
            if normalized_alternatives and any(span.casefold() not in " ".join(normalized_alternatives)
                                               for span in critical_spans):
                return ConfidenceDecision(ConfidenceAction.CLARIFY, "conflicting critical alternative")
        if transcript.confidence < self.proceed:
            return ConfidenceDecision(ConfidenceAction.CLARIFY, "overall confidence below proceed threshold")
        return ConfidenceDecision(ConfidenceAction.PROCEED, "accepted")

    def record(self, decision: ConfidenceDecision, server_outcome: str) -> None:
        if decision.action is ConfidenceAction.CLARIFY and server_outcome == "clarify":
            self.failed_clarifications += 1
        elif decision.action is ConfidenceAction.PROCEED:
            self.failed_clarifications = 0

    def effective(self, transcript: Transcript) -> ConfidenceDecision:
        decision = self.evaluate(transcript)
        if decision.action is ConfidenceAction.CLARIFY and self.failed_clarifications >= 2:
            return ConfidenceDecision(ConfidenceAction.HANDOFF, "two consecutive failed clarifications")
        return decision
