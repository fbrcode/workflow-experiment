"""The two external APIs: Gemini (SUM-5) and the Telegram Bot API (SUM-7b, SND-3).

Both take their base URL from configuration, so they can point at the mocks (D-004). Neither
retries: google-genai's sync client makes one attempt unless `HttpOptions.retry_options` is set,
and retries inside the model client aren't part of parity (D-019).
"""

from __future__ import annotations

from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from email_summarizer.summarize import SYSTEM_MESSAGE


class TelegramError(Exception):
    """A failed `sendMessage`. The message is Telegram's `description` when there is one (SND-5)."""


def generate(*, base_url: str, api_key: str, model: str, prompt: str) -> str:
    """One `generateContent` call with n8n's Agent system message (Q-13) and no sampling options."""
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(base_url=base_url, api_version="v1beta"))
    response = client.models.generate_content(
        model=model.removeprefix("models/"),  # D-011: the prefix is kept in config, stripped for the API
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_MESSAGE),
    )
    return response.text or ""


def error_message(error: BaseException) -> str:
    """The text n8n would put in `error`: the API's own message when there is one."""
    if isinstance(error, errors.APIError) and error.message:
        return error.message
    return str(error) or type(error).__name__


def send_message(*, base_url: str, token: str, chat_id: str, text: str, parse_mode: str) -> dict[str, Any]:
    """`sendMessage` as n8n's Telegram node v1.2 sends it, including `disable_web_page_preview`."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    response = httpx.post(f"{base_url}/bot{token}/sendMessage", json=payload)
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.is_success and isinstance(body, dict) and body.get("ok"):
        return body
    if isinstance(body, dict) and body.get("description"):
        raise TelegramError(body["description"])
    raise TelegramError(f"HTTP {response.status_code} from Telegram sendMessage")
