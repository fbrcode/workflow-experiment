"""Readers for the mocks' recorded calls and the audit log, shared by the scenarios."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

JSON = dict[str, Any]


def bare(model: str) -> str:
    """The model id in the API path (D-011: config keeps the `models/` prefix)."""
    return model.removeprefix("models/")


def _texts(content: JSON | None) -> str:
    return "\n".join(p["text"] for p in (content or {}).get("parts") or [] if isinstance(p.get("text"), str))


def prompt_of(call: JSON) -> str:
    """The user text of a recorded generateContent call (the SUM-4 prompt)."""
    return "\n".join(_texts(c) for c in (call.get("request") or {}).get("contents") or [])


def system_of(call: JSON) -> str:
    request = call.get("request") or {}
    return _texts(request.get("systemInstruction") or request.get("system_instruction"))


def send_params(call: JSON) -> JSON:
    """sendMessage parameters, normalized: form and JSON bodies carry the same values as text."""
    params = call["params"]
    return {
        "chat_id": str(params.get("chat_id")),
        "text": params.get("text"),
        "parse_mode": params.get("parse_mode"),
        "disable_web_page_preview": str(params.get("disable_web_page_preview")).lower(),
    }


def sent(chat: str, text: str, parse_mode: str) -> JSON:
    """What `send_params` should return for a baseline sendMessage (SND-3, SUM-7b; D-019)."""
    return {"chat_id": chat, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": "true"}


def changes(history: list[JSON]) -> dict[int, list[tuple[str, set[str]]]]:
    """Per row: each audit entry as (operation, changed columns)."""
    out: dict[int, list[tuple[str, set[str]]]] = defaultdict(list)
    for entry in history:
        out[entry["news_email_id"]].append((entry["operation"], set(entry["changed_fields"] or [])))
    return dict(out)


def pick(row: JSON, keys: JSON | set[str]) -> JSON:
    return {k: row[k] for k in keys}
