"""Record one microphone utterance and run the real single-turn voice pipeline."""

from __future__ import annotations

import argparse
import asyncio
import platform
import shutil
import subprocess
import wave
from pathlib import Path

from voice.cli.live_run import run_single
from voice.config import VoiceSettings


class RecordingError(RuntimeError):
    """Raised when a real microphone recording cannot be made safely."""


def _linux_has_capture_device() -> bool:
    snd = Path("/dev/snd")
    return snd.is_dir() and any(snd.glob("pcm*C*c"))


def _input_args(input_format: str | None, input_device: str | None) -> list[str]:
    if (input_format is None) != (input_device is None):
        raise RecordingError("--input-format and --input-device must be provided together")
    if input_format is not None and input_device is not None:
        return ["-f", input_format, "-i", input_device]

    system = platform.system()
    if system == "Linux":
        if not _linux_has_capture_device():
            raise RecordingError("no audio input device found")
        return ["-f", "alsa", "-i", "default"]
    if system == "Darwin":
        return ["-f", "avfoundation", "-i", ":0"]
    if system == "Windows":
        return ["-f", "dshow", "-i", "audio=default"]
    raise RecordingError(
        f"no audio input device found: automatic microphone selection is unsupported on {system}"
    )


def _validate_recording(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as wav:
            actual = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            )
            frames = wav.getnframes()
    except (EOFError, OSError, wave.Error) as exc:
        raise RecordingError(f"ffmpeg did not create a valid WAV recording: {exc}") from exc
    expected = (1, 2, 16_000, "NONE")
    if actual != expected or frames <= 0:
        raise RecordingError(
            "recording is not a non-empty 16 kHz / 16-bit / mono PCM WAV; "
            f"got channels={actual[0]}, sample_width={actual[1]}, "
            f"sample_rate={actual[2]}, compression={actual[3]}, frames={frames}"
        )


def _record(
    path: Path,
    seconds: float,
    input_format: str | None,
    input_device: str | None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RecordingError("ffmpeg was not found on PATH")
    device_args = _input_args(input_format, input_device)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *device_args,
        "-t",
        str(seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    print(f"Recording for {seconds:g} seconds. Speak now...")
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown ffmpeg error"
        raise RecordingError(f"microphone recording failed: {detail}")
    _validate_recording(path)
    print(f"recording: {path.resolve()}")


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="record a real microphone utterance")
    parser.add_argument(
        "--seconds",
        type=_positive_seconds,
        default=10.0,
        help="recording length (default: 10)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/boabot-human-voice"),
        help="recording and run output directory",
    )
    parser.add_argument("--input-format", help="advanced: ffmpeg input format override")
    parser.add_argument("--input-device", help="advanced: matching ffmpeg input device override")
    args = parser.parse_args()
    if not args.record:
        parser.error("--record is required; this helper never uses prerecorded or fake input")

    recording_path = args.out / "recording.wav"
    try:
        _record(recording_path, args.seconds, args.input_format, args.input_device)
        manifest = asyncio.run(run_single(recording_path, args.out, VoiceSettings.from_env()))
    except RecordingError as exc:
        parser.exit(2, f"record_and_run: error: {exc}\n")

    answer_path = (args.out / "answer.wav").resolve()
    print(f"ASR transcript: {manifest['transcript_text']}")
    print(f"outcome: {manifest['outcome']}")
    print(f"answer.wav: {answer_path}")


if __name__ == "__main__":
    main()
