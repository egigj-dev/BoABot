"""Azure-versus-Chirp recorded-audio bake-off table scaffold (Schema 1 §8)."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from voice.config import VoiceSettings


@dataclass(frozen=True, slots=True)
class Score:
    provider: str
    fixture: str
    wer: str
    entity_accuracy: str
    final_latency_ms: float
    mode: str


def render(scores: list[Score]) -> str:
    rows = ["provider | fixture | WER | entity accuracy | final latency ms | mode",
            "--- | --- | ---: | ---: | ---: | ---"]
    rows.extend(f"{s.provider} | {s.fixture} | {s.wer} | {s.entity_accuracy} | "
                f"{s.final_latency_ms:.1f} | {s.mode}" for s in scores)
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="*", type=Path, help="recorded WAV/PCM fixtures")
    parser.add_argument("--live", action="store_true", help="require both configured providers")
    args = parser.parse_args()
    settings = VoiceSettings.from_env()
    fixtures = args.audio or [Path("offline_fixture.wav")]
    if args.live:
        settings.require_azure_asr()
        settings.require_chirp()
        missing = [str(path) for path in fixtures if not path.is_file()]
        if missing:
            raise SystemExit(f"missing recorded fixtures: {', '.join(missing)}")
        raise SystemExit("Wire consented reference transcripts before a live scored run")
    started = time.monotonic()
    scores = [Score(provider, path.name, "pending-reference", "pending-reference",
                    (time.monotonic() - started) * 1000, "fake-adapter")
              for path in fixtures for provider in ("azure", "chirp_3")]
    print(render(scores))


if __name__ == "__main__":
    main()
