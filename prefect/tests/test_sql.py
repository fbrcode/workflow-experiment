"""The spec requires SUM-1, DUP-1 and SND-1 verbatim: compare them with the baseline export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from email_summarizer import sql

BASELINE = Path(__file__).resolve().parents[2] / "baseline" / "EmailSummarizerFromPgCron.json"
QUERIES = {
    node["name"]: node["parameters"]["query"]
    for node in json.loads(BASELINE.read_text(encoding="utf-8"))["nodes"]
    if node.get("parameters", {}).get("query")
}


@pytest.mark.parametrize(
    ("node", "query"),
    [
        ("pg-get-dedup-raw-news", sql.PICK_ROW_TO_SUMMARIZE),
        ("pg-drop-dups", sql.DROP_DUPLICATES),
        ("pg-get-summary", sql.PICK_ROW_TO_SEND),
    ],
)
def test_query_is_verbatim(node: str, query: str) -> None:
    assert query == QUERIES[node]
