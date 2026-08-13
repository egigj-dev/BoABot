"""Measure real Azure ASR confidence against known-correct WAV references."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import unicodedata
import wave
from collections.abc import AsyncIterator
from itertools import zip_longest
from pathlib import Path
from typing import Any

from voice.asr.azure_adapter import AzureStreamingASR
from voice.config import VoiceSettings
from voice.events import Transcript
from voice.schema1 import CRITICAL_RE, ConfidenceAction, ConfidencePolicy


PERCENTILES = (
    ("min", 0.0),
    ("p05", 0.05),
    ("p25", 0.25),
    ("p50", 0.50),
    ("p75", 0.75),
    ("p95", 0.95),
    ("max", 1.0),
)
PROBE_RESULT_PATH = Path("/tmp/boabot_confidence_probe_result.json")


def _strip_token_punctuation(token: str) -> str:
    """Strip boundary punctuation while preserving numeric formatting and percent signs."""
    characters = list(token)

    def removable(index: int) -> bool:
        character = characters[index]
        if character == "%" or not unicodedata.category(character).startswith("P"):
            return False
        if character in {".", ","}:
            previous_is_digit = index > 0 and characters[index - 1].isdigit()
            next_is_digit = index + 1 < len(characters) and characters[index + 1].isdigit()
            if previous_is_digit and next_is_digit:
                return False
        return True

    start = 0
    end = len(characters)
    while start < end and removable(start):
        start += 1
    while end > start and removable(end - 1):
        end -= 1
    return "".join(characters[start:end])


def _normalized_tokens(text: str) -> list[str]:
    return [
        normalized
        for token in text.split()
        if (normalized := _strip_token_punctuation(token).casefold())
    ]


def _normalize_text(text: str) -> str:
    return " ".join(_normalized_tokens(text))


def _first_token_mismatch(reference_text: str, transcript_text: str) -> dict[str, Any] | None:
    reference_tokens = reference_text.split()
    transcript_tokens = transcript_text.split()
    for index, (reference_token, transcript_token) in enumerate(
        zip_longest(reference_tokens, transcript_tokens)
    ):
        if reference_token != transcript_token:
            return {
                "token_index": index,
                "reference_token": reference_token,
                "transcript_token": transcript_token,
                "normalized_reference_token": (
                    _strip_token_punctuation(reference_token).casefold()
                    if reference_token is not None
                    else None
                ),
                "normalized_transcript_token": (
                    _strip_token_punctuation(transcript_token).casefold()
                    if transcript_token is not None
                    else None
                ),
            }
    if reference_text != transcript_text:
        return {
            "token_index": None,
            "reference_token": None,
            "transcript_token": None,
            "normalized_reference_token": None,
            "normalized_transcript_token": None,
            "difference": "whitespace-only",
        }
    return None


def _normalized_span_in_reference(span: str, reference_text: str) -> bool:
    span_tokens = _normalized_tokens(span)
    reference_tokens = _normalized_tokens(reference_text)
    if not span_tokens:
        return False
    width = len(span_tokens)
    return any(
        reference_tokens[index:index + width] == span_tokens
        for index in range(len(reference_tokens) - width + 1)
    )


def _probe_signal_did_not_vary() -> bool:
    try:
        result = json.loads(PROBE_RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return result.get("all_six_utterance_confidences_identical") is True


def _nearest_rank(values: list[float], percentile: float) -> float:
    """Use the same nearest-rank rule as voice.metrics."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _distribution(values: list[float]) -> dict[str, int | float]:
    return {
        "count": len(values),
        **{name: _nearest_rank(values, percentile) for name, percentile in PERCENTILES},
    }


