"""Probe raw Azure ASR confidence over deliberately varied real audio inputs."""

from __future__ import annotations

import json
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from voice.config import VoiceSettings
from voice.phrases import build_phrase_list


CREDINS_FIXTURE = Path("/tmp/credins/credins_fixture.wav")
PROBE_RESULT_PATH = Path("/tmp/boabot_confidence_probe_result.json")


@dataclass(frozen=True)
class ProbeResult:
    name: str
    reason: str
    text: str
    raw_json: str
    nbest: list[dict[str, Any]]

    @property
    def utterance_confidence(self) -> Any:
        return self.nbest[0].get("Confidence") if self.nbest else None

    @property
    def words(self) -> list[Any]:
        words = self.nbest[0].get("Words") if self.nbest else []
        return words if isinstance(words, list) else []


def _read_pcm(path: Path) -> tuple[bytes, int]:
    try:
        with wave.open(str(path), "rb") as wav:
            audio_format = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            )
            frame_count = wav.getnframes()
            pcm = wav.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as exc:
        raise RuntimeError(f"invalid WAV {path}: {exc}") from exc
    if audio_format != (1, 2, 16_000, "NONE"):
        raise RuntimeError(
            f"input must be 16 kHz, 16-bit, mono PCM WAV; got {audio_format!r}: {path}"
        )
    if not pcm:
        raise RuntimeError(f"input WAV contains no audio: {path}")
    return pcm, frame_count


def _write_pcm(path: Path, samples: np.ndarray) -> None:
    pcm = np.asarray(samples, dtype="<i2").tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)


def _synthesize(path: Path, text: str, settings: VoiceSettings) -> None:
    import azure.cognitiveservices.speech as speechsdk

    credentials = settings.require_azure_tts()
    config = speechsdk.SpeechConfig(
        credentials["AZURE_TTS_KEY"], credentials["AZURE_TTS_REGION"]
    )
    config.speech_synthesis_voice_name = settings.azure_tts_voice
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(path))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=config, audio_config=audio_config
    )
    result = synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = speechsdk.SpeechSynthesisCancellationDetails(result)
        raise RuntimeError(
            f"Azure TTS failed for {text!r}: reason={result.reason}; "
            f"cancellation_reason={details.reason}; error_code={details.error_code}; "
            f"details={details.error_details}"
        )
    _read_pcm(path)


def _recognize(name: str, path: Path, settings: VoiceSettings) -> ProbeResult:
    import azure.cognitiveservices.speech as speechsdk

    credentials = settings.require_azure_asr()
    config = speechsdk.SpeechConfig(
        credentials["AZURE_SPEECH_KEY"], credentials["AZURE_SPEECH_REGION"]
    )
    config.speech_recognition_language = "sq-AL"
    config.output_format = speechsdk.OutputFormat.Detailed
    config.request_word_level_timestamps()
    audio_config = speechsdk.audio.AudioConfig(filename=str(path))
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=config, audio_config=audio_config
    )
    grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
    for phrase in build_phrase_list():
        grammar.addPhrase(phrase)
    grammar.setWeight(2.0)
    result = recognizer.recognize_once_async().get()
    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.CancellationDetails(result)
        raise RuntimeError(
            f"Azure ASR canceled for {name}: reason={details.reason}; "
            f"error_code={details.code}; details={details.error_details}"
        )
    if result.reason not in {
        speechsdk.ResultReason.RecognizedSpeech,
        speechsdk.ResultReason.NoMatch,
    }:
        raise RuntimeError(f"Azure ASR returned unexpected reason for {name}: {result.reason}")

    properties = result.properties
    property_id = speechsdk.PropertyId.SpeechServiceResponse_JsonResult
    raw_json = (
        properties.get_property(property_id)
        if hasattr(properties, "get_property")
        else properties.get(property_id)
    )
    if not isinstance(raw_json, str) or not raw_json:
        raise RuntimeError(f"Azure detailed-result JSON is absent for {name}")
    try:
        detail = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Azure detailed-result JSON is invalid for {name}: {exc}") from exc
    nbest = detail.get("NBest") or []
    if not isinstance(nbest, list) or not all(isinstance(item, dict) for item in nbest):
        raise RuntimeError(f"Azure NBest has an unexpected shape for {name}: {nbest!r}")
    return ProbeResult(name, str(result.reason), str(result.text or ""), raw_json, nbest)


def _distinct_word_confidences(result: ProbeResult) -> list[str]:
    return sorted(
        {repr(word.get("Confidence")) for word in result.words if "Confidence" in word}
    )


