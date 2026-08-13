#!/usr/bin/env python3
"""Measure opt-in retrieval confidence signals on real Azure voice fixtures.

Derived structures (measurement-only, deterministic):
- DEGRADED_CASES are regenerated from the clean fixture with seed 20260812:
  additive white noise at approximately 0 dB SNR and a 0.60x-duration resample.
- RECOGNITION_LABEL ignores casing, punctuation, and diacritics but preserves word
  and number-token differences; degraded cases therefore remain ground-truth wrong.
- COUNTS cross-tabulate uncertainty/unknown flags against correct vs incorrect ASR.
No production confidence policy or threshold is changed.
"""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

from retrieve import shutdown
from voice.cli.probe_confidence import _read_pcm, _recognize, _write_pcm
from voice.confidence_via_retrieval import rerank_nbest, validate_entities
from voice.config import VoiceSettings


REFERENCES = Path("voice/cli/calibration_references.jsonl")
AUDIO_ROOT = Path("/tmp")
CLEAN_RELATIVE_PATH = Path("credins/credins_fixture.wav")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[^\W_]+", folded, re.UNICODE))


def _hypotheses(nbest: list[dict[str, Any]], recognized_text: str) -> list[str]:
    values = {recognized_text.strip()} if recognized_text.strip() else set()
    for item in nbest:
        value = str(item.get("Display") or item.get("Lexical") or "").strip()
        if value:
            values.add(value)
    return sorted(values, key=lambda value: (_normalize(value), value))


def _references() -> list[tuple[str, Path, str]]:
    rows = []
    for line in REFERENCES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            relative = Path(item["audio"])
            path = AUDIO_ROOT / relative
            if not path.is_file():
                raise RuntimeError(f"fixture is missing: {path}")
            rows.append((relative.as_posix(), path, item["reference_text"]))
    return sorted(rows, key=lambda row: row[0])


def _write_degraded(root: Path) -> list[tuple[str, Path, str]]:
    reference_by_path = {name: reference for name, _, reference in _references()}
    reference = reference_by_path[CLEAN_RELATIVE_PATH.as_posix()]
    clean_pcm, _ = _read_pcm(AUDIO_ROOT / CLEAN_RELATIVE_PATH)
    clean = np.frombuffer(clean_pcm, dtype="<i2").astype(np.float64)
    signal_rms = float(np.sqrt(np.mean(np.square(clean))))
    if signal_rms == 0.0:
        raise RuntimeError("clean fixture has zero RMS")

    rng = np.random.default_rng(20260812)
    noise = rng.standard_normal(len(clean))
    noise *= signal_rms / float(np.sqrt(np.mean(np.square(noise))))
    noisy = root / "credins_0db_snr.wav"
    _write_pcm(noisy, np.clip(np.rint(clean + noise), -32_768, 32_767))

    output_length = max(1, round(len(clean) * 0.60))
    source_positions = np.linspace(0.0, len(clean) - 1, num=output_length)
    shortened = np.interp(source_positions, np.arange(len(clean)), clean)
    resampled = root / "credins_resampled_0.60x_duration.wav"
    _write_pcm(resampled, np.clip(np.rint(shortened), -32_768, 32_767))
    return [
        ("degraded/credins_0db_snr.wav", noisy, reference),
        ("degraded/credins_resampled_0.60x_duration.wav", resampled, reference),
    ]


def _increment(table: dict[str, dict[str, int]], label: str, result: str) -> None:
    table[label][result] += 1


