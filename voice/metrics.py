"""Schema 1/2 §§3–4 in-process latency and invariant metrics."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


@dataclass(slots=True)
class VoiceMetrics:
    """Small offline-safe registry; Redis flushing is explicitly optional."""

    latencies_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    counters: Counter[str] = field(default_factory=Counter)

    def observe(self, stage: str, milliseconds: float) -> None:
        self.latencies_ms[stage].append(max(0.0, milliseconds))

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def outcome(self, value: str, handoff: bool = False) -> None:
        self.increment(f"outcome.{value}")
        if handoff:
            self.increment("handoff")

    def summary(self) -> dict[str, object]:
        histograms = {
            stage: {"count": len(values), "p50": _nearest_rank(values, 0.50),
                    "p95": _nearest_rank(values, 0.95), "p99": _nearest_rank(values, 0.99)}
            for stage, values in sorted(self.latencies_ms.items())
        }
        return {"latency_ms": histograms, "counters": dict(sorted(self.counters.items()))}

    def flush_redis(self, redis_url: str | None,
                    publisher: Callable[[str, str], object] | None = None) -> bool:
        """Flush when explicitly wired; no Redis package or connection is required by default."""
        if not redis_url:
            return False
        payload = json.dumps(self.summary(), ensure_ascii=False)
        if publisher is not None:
            publisher("boabot:voice:metrics", payload)
        else:
            try:
                from redis import Redis  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("redis package is required when VOICE_REDIS_URL is configured") from exc
            client = Redis.from_url(redis_url)
            client.set("boabot:voice:metrics", payload)
        return True