def _print_result(result: ProbeResult) -> None:
    print(f"=== input: {result.name} ===")
    print("raw_json:")
    print(result.raw_json)
    print(f"utt_confidence_repr: {repr(result.utterance_confidence)}")
    print(
        "NBest[0][\"Words\"]: "
        + json.dumps(result.words, ensure_ascii=False, separators=(",", ":"))
    )
    print(f"nbest_len: {len(result.nbest)}")
    for index, item in enumerate(result.nbest):
        print(f"NBest[{index}][\"Confidence\"]: {repr(item.get('Confidence'))}")
    print(f"recognized_text: {result.text}")
    print(f"ResultReason: {result.reason}")
    print()


def _print_verdict(results: list[ProbeResult]) -> bool:
    headings = (
        "input",
        "reason",
        "text",
        "nbest_len",
        "utt_confidence_repr",
        "distinct_word_confidence_values",
    )
    rows = [
        (
            result.name,
            result.reason,
            result.text,
            str(len(result.nbest)),
            repr(result.utterance_confidence),
            json.dumps(_distinct_word_confidences(result), ensure_ascii=False),
        )
        for result in results
    ]
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        for index in range(len(headings))
    ]
    print("VERDICT TABLE")
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headings)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))

    values = [result.utterance_confidence for result in results]
    identical = all(value == values[0] for value in values[1:])
    print()
    print(
        "GATE A: utt_confidence_repr was "
        + ("IDENTICAL" if identical else "NOT IDENTICAL")
        + " across all six inputs."
    )
    by_name = {result.name: result for result in results}
    clean_value = by_name["credins_clean"].utterance_confidence
    silence_same = by_name["silence_2s"].utterance_confidence == clean_value
    noise_same = by_name["credins_0db_snr"].utterance_confidence == clean_value
    print(
        "Silence returned "
        + ("the same" if silence_same else "a different")
        + " utterance confidence value as clean speech: "
        + f"{repr(by_name['silence_2s'].utterance_confidence)} vs {repr(clean_value)}."
    )
    print(
        "0 dB SNR audio returned "
        + ("the same" if noise_same else "a different")
        + " utterance confidence value as clean speech: "
        + f"{repr(by_name['credins_0db_snr'].utterance_confidence)} vs {repr(clean_value)}."
    )
    return identical


def main() -> None:
    if not CREDINS_FIXTURE.is_file():
        raise RuntimeError(f"Credins fixture does not exist: {CREDINS_FIXTURE}")
    settings = VoiceSettings.from_env()
    settings.require_azure_asr()
    settings.require_azure_tts()

    with tempfile.TemporaryDirectory(prefix="boabot-confidence-probe-") as temp_dir:
        root = Path(temp_dir)
        different = root / "different_sentence.wav"
        short = root / "short_utterance.wav"
        silence = root / "silence.wav"
        noisy = root / "credins_0db_snr.wav"
        resampled = root / "credins_resampled.wav"

        _synthesize(
            different,
            "Sot po planifikojmë një udhëtim të gjatë nëpër malet e Shqipërisë.",
            settings,
        )
        _synthesize(short, "Ku jeni?", settings)
        _write_pcm(silence, np.zeros(2 * 16_000, dtype=np.int16))

        clean_pcm, _ = _read_pcm(CREDINS_FIXTURE)
        clean = np.frombuffer(clean_pcm, dtype="<i2").astype(np.float64)
        signal_rms = float(np.sqrt(np.mean(np.square(clean))))
        if signal_rms == 0.0:
            raise RuntimeError("Credins fixture has zero RMS")
        rng = np.random.default_rng(20260812)
        noise = rng.standard_normal(len(clean))
        noise *= signal_rms / float(np.sqrt(np.mean(np.square(noise))))
        _write_pcm(noisy, np.clip(np.rint(clean + noise), -32_768, 32_767))

        output_length = max(1, round(len(clean) * 0.60))
        source_positions = np.linspace(0.0, len(clean) - 1, num=output_length)
        stretched = np.interp(source_positions, np.arange(len(clean)), clean)
        _write_pcm(resampled, np.clip(np.rint(stretched), -32_768, 32_767))

        inputs = (
            ("credins_clean", CREDINS_FIXTURE),
            ("different_sentence", different),
            ("short_utterance", short),
            ("silence_2s", silence),
            ("credins_0db_snr", noisy),
            ("credins_resampled_0.60x_duration", resampled),
        )
        results = [_recognize(name, path, settings) for name, path in inputs]

    for result in results:
        _print_result(result)
    identical = _print_verdict(results)
    PROBE_RESULT_PATH.write_text(
        json.dumps(
            {
                "all_six_utterance_confidences_identical": identical,
                "utterance_confidence_repr": {
                    result.name: repr(result.utterance_confidence) for result in results
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
