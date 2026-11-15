"""Settings for the parity suite.

The Makefile sources the env file (`.env`, else `.env.example`) before running pytest, so every
value comes from the same file the stack was started with. The suite talks to the stack through
the host ports in that file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VECTORS = REPO / "tests" / "golden" / "vectors"
FIXTURES = REPO / "fixtures" / "emails"

# What the stack must point at for the expected values to hold (D-004: mocks by default).
MOCK_MODE = {
    "IMAP_HOST": "mail",
    "GEMINI_BASE_URL": "http://mock-gemini:8091",
    "TELEGRAM_BASE_URL": "http://mock-telegram:8090",
}


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set: run the suite through `make parity-<tool>`, which sources the env file")
    return value


@dataclass(frozen=True)
class Settings:
    db: dict[str, str]
    imap_port: int
    mailbox: str
    telegram_url: str
    gemini_url: str
    alert_chat: str
    chats: dict[str, str]
    model: str
    model_fallback: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db={
                "host": "localhost",
                "port": env("APP_DB_HOST_PORT"),
                "dbname": env("APP_DB_NAME"),
                "user": env("APP_DB_USER"),
                "password": env("APP_DB_PASSWORD"),
            },
            imap_port=int(env("MAIL_IMAP_HOST_PORT")),
            mailbox=env("IMAP_MAILBOX"),
            telegram_url=f"http://localhost:{env('MOCK_TELEGRAM_HOST_PORT')}",
            gemini_url=f"http://localhost:{env('MOCK_GEMINI_HOST_PORT')}",
            alert_chat=env("TELEGRAM_ALERT_CHAT_ID"),
            chats={
                "news": env("TELEGRAM_CHAT_ID_NEWS"),
                "finance": env("TELEGRAM_CHAT_ID_FINANCE"),
                "tech": env("TELEGRAM_CHAT_ID_TECH"),
            },
            model=env("GEMINI_MODEL"),
            model_fallback=env("GEMINI_MODEL_FALLBACK"),
        )

    @staticmethod
    def not_in_mock_mode() -> list[str]:
        """Settings that point a stack at a real service instead of the local stand-ins."""
        return [
            f"{name}={os.environ.get(name)!r} (expected {want!r})"
            for name, want in MOCK_MODE.items()
            if os.environ.get(name) != want
        ]
