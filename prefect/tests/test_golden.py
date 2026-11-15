"""Golden vectors (tests/golden/vectors) against the Python port, without Prefect running.

Each case's `output` must match byte for byte. Results are compared as canonical JSON, so a type
difference (true vs 1, null vs "") fails even where Python's == would let it pass.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from email_summarizer import ingest, markdown, summarize

VECTORS = Path(__file__).resolve().parents[2] / "tests" / "golden" / "vectors"


def _cases(name: str) -> list[Any]:
    data = json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))
    return [pytest.param(name, case, id=f"{name}/{case['id']}") for case in data["cases"]]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _alert(case_input: dict[str, Any]) -> dict[str, Any]:
    text = summarize.alert_text(
        now=case_input["now"],
        error=case_input["error"],
        transformed=case_input["transform-content"],
        prompt=case_input["prompt-builder"],
    )
    return {"text": text, "parse_mode": "HTML"}


PORTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ing2-select-content": ingest.select_content,
    "ing3-map-content": ingest.map_content,
    "ing4-insert-raw-news": ingest.insert_values,
    "sum3-transform-content": summarize.transform_content,
    "sum4-prompt-builder": summarize.build_prompt,
    "sum7b-alert-message": _alert,
    "snd2-format-as-markdown": markdown.format_as_markdown,
}


@pytest.mark.parametrize(("port", "case"), [param for name in PORTS for param in _cases(name)])
def test_vector(port: str, case: Any) -> None:
    assert _canonical(PORTS[port](case["input"])) == _canonical(case["output"])


def test_alert_timestamp_matches_luxon_to_iso() -> None:
    # The vectors freeze $now at 2026-11-14T12:00:00.000-03:00 (America/Sao_Paulo, D-019).
    assert summarize.now_iso(datetime(2026, 11, 14, 15, 0, tzinfo=UTC)) == "2026-11-14T12:00:00.000-03:00"


def test_invalid_date_prints_like_javascript() -> None:
    assert ingest.date_gmt3("not a date") == "undefined, NaN undefined NaN at NaNh NaNmin"
