"""Expected values, taken from the golden vectors (which ran the original n8n code, docs/golden.md).

The vectors were generated with the `.env.example` model names and mock-gemini, so the suite
requires those (checked in conftest). Nothing here is hand-written except the column defaults,
which come from the production schema (D-018), and the sets of columns each step changes, which
come from the baseline's Postgres nodes (spec SUM-6, SUM-7a, SND-4, SND-5).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from .settings import FIXTURES, VECTORS

JSON = dict[str, Any]

FIXTURE_NAMES = tuple(sorted(p.stem for p in FIXTURES.glob("*.eml")))
SYSTEM_MESSAGE = "You are a helpful assistant"  # SUM-5, Q-13

# A fresh row's defaults (ING-4 leaves them to the database; production schema, D-018).
INSERT_DEFAULTS: JSON = {
    "translate_language": "pt-BR",
    "model_used": None,
    "summary_status": "OK",
    "is_summarized": False,
    "summary_content": None,
    "summarized_at": None,
    "summary_retry": -1,
    "formatted_summary": None,
    "formatted_length": None,
    "sent_status": "OK",
    "was_sent": False,
    "sent_at": None,
    "sent_retry": -1,
}

# Columns whose value each step changes on a fresh row (news_email_history.changed_fields).
# A column the step writes with its current value doesn't show up (e.g. summary_status 'OK').
SUM6_CHANGES = {"is_summarized", "model_used", "summarized_at", "summary_content", "summary_retry"}
SUM7A_CHANGES = {"model_used", "summarized_at", "summary_retry", "summary_status"}
SND4_CHANGES = {"formatted_length", "formatted_summary", "sent_at", "sent_retry", "was_sent"}
SND5_CHANGES = {"formatted_length", "formatted_summary", "is_summarized", "sent_at", "sent_retry", "sent_status"}


@cache
def _cases(file: str) -> dict[str, JSON]:
    return {case["id"]: case for case in json.loads((VECTORS / file).read_text())["cases"]}


def _fixture_case(file: str, fixture: str) -> JSON:
    cases = _cases(file)
    if f"fixture:{fixture}" in cases:
        return cases[f"fixture:{fixture}"]
    # 03 is a byte-identical copy of 02 as far as the database is concerned (same ING-4 values),
    # so the vectors stop at ING-4 for it. Downstream it expects exactly what 02 expects.
    twin = _twin(fixture)
    return cases[f"fixture:{twin}"]


@cache
def _twin(fixture: str) -> str:
    insert = _cases("ing4-insert-raw-news.json")
    mine = insert[f"fixture:{fixture}"]["output"]
    twins = [n for n in FIXTURE_NAMES if n < fixture and insert[f"fixture:{n}"]["output"] == mine]
    assert twins, f"no golden vector covers {fixture} after ING-4"
    return twins[0]


@dataclass(frozen=True)
class Email:
    """What one fixture should become, end to end."""

    fixture: str
    insert: JSON  # ING-4 column values
    category: str
    prompt: str  # SUM-4
    model: str
    model_fallback: str
    summary: str  # mock-gemini's primary-model answer to the prompt (SUM-6 summary_content)
    message: str  # SND-2 telegramMessage (SND-3 text, SND-5 formatted_summary)
    final_length: int  # SND-2 finalMessageLength (formatted_length)
    rendered: str  # mock-telegram's result.text for the message (SND-4 formatted_summary)


@cache
def email(fixture: str) -> Email:
    insert = _cases("ing4-insert-raw-news.json")[f"fixture:{fixture}"]["output"]
    prompt = _fixture_case("sum4-prompt-builder.json", fixture)["output"]
    send = _fixture_case("snd2-format-as-markdown.json", fixture)
    assert send["mock_telegram"]["ok"], f"{fixture}: the golden message isn't valid MarkdownV2"
    return Email(
        fixture=fixture,
        insert=insert,
        category=insert["email_category"],
        prompt=prompt["prompt"],
        model=prompt["model"],
        model_fallback=prompt["modelFallback"],
        summary=send["input"]["summary_content"],
        message=send["output"]["telegramMessage"],
        final_length=send["output"]["finalMessageLength"],
        rendered=send["mock_telegram"]["result_text"],
    )


@dataclass(frozen=True)
class Alert:
    """SUM-7b: the alert text with `$now` and the error left open, and mock-telegram's verdict."""

    case: JSON
    pattern: re.Pattern[str]

    @property
    def transform(self) -> JSON:
        return self.case["input"]["transform-content"]

    @property
    def verdict(self) -> JSON:
        return self.case["mock_telegram"]


ISO_MILLIS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}"


@cache
def alert(case_id: str) -> Alert:
    case = _cases("sum7b-alert-message.json")[case_id]
    text, now, error = case["output"]["text"], case["input"]["now"], case["input"]["error"]
    head, rest = text.split(now, 1)
    middle, tail = rest.split(error, 1)
    pattern = re.compile(
        re.escape(head) + f"(?P<now>{ISO_MILLIS})" + re.escape(middle) + "(?P<error>.+?)" + re.escape(tail),
        re.DOTALL,
    )
    return Alert(case, pattern)


def vector_models() -> tuple[str, str]:
    first = _cases("sum4-prompt-builder.json")[f"fixture:{FIXTURE_NAMES[0]}"]["output"]
    return first["model"], first["modelFallback"]
