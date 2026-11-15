"""Workflow 2, branch A logic: SUM-3 (transform content), SUM-4 (prompt) and the SUM-7b alert text.

Ported from `baseline/EmailSummarizerFromPgCron.json`: the `transform-content` and
`prompt-builder` Code nodes and the `telegram-send-failed-exec` text expression. The golden vectors
(`sum3-*`, `sum4-*`, `sum7b-*`) are the referee.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from email_summarizer import js

DEFAULT_MODEL: Final = "models/gemini-flash-lite-latest"
DEFAULT_MODEL_FALLBACK: Final = "models/gemini-2.5-flash"
SYSTEM_MESSAGE: Final = "You are a helpful assistant"  # the n8n Agent's default (Q-13)
ALERT_TIMEZONE: Final = ZoneInfo("America/Sao_Paulo")  # D-019

_NAME_BEFORE_ANGLE: Final = re.compile(r"^([^<]+)<")
_NON_PRINTABLE: Final = re.compile(r"[^\x20-\x7e]")
_REMOVED_PHRASES: Final = ("View image: ", "Caption: ", "Follow image link: ")


def transform_content(row: dict[str, Any]) -> dict[str, Any]:
    """SUM-3: display name before `<`, and the content without image phrases or non-ASCII chars."""
    sender = row.get("email_from") or ""
    match = _NAME_BEFORE_ANGLE.match(sender)
    if match:
        sender = js.trim(match.group(1))

    content = row.get("email_raw_content") or ""
    for phrase in _REMOVED_PHRASES:
        content = content.replace(phrase, "")
    content = _NON_PRINTABLE.sub("", content)

    return {
        "news_id": row["id"],
        "from": sender,
        "subject": row.get("email_subject"),
        "date": row.get("email_date"),
        "to_translate": row.get("to_translate"),
        "translate_language": row.get("translate_language"),
        "content": content,
    }


def build_prompt(
    item: dict[str, Any], model: str = DEFAULT_MODEL, model_fallback: str = DEFAULT_MODEL_FALLBACK
) -> dict[str, Any]:
    """SUM-4: the prompt, verbatim, `cntent` typo included (Q-1)."""
    lead = "translated to " if js.truthy(item.get("to_translate")) else "using "
    translate_prompt = f"{lead}{js.string(item.get('translate_language'))} language"
    prompt = (
        "Summarize this email cntent in main topics with content with 2500 chars limit "
        f"{translate_prompt} and remove any sponsored news (presented by) silently. "
        f"Content >> {js.string(item.get('content'))}"
    )
    return {"model": model, "modelFallback": model_fallback, "prompt": prompt}


def now_iso(now: datetime) -> str:
    """Luxon's `$now.toISO()`: local time in the alert time zone, milliseconds, `+hh:mm` offset."""
    local = now.astimezone(ALERT_TIMEZONE)
    return local.isoformat(timespec="milliseconds")


def alert_text(*, now: str, error: str, transformed: dict[str, Any], prompt: dict[str, Any]) -> str:
    """SUM-7b: the alert, with values unescaped as in n8n (Q-15) and without n8n's footer (D-019)."""
    return (
        "<b>Unable to summarize message</b>\n"
        f"<i>Attempted at {now}</i>\n"
        "\n"
        f"<b>Subject:</b> {transformed['subject']}\n"
        f"<b>From</b>: {transformed['from']}\n"
        f"<b>Date:</b> {transformed['date']}\n"
        f"<b>Model:</b> {prompt['model']}\n"
        f"<b>Model fallback:</b> {prompt['modelFallback']}\n"
        "\n"
        f"Execution error <b>{error}</b>\n"
        "\n"
        "<i>Retrying after 10 minutes</i>"
    )