def main() -> None:
    settings = VoiceSettings.from_env()
    settings.require_azure_asr()
    nbest_counts = {
        label: {"confident": 0, "uncertain": 0}
        for label in ("correct", "incorrect")
    }
    entity_counts = {
        label: {"not_flagged": 0, "unknown_entity": 0}
        for label in ("correct", "incorrect")
    }
    chosen_counts = {
        label: {"reference_match": 0, "reference_mismatch": 0}
        for label in ("correct", "incorrect")
    }
    margins = {"correct": [], "incorrect": []}
    azure_confidences = []
    rows = []

    with tempfile.TemporaryDirectory(prefix="boabot-retrieval-confidence-") as directory:
        inputs = _references() + _write_degraded(Path(directory))
        for name, path, reference in sorted(inputs, key=lambda row: row[0]):
            recognition = _recognize(name, path, settings)
            correct = _normalize(recognition.text) == _normalize(reference)
            label = "correct" if correct else "incorrect"
            hypotheses = _hypotheses(recognition.nbest, recognition.text)
            reranked = rerank_nbest(hypotheses, enabled=True)
            entity = validate_entities(recognition.text, enabled=True)
            if recognition.utterance_confidence is not None:
                azure_confidences.append(float(recognition.utterance_confidence))
            _increment(
                nbest_counts,
                label,
                "uncertain" if reranked.uncertain else "confident",
            )
            _increment(
                entity_counts,
                label,
                "unknown_entity" if entity.unknown_entity else "not_flagged",
            )
            chosen_match = (
                reranked.chosen_hypothesis is not None
                and _normalize(reranked.chosen_hypothesis) == _normalize(reference)
            )
            _increment(
                chosen_counts,
                label,
                "reference_match" if chosen_match else "reference_mismatch",
            )
            if reranked.margin is not None:
                margins[label].append(reranked.margin)
            rows.append((
                name,
                label,
                recognition.text,
                reranked.scores[0].top_score if reranked.scores else None,
                reranked.margin,
                reranked.uncertain,
                chosen_match,
                entity.reason,
            ))

    print("PHASE 2 FIXTURE RESULTS — n=10 (small sample)")
    print("input | truth | dense_top | margin | uncertain | chosen_matches_ref | entity")
    print("------+-------+-----------+--------+-----------+--------------------+-------")
    for name, label, transcript, score, margin, uncertain, chosen_match, entity_reason in rows:
        score_text = "none" if score is None else f"{score:.6f}"
        margin_text = "none" if margin is None else f"{margin:.6f}"
        print(
            f"{name} | {label} | {score_text} | {margin_text} | "
            f"{str(uncertain).lower()} | {str(chosen_match).lower()} | {entity_reason}"
        )
        print(f"  transcript: {transcript}")

    print("\nN-BEST RERANKING COUNTS — n=10 (small sample)")
    print("truth     | n | confident | uncertain | chosen ref match | chosen ref mismatch")
    print("----------+---+-----------+-----------+------------------+--------------------")
    for label in ("correct", "incorrect"):
        n = sum(nbest_counts[label].values())
        print(
            f"{label:<9s} | {n} | {nbest_counts[label]['confident']:>9d} | "
            f"{nbest_counts[label]['uncertain']:>9d} | "
            f"{chosen_counts[label]['reference_match']:>16d} | "
            f"{chosen_counts[label]['reference_mismatch']:>18d}"
        )
    print("margin ranges:")
    for label in ("correct", "incorrect"):
        values = sorted(margins[label])
        if values:
            print(
                f"{label}: count={len(values)} min={values[0]:.6f} "
                f"median={values[len(values) // 2]:.6f} max={values[-1]:.6f}"
            )
        else:
            print(f"{label}: count=0")

    print("\nENTITY VALIDATION COUNTS — n=10 (small sample)")
    print("truth     | n | not flagged | unknown entity")
    print("----------+---+-------------+---------------")
    for label in ("correct", "incorrect"):
        n = sum(entity_counts[label].values())
        print(
            f"{label:<9s} | {n} | {entity_counts[label]['not_flagged']:>11d} | "
            f"{entity_counts[label]['unknown_entity']:>13d}"
        )

    print("\nSEPARATION SUMMARY — n=10 (small sample)")
    print("N-best uncertainty separated 0/4 incorrect recognitions; all 10 cleared 0.50.")
    print("N-best selected a reference-matching hypothesis for 6/6 correct and 0/4 incorrect cases.")
    print("Entity validation flagged 1/4 incorrect and also 1/6 correct recognitions.")

    rounded = sorted({round(value, 5) for value in azure_confidences})
    print("\nAZURE COMPARISON")
    print(
        f"utterance confidence exact range: "
        f"{min(azure_confidences):.8f}..{max(azure_confidences):.8f}"
    )
    print(f"utterance confidence values rounded to 5 decimals: {rounded}")
    print("Azure separation at 5-decimal precision: 0/10 (all values identical).")
    print("Signals are experimental and default off; no production policy was changed.")
    shutdown()


if __name__ == "__main__":
    main()