def _read_references(audio_dir: Path, references_path: Path) -> list[tuple[Path, str]]:
    if not audio_dir.is_dir():
        raise RuntimeError(f"audio directory does not exist: {audio_dir}")
    records: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for line_number, raw_line in enumerate(
        references_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
            relative_audio = item["audio"]
            reference_text = item["reference_text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"invalid reference JSONL at line {line_number}: {exc}"
            ) from exc
        if not isinstance(relative_audio, str) or not isinstance(reference_text, str):
            raise RuntimeError(
                f"reference line {line_number} requires string audio and reference_text"
            )
        audio_path = audio_dir / relative_audio
        if audio_path.suffix.lower() != ".wav" or not audio_path.is_file():
            raise RuntimeError(f"reference WAV does not exist: {audio_path}")
        resolved = audio_path.resolve()
        if resolved in seen:
            raise RuntimeError(f"duplicate reference WAV: {audio_path}")
        seen.add(resolved)
        records.append((audio_path, reference_text))
    if not records:
        raise RuntimeError("reference JSONL contains no fixtures")
    return records


def _read_pcm(audio_path: Path) -> bytes:
    try:
        with wave.open(str(audio_path), "rb") as wav:
            audio_format = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            )
            pcm = wav.readframes(wav.getnframes())
    except (EOFError, OSError, wave.Error) as exc:
        raise RuntimeError(f"invalid input WAV {audio_path}: {exc}") from exc
    if audio_format != (1, 2, 16_000, "NONE"):
        raise RuntimeError(
            f"input must be 16 kHz, 16-bit, mono PCM WAV; got {audio_format!r}: {audio_path}"
        )
    if not pcm:
        raise RuntimeError(f"input WAV contains no audio frames: {audio_path}")
    return pcm


async def _frames(pcm: bytes, chunk_bytes: int = 3_200) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), chunk_bytes):
        yield pcm[offset:offset + chunk_bytes]
        await asyncio.sleep(0)


async def _recognize(audio_path: Path, settings: VoiceSettings) -> Transcript:
    asr = AzureStreamingASR(settings)
    assert isinstance(asr, AzureStreamingASR), "calibration ASR is not real AzureStreamingASR"
    final_transcripts: list[Transcript] = []
    async with asyncio.timeout(45):
        async for transcript in asr.start(_frames(_read_pcm(audio_path))):
            if transcript.final and transcript.text.strip():
                final_transcripts.append(transcript)
    if len(final_transcripts) != 1:
        raise RuntimeError(
            f"Azure ASR must return exactly one non-empty final transcript; "
            f"got {len(final_transcripts)} for {audio_path}"
        )
    return final_transcripts[0]


def _format_value(value: int | float) -> str:
    return str(value) if isinstance(value, int) else f"{value:.8f}"


def _print_distribution_table(distributions: dict[str, dict[str, dict[str, int | float]]]) -> None:
    headings = ("signal", "recognition", "count", *(name for name, _ in PERCENTILES))
    rows: list[tuple[str, ...]] = []
    for signal in ("per_span", "utterance"):
        for recognition in ("correct", "incorrect"):
            values = distributions[signal][recognition]
            rows.append(
                (
                    signal,
                    recognition,
                    _format_value(values["count"]),
                    *(_format_value(values[name]) for name, _ in PERCENTILES),
                )
            )
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        for index in range(len(headings))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headings)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


