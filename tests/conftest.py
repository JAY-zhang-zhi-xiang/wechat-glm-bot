"""Shared pytest setup.

``main.py`` calls ``load_dotenv()`` at import time, which copies a developer's
local ``.env`` into ``os.environ``. Several tests assert against a clean
configuration (for example ``scripts/check_config.py`` overlapping process
environment over an env file), so a stray local ``.env`` would silently change
their outcome. This fixture removes every project setting from the process
environment before each test, leaving tests free to set only what they need.
"""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Settings that are read from the environment or an env file. SMOKE_BASE_URL is
# not in .env.example but is honoured by scripts/smoke_wechat.py. The legacy
# provider-specific key aliases are commented out in .env.example, so list them
# explicitly to keep a developer's shell from leaking into tests.
EXTRA_SETTINGS = (
    "SMOKE_BASE_URL",
    "ZHIPU_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
)


def _project_settings():
    keys = set(EXTRA_SETTINGS)
    env_example = ROOT / ".env.example"
    if env_example.exists():
        for raw_line in env_example.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip().lstrip("\ufeff")
            if key:
                keys.add(key)
    return keys


@pytest.fixture(autouse=True)
def isolate_project_environment(monkeypatch):
    for key in _project_settings():
        monkeypatch.delenv(key, raising=False)

    # Drop cached settings so a value from an earlier test cannot leak in.
    main = sys.modules.get("main")
    if main is not None:
        for name in ("get_settings", "get_llm_client", "get_moderation_blocklist"):
            cached = getattr(main, name, None)
            cache_clear = getattr(cached, "cache_clear", None)
            if callable(cache_clear):
                cache_clear()

    yield
