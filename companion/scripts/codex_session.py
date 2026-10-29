"""Project-owned Codex configuration and subscription-only subprocess context."""

import os
from pathlib import Path


def clean_environment(home):
    directory = Path(home) / "codex"
    directory.mkdir(parents=True, exist_ok=True)
    # This belongs to the application, never ~/.codex. Do not import a global
    # provider/profile/base URL or change the desktop client's login state.
    config = directory / "config.toml"
    expected = 'model_provider = "openai"\n'
    if not config.exists() or config.read_text(encoding="utf-8") != expected:
        config.write_text(expected, encoding="utf-8")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(directory)
    for key in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "CHATGPT_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    ):
        env.pop(key, None)
    return env
