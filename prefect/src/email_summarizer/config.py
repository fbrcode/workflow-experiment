"""Where the flows read their configuration at run time (criterion C8), all of it from the Prefect server.

- Credentials are **blocks**, stored encrypted in the server database: a `SqlAlchemyConnector`
  block for app-db (password as a `SecretStr`) and `Secret` blocks for the rest.
- Everything else is a Prefect **variable** (plain JSON, visible in the UI).

`bootstrap.py` writes both from the environment (`.env`) when the stack is deployed, so the
worker container never sees a secret in its environment. Names follow the shared configuration
names in `.env.example` (CLAUDE.md), lowercased.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from prefect.blocks.system import Secret
from prefect.variables import Variable
from prefect_sqlalchemy import SqlAlchemyConnector

APP_DB_BLOCK: Final = "app-db"

# Secret block name -> environment variable it's created from
SECRETS: Final = {
    "imap-password": "IMAP_PASSWORD",
    "gemini-api-key": "GEMINI_API_KEY",
    "telegram-bot-token": "TELEGRAM_BOT_TOKEN",
    "telegram-alert-chat-id": "TELEGRAM_ALERT_CHAT_ID",
}

# Variable name -> (environment variable, type)
VARIABLES: Final[dict[str, tuple[str, type]]] = {
    "imap_host": ("IMAP_HOST", str),
    "imap_port": ("IMAP_PORT", int),
    "imap_secure": ("IMAP_SECURE", bool),
    "imap_user": ("IMAP_USER", str),
    "imap_mailbox": ("IMAP_MAILBOX", str),
    "gemini_base_url": ("GEMINI_BASE_URL", str),
    "gemini_model": ("GEMINI_MODEL", str),
    "gemini_model_fallback": ("GEMINI_MODEL_FALLBACK", str),
    "telegram_base_url": ("TELEGRAM_BASE_URL", str),
}


@dataclass(frozen=True)
class Imap:
    host: str
    port: int
    secure: bool
    user: str
    password: str
    mailbox: str


def variable(name: str) -> Any:
    value = Variable.get(name)
    if value is None:
        raise LookupError(f"Prefect variable {name!r} is not set; run make prefect-deploy")
    return value


def secret(name: str) -> str:
    """The stored string, as saved by bootstrap.py.

    Not `Secret.get()`: that runs `json.loads` on the value, so a chat id such as `-100123` comes
    back as an int (and would be sent to Telegram as a JSON number), and a numeric password too.
    """
    value = Secret.load(name).value.get_secret_value()
    return value if isinstance(value, str) else str(value)


def app_db() -> SqlAlchemyConnector:
    """A fresh connector; use it as a context manager so its connections close with the task."""
    return SqlAlchemyConnector.load(APP_DB_BLOCK)


def imap() -> Imap:
    return Imap(
        host=variable("imap_host"),
        port=variable("imap_port"),
        secure=variable("imap_secure"),
        user=variable("imap_user"),
        password=secret("imap-password"),
        mailbox=variable("imap_mailbox"),
    )
