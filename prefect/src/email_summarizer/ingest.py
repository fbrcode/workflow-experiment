"""Workflow 1 logic: ING-2 (select content), ING-3 (map content) and ING-4 (insert values).

Ported from `baseline/EmailSummarizerMainUnique.json`: the `select-n8n-content-generic` Set node,
the `map-content` Code node and the `insert_raw_news` column map. The golden vectors
(`ing2-*`, `ing3-*`, `ing4-*`) are the referee.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Final

from email_summarizer import js

# /\(https?:\/\/[^\s)]+\)/g
_PAREN_URL: Final = re.compile(rf"\(https?://[^{js.S_BODY})]+\)")
# /^"?([^"<]+)"?\s*<([^>]+)>$/
_SENDER: Final = re.compile(rf'^"?([^"<]+)"?{js.S}*<([^>]+)>{js.END}')
_NON_PRINTABLE: Final = re.compile(r"[^\x20-\x7e]")
_NOT_SLUG: Final = re.compile(rf"[^a-z0-9{js.S_BODY}]")
_WHITESPACE_RUN: Final = re.compile(f"{js.S}+")

TRANSLATE_IDS: Final = frozenset(
    {"lennys-newsletter", "the-pragmatic-engineer", "tech-brew", "the-neuron", "morning-brew", "refactoring"}
)
CATEGORY_IDS: Final = {"finance": frozenset({"rico-matinal"}), "news": frozenset({"the-news", "morning-brew"})}
HTML_CONTENT_IDS: Final = frozenset({"tech-brew", "rico-matinal"})

_DAYS: Final = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def select_content(email: dict[str, Any]) -> dict[str, Any]:
    """ING-2: keep the five fields, with URLs in parentheses removed from both bodies."""
    return {
        "from": email.get("from"),
        "subject": email.get("subject"),
        "date": email.get("date"),
        "htmlContent": _PAREN_URL.sub("", email["textHtml"]),
        "plainContent": _PAREN_URL.sub("", email["textPlain"]),
    }


def category_of(from_name_id: str) -> str:
    for category, ids in CATEGORY_IDS.items():
        if from_name_id in ids:
            return category
    return "tech"


def date_gmt3(date: str | None) -> str:
    """ING-3.6: the date minus a fixed 3 hours, as `Sat, 14 Nov 2026 at 09h 05min`.

    An unparseable date gives what the JavaScript gives for an Invalid Date.
    """
    parsed = js.parse_date(date)
    if parsed is None:
        return "undefined, NaN undefined NaN at NaNh NaNmin"
    d = parsed - timedelta(hours=3)
    weekday = (d.weekday() + 1) % 7  # JS getUTCDay(): Sunday is 0
    return f"{_DAYS[weekday]}, {d.day} {_MONTHS[d.month - 1]} {d.year} at {d.hour:02d}h {d.minute:02d}min"


def map_content(item: dict[str, Any]) -> dict[str, Any]:
    """ING-3: sender name and address, slug, translate flag, category, body choice, GMT-3 date."""
    sender = item.get("from") or ""
    match = _SENDER.match(sender)
    if match:
        from_name = js.trim(_NON_PRINTABLE.sub("", js.trim(match.group(1))))
        from_email = js.trim(match.group(2))
    else:
        from_name = ""
        from_email = js.trim(sender)

    from_name_id = _WHITESPACE_RUN.sub("-", js.trim(_NOT_SLUG.sub("", from_name.lower())))
    return {
        "from_name_id": from_name_id,
        "from_name": from_name,
        "from_email": from_email,
        "translate": from_name_id in TRANSLATE_IDS,
        "category": category_of(from_name_id),
        "date": item.get("date"),
        "date_gmt3": date_gmt3(item.get("date")),
        "subject": item.get("subject"),
        "raw_content": item.get("htmlContent") if from_name_id in HTML_CONTENT_IDS else item.get("plainContent"),
    }


def insert_values(mapped: dict[str, Any]) -> dict[str, Any]:
    """ING-4: the `insert_raw_news` column map. Other columns take their database defaults."""
    return {
        "email_from": mapped["from_name"],
        "email_subject": mapped["subject"],
        "email_date": mapped["date_gmt3"],
        "email_raw_content": mapped["raw_content"],
        "email_inbox_label": mapped["from_name_id"],
        "to_translate": mapped["translate"],
        "email_category": mapped["category"],
    }
