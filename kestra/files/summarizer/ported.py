"""The n8n Set/Code node logic, ported to Python (spec ING-2, ING-3, SUM-3, SUM-4, SND-2).

Each function takes the item the n8n node received and returns the item it emitted. The
reference is the original JavaScript in baseline/*.json, and the referee is
tests/golden/vectors: outputs must match byte for byte (CLAUDE.md rule 4). Regexes are written
with the JavaScript character sets from `jscompat` because Python's `\\s`, `.`, `^` and `$` differ.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from .jscompat import (
    INPUT_END,
    LINE_END,
    LINE_START,
    NOT_LT,
    UNDEFINED,
    WS,
    drop_undefined,
    get,
    js_date_utc,
    js_str,
    js_trim,
    js_truthy,
    utf16_len,
)

DEFAULT_MODEL = "models/gemini-flash-lite-latest"
DEFAULT_MODEL_FALLBACK = "models/gemini-2.5-flash"

# --- ING-2 select-n8n-content-generic ---------------------------------------------------------

_PAREN_URL = re.compile(rf"\(https?://[^{WS})]+\)")


def select_content(item: dict[str, Any]) -> dict[str, Any]:
    """Set node: keep five fields and strip `(http…)` links from both bodies."""
    return drop_undefined(
        {
            "from": get(item, "from"),
            "subject": get(item, "subject"),
            "date": get(item, "date"),
            "htmlContent": _PAREN_URL.sub("", item["textHtml"]),
            "plainContent": _PAREN_URL.sub("", item["textPlain"]),
        }
    )


# --- ING-3 map-content ----------------------------------------------------------------------

TRANSLATE_IDS = frozenset(
    {"lennys-newsletter", "the-pragmatic-engineer", "tech-brew", "the-neuron", "morning-brew", "refactoring"}
)
CATEGORY_IDS = {"finance": frozenset({"rico-matinal"}), "news": frozenset({"the-news", "morning-brew"})}
HTML_CONTENT_IDS = frozenset({"tech-brew", "rico-matinal"})

_SENDER = re.compile(rf'"?([^"<]+)"?[{WS}]*<([^>]+)>{INPUT_END}')
_NON_PRINTABLE_ASCII = re.compile(r"[^\x20-\x7E]")
_NOT_SLUG = re.compile(rf"[^a-z0-9{WS}]")
_WS_RUN = re.compile(rf"[{WS}]+")
_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _category(from_name_id: str) -> str:
    for category, ids in CATEGORY_IDS.items():
        if from_name_id in ids:
            return category
    return "tech"


def date_gmt3(date: Any) -> str:
    """`${Ddd}, ${D} ${Mon} ${YYYY} at ${HH}h ${MM}min` for (instant - 3 h), as V8 prints it."""
    instant = js_date_utc(date)
    if instant is None:  # Invalid Date: every getUTC* is NaN and days[NaN] is undefined
        return "undefined, NaN undefined NaN at NaNh NaNmin"
    t = instant - timedelta(hours=3)
    weekday = (t.weekday() + 1) % 7  # Python: Monday=0; JS getUTCDay: Sunday=0
    return f"{_DAYS[weekday]}, {t.day} {_MONTHS[t.month - 1]} {t.year} at {t.hour:02d}h {t.minute:02d}min"


def map_content(item: dict[str, Any]) -> dict[str, Any]:
    sender = get(item, "from")
    sender = sender if js_truthy(sender) else ""
    match = _SENDER.match(sender)
    if match:
        from_name = js_trim(_NON_PRINTABLE_ASCII.sub("", js_trim(match.group(1))))
        from_email = js_trim(match.group(2))
    else:
        from_name = ""
        from_email = js_trim(sender)
    from_name_id = _WS_RUN.sub("-", js_trim(_NOT_SLUG.sub("", from_name.lower())))
    raw_content = get(item, "htmlContent") if from_name_id in HTML_CONTENT_IDS else get(item, "plainContent")
    return drop_undefined(
        {
            "from_name_id": from_name_id,
            "from_name": from_name,
            "from_email": from_email,
            "translate": from_name_id in TRANSLATE_IDS,
            "category": _category(from_name_id),
            "date": get(item, "date"),
            "date_gmt3": date_gmt3(get(item, "date")),
            "subject": get(item, "subject"),
            "raw_content": raw_content,
        }
    )


def insert_values(mapped: dict[str, Any]) -> dict[str, Any]:
    """ING-4 `insert_raw_news` column mapping (column name to value)."""
    return {
        "email_from": mapped["from_name"],
        "email_subject": mapped["subject"],
        "email_date": mapped["date_gmt3"],
        "email_raw_content": mapped["raw_content"],
        "email_inbox_label": mapped["from_name_id"],
        "to_translate": mapped["translate"],
        "email_category": mapped["category"],
    }


# --- SUM-3 transform-content ------------------------------------------------------------------

_BEFORE_ANGLE = re.compile(r"([^<]+)<")
_REMOVE_PHRASES = ("View image: ", "Caption: ", "Follow image link: ")


def transform_content(row: dict[str, Any]) -> dict[str, Any]:
    sender = get(row, "email_from")
    sender = sender if js_truthy(sender) else ""
    match = _BEFORE_ANGLE.match(sender)
    if match:
        sender = js_trim(match.group(1))
    content = get(row, "email_raw_content")
    content = content if js_truthy(content) else ""
    for phrase in _REMOVE_PHRASES:
        content = content.replace(phrase, "")
    content = _NON_PRINTABLE_ASCII.sub("", content)
    return drop_undefined(
        {
            "news_id": get(row, "id"),
            "from": sender,
            "subject": get(row, "email_subject"),
            "date": get(row, "email_date"),
            "to_translate": get(row, "to_translate"),
            "translate_language": get(row, "translate_language"),
            "content": content,
        }
    )


# --- SUM-4 prompt-builder -----------------------------------------------------------------------


def build_prompt(
    item: dict[str, Any], model: str = DEFAULT_MODEL, model_fallback: str = DEFAULT_MODEL_FALLBACK
) -> dict[str, Any]:
    """The prompt is verbatim, `cntent` typo included (Q-1). Models are configurable (SUM-4)."""
    translate_prompt = ("translated to " if js_truthy(get(item, "to_translate")) else "using ") + (
        f"{js_str(get(item, 'translate_language'))} language"
    )
    prompt = (
        "Summarize this email cntent in main topics with content with 2500 chars limit "
        f"{translate_prompt} and remove any sponsored news (presented by) silently. "
        f"Content >> {js_str(get(item, 'content'))}"
    )
    return {"model": model, "modelFallback": model_fallback, "prompt": prompt}


# --- SND-2 format-as-markdown -----------------------------------------------------------------

_HEADINGS = [re.compile(rf"{LINE_START}{hashes}[{WS}]*({NOT_LT}+){LINE_END}") for hashes in ("####", "###", "##")]
_RULE = re.compile(rf"{LINE_START}---{LINE_END}")
_BULLET_WIDE = re.compile(rf"{LINE_START}([{WS}]*)[*\-][{WS}]{{2,}}")
_BULLET = re.compile(rf"{LINE_START}([{WS}]*)[*\-][{WS}]")
_ALL_SPECIAL = re.compile(r"([_\[\]()~`>#+\-=|{}.!*\\])")
_SPECIAL_NO_STAR = re.compile(r"([_\[\]()~`>#+\-=|{}.!\\])")
_BOLD = re.compile(r"\*\*([\s\S]+?)\*\*")
_ITALIC = re.compile(rf"\*([^{WS}*][^*\n]*?)\*")
_PLACEHOLDER = re.compile(r"@@(?:BOLD|ITAL)([0-9]+)@@")


def _escape_keep_bold_italic(text: str) -> str:
    placeholders: list[str] = []

    def bold(m: re.Match[str]) -> str:
        key = f"@@BOLD{len(placeholders)}@@"
        placeholders.append("*" + _ALL_SPECIAL.sub(r"\\\1", m.group(1)) + "*")
        return key

    def italic(m: re.Match[str]) -> str:
        key = f"@@ITAL{len(placeholders)}@@"
        placeholders.append("_" + _ALL_SPECIAL.sub(r"\\\1", m.group(1)) + "_")
        return key

    def restore(m: re.Match[str]) -> str:
        index = int(m.group(1))
        return placeholders[index] if index < len(placeholders) else "undefined"

    text = _BOLD.sub(bold, text)
    text = _ITALIC.sub(italic, text)
    text = _SPECIAL_NO_STAR.sub(r"\\\1", text)
    text = text.replace("*", "\\*")
    return _PLACEHOLDER.sub(restore, text)


def format_as_markdown(row: dict[str, Any]) -> dict[str, Any]:
    summary = (
        f"**{js_str(get(row, 'email_from'))}**\n**{js_str(get(row, 'email_date'))}**\n"
        f"**{js_str(get(row, 'email_subject'))}**\n"
        f"*Summary {js_str(get(row, 'translated'))} with {js_str(get(row, 'model_used'))}*\n\n"
        f"{js_str(get(row, 'summary_content'))}"
    )
    summary = summary.replace("\\n", "\n")  # 0) literal backslash-n from the LLM
    for heading in _HEADINGS:  # 1) headings become bold, `---` lines become empty
        summary = heading.sub(r"**\1**", summary)
    summary = _RULE.sub("", summary)
    summary = _BULLET_WIDE.sub(r"\1• ", summary)  # 2) bullets
    summary = _BULLET.sub(r"\1• ", summary)
    summary = summary.replace('"', "'")  # 3) double quotes
    message = _escape_keep_bold_italic(summary)
    return drop_undefined(
        {
            "news_id": get(row, "id"),
            "initialMessageLength": utf16_len(summary),
            "finalMessageLength": utf16_len(message),
            "telegramMessage": message,
        }
    )


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MODEL_FALLBACK",
    "UNDEFINED",
    "build_prompt",
    "date_gmt3",
    "format_as_markdown",
    "insert_values",
    "map_content",
    "select_content",
    "transform_content",
]