async def calibrate(
    audio_dir: Path, references_path: Path, output_path: Path, settings: VoiceSettings
) -> dict[str, Any]:
    references = _read_references(audio_dir, references_path)
    policy = ConfidencePolicy(
        settings.confidence_proceed,
        settings.confidence_critical,
        settings.confidence_handoff,
    )
    fixtures: list[dict[str, Any]] = []
    span_confidences: dict[str, list[float]] = {"correct": [], "incorrect": []}
    utterance_confidences: dict[str, list[float]] = {"correct": [], "incorrect": []}
    threshold_outcomes = {
        "correct-and-passed": 0,
        "correct-but-blocked": 0,
        "incorrect-but-passed": 0,
        "incorrect-and-blocked": 0,
    }
    exact_match_count = 0
    normalized_match_count = 0
    match_disagreements: list[dict[str, Any]] = []

    for audio_path, reference_text in references:
        transcript = await _recognize(audio_path, settings)
        critical_spans = CRITICAL_RE.findall(transcript.text)
        assert all(
            key in critical_spans for key in transcript.critical_confidences
        ), "critical confidence key is not a CRITICAL_RE.findall member"
        span_results = []
        for span in critical_spans:
            exact_correct = span in reference_text
            normalized_correct = _normalized_span_in_reference(span, reference_text)
            confidence = transcript.critical_confidences.get(span)
            span_results.append(
                {
                    "span": span,
                    "confidence": confidence,
                    "exact_correct": exact_correct,
                    "normalized_correct": normalized_correct,
                }
            )
            if confidence is not None:
                span_confidences[
                    "correct" if normalized_correct else "incorrect"
                ].append(confidence)

        exact_match = transcript.text == reference_text
        normalized_match = _normalize_text(transcript.text) == _normalize_text(reference_text)
        first_token_mismatch = _first_token_mismatch(reference_text, transcript.text)
        exact_match_count += int(exact_match)
        normalized_match_count += int(normalized_match)
        if exact_match != normalized_match:
            match_disagreements.append(
                {
                    "audio_path": str(audio_path.resolve()),
                    "reference_text": reference_text,
                    "transcript_text": transcript.text,
                    "exact_match": exact_match,
                    "normalized_match": normalized_match,
                    "first_token_mismatch": first_token_mismatch,
                }
            )
        if transcript.confidence is not None:
            utterance_confidences["correct" if normalized_match else "incorrect"].append(
                transcript.confidence
            )
        decision = policy.evaluate(transcript)
        passed = decision.action is ConfidenceAction.PROCEED
        if normalized_match:
            outcome_key = "correct-and-passed" if passed else "correct-but-blocked"
        else:
            outcome_key = "incorrect-but-passed" if passed else "incorrect-and-blocked"
        threshold_outcomes[outcome_key] += 1

        fixture = {
            "audio_path": str(audio_path.resolve()),
            "reference_text": reference_text,
            "transcript_text": transcript.text,
            "exact_match": exact_match,
            "normalized_match": normalized_match,
            "first_token_mismatch": first_token_mismatch,
            "utterance_confidence": transcript.confidence,
            "critical_spans": critical_spans,
            "critical_confidences": transcript.critical_confidences,
            "per_span_exact_correct": all(
                result["exact_correct"] for result in span_results
            ),
            "per_span_correct": all(
                result["normalized_correct"] for result in span_results
            ),
            "span_results": span_results,
            "confidence_action": decision.action.value,
            "confidence_reason": decision.reason,
        }
        fixtures.append(fixture)
        print(f"fixture: {audio_path}")
        print(f"reference_text: {reference_text}")
        print(f"transcript_text: {transcript.text}")
        print(f"exact_match: {exact_match}")
        print(f"normalized_match: {normalized_match}")
        print(
            "first_token_mismatch: "
            + json.dumps(first_token_mismatch, ensure_ascii=False, sort_keys=True)
        )
        print(f"action: {decision.action.value}")
        print()

    distributions = {
        "per_span": {
            recognition: _distribution(values)
            for recognition, values in span_confidences.items()
        },
        "utterance": {
            recognition: _distribution(values)
            for recognition, values in utterance_confidences.items()
        },
    }
    calibration = {
        "fixture_count": len(fixtures),
        "exact_match_count": exact_match_count,
        "normalized_match_count": normalized_match_count,
        "exact_normalized_disagreements": match_disagreements,
        "recognition_label": "normalized_match",
        "fixtures": fixtures,
        "distributions": distributions,
        "current_thresholds": {
            "confidence_proceed": settings.confidence_proceed,
            "confidence_critical": settings.confidence_critical,
            "confidence_handoff": settings.confidence_handoff,
        },
        "threshold_outcomes": threshold_outcomes,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    signal_did_not_vary = _probe_signal_did_not_vary()
    if signal_did_not_vary:
        print(
            "SIGNAL DID NOT VARY ACROSS INPUTS; "
            "DISTRIBUTIONS ARE THEREFORE UNINFORMATIVE."
        )
    print("DISTRIBUTIONS (correct/incorrect label: normalized_match)")
    _print_distribution_table(distributions)
    print()
    print(f"exact_match count: {exact_match_count} out of {len(fixtures)}")
    print(f"normalized_match count: {normalized_match_count} out of {len(fixtures)}")
    print("exact_match/normalized_match disagreements:")
    if not match_disagreements:
        print("none")
    for disagreement in match_disagreements:
        print(json.dumps(disagreement, ensure_ascii=False, sort_keys=True))
    print()
    print(
        "current thresholds: "
        f"proceed={settings.confidence_proceed}, critical={settings.confidence_critical}, "
        f"handoff={settings.confidence_handoff}"
    )
    for label, count in threshold_outcomes.items():
        print(f"{label}: {count}")
    print(f"fixture_count: {len(fixtures)}")
    if len(fixtures) < 10:
        print("fewer than 10 fixtures: distributions are not yet meaningful")
    print(f"calibration.json: {output_path}")
    return calibration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", "--audio-dir", dest="audio_dir", type=Path, required=True)
    parser.add_argument("--refs", "--references", dest="references", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("calibration.json"))
    args = parser.parse_args()
    asyncio.run(
        calibrate(args.audio_dir, args.references, args.out, VoiceSettings.from_env())
    )


if __name__ == "__main__":
    main()
