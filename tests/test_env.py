from __future__ import annotations

import os

from core.env import load_project_env


def test_project_env_loads_dotenv_values_without_overwriting_process_environment(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# local settings\n"
        "BOABOT_DSN=postgresql://boa:boa@127.0.0.1:5433/boa\n"
        "export QUOTED_VALUE=\"has a # character\"\n"
        "PROCESS_VALUE=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BOABOT_DSN", raising=False)
    monkeypatch.delenv("QUOTED_VALUE", raising=False)
    monkeypatch.setenv("PROCESS_VALUE", "from-process")

    load_project_env(env_file)

    assert os.environ["BOABOT_DSN"] == "postgresql://boa:boa@127.0.0.1:5433/boa"
    assert os.environ["QUOTED_VALUE"] == "has a # character"
    assert os.environ["PROCESS_VALUE"] == "from-process"


def test_project_env_allows_a_missing_file(tmp_path):
    load_project_env(tmp_path / ".env")
