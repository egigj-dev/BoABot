"""Load local development configuration without overriding deployment settings."""

from __future__ import annotations

import os
import re
from pathlib import Path

_FEATURE_FLAGS = ("BOABOT_COMPARISON_STRUCTURED", "BOABOT_LLM_ROUTER", "BOABOT_LLM_ANSWERABILITY")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _value(raw: str) -> str:
    """Parse the conventional dotenv syntax used by this app."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    # An unquoted `` # comment`` is a dotenv comment; hashes in a value remain valid.
    return value.split(" #", 1)[0].rstrip()


def load_project_env(env_path: Path | None = None) -> None:
    """Load the repository ``.env`` if it exists.

    Explicit process variables always take precedence, keeping Docker, systemd,
    and other production deployments unchanged. A missing local file is normal.
    """
    path = env_path or Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if separator and _KEY.fullmatch(key):
            os.environ.setdefault(key, _value(raw_value))


def feature_flag_states() -> dict[str, bool]:
    """Return the effective state of behavior-changing, non-secret flags."""
    return {
        name: os.environ.get(name, "").strip().casefold() in _TRUE_VALUES
        for name in _FEATURE_FLAGS
    }
