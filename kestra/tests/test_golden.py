"""The ported n8n logic against the golden vectors (CLAUDE.md rule 4), without Kestra running.

Every case in each vector file must come out byte for byte: same keys, same types, same strings.
ING-1 needs GreenMail and is checked in test_imap_simple.py. SUM-7b's alert text is a Pebble
template in the flow, so it's checked end to end (docs/kestra/README.md, Verification).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from summarizer import ported, task

VECTORS = Path(__file__).resolve().parents[2] / "tests" / "golden" / "vectors"


def cases(name: str) -> list[Any]:
    doc = json.loads((VECTORS / name).read_text(encoding="utf-8"))
    assert doc["cases"], name
    return [pytest.param(case, id=case["id"]) for case in doc["cases"]]


@pytest.mark.parametrize("case", cases("ing2-select-content.json"))
def test_ing2_select_content(case: dict[str, Any]) -> None:
    assert ported.select_content(case["input"]) == case["output"]


@pytest.mark.parametrize("case", cases("ing3-map-content.json"))
def test_ing3_map_content(case: dict[str, Any]) -> None:
    assert ported.map_content(case["input"]) == case["output"]


@pytest.mark.parametrize("case", cases("ing4-insert-raw-news.json"))
def test_ing4_insert_values(case: dict[str, Any]) -> None:
    assert ported.insert_values(case["input"]) == case["output"]


@pytest.mark.parametrize("case", cases("sum3-transform-content.json"))
def test_sum3_transform_content(case: dict[str, Any]) -> None:
    assert ported.transform_content(case["input"]) == case["output"]


@pytest.mark.parametrize("case", cases("sum4-prompt-builder.json"))
def test_sum4_prompt_builder(case: dict[str, Any]) -> None:
    assert ported.build_prompt(case["input"]) == case["output"]


@pytest.mark.parametrize("case", cases("snd2-format-as-markdown.json"))
def test_snd2_format_as_markdown(case: dict[str, Any]) -> None:
    assert ported.format_as_markdown(case["input"]) == case["output"]


# Kestra's JDBC Query task leaves NULL columns out of `row` entirely. The task entry points put
# them back as None, so a NULL still prints as "null" (golden finding G-3), not "undefined".


@pytest.mark.parametrize("case", cases("sum3-transform-content.json"))
def test_sum3_with_kestra_row_dropping_nulls(case: dict[str, Any]) -> None:
    kestra_row = {k: v for k, v in case["input"].items() if v is not None}
    restored = task.restore_null_columns(kestra_row, task.SUM1_COLUMNS)
    assert ported.transform_content(restored) == case["output"]


@pytest.mark.parametrize("case", cases("snd2-format-as-markdown.json"))
def test_snd2_with_kestra_row_dropping_nulls(case: dict[str, Any]) -> None:
    kestra_row = {k: v for k, v in case["input"].items() if v is not None}
    restored = task.restore_null_columns(kestra_row, task.SND1_COLUMNS)
    assert ported.format_as_markdown(restored) == case["output"]
