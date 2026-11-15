"""Entry points for the Kestra script tasks: `python -m summarizer.task <step>`.

Each step reads its input from a JSON file that the flow renders with `inputFiles`, runs the ported
logic, and hands results back to Kestra:

- small values as task outputs, by printing `::{"outputs": {...}}::` (Kestra's script output
  protocol), read in the flow as `{{ outputs.<task>.vars.<key> }}`
- rows for the JDBC Batch insert as `rows.ion` (one JSON object per line, which is valid Ion
  text), captured with `outputFiles`

Configuration comes from environment variables that the flow sets from `envs` and `secret()`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import imap_simple, ported

# Columns of the SUM-1 and SND-1 result rows. Kestra's JDBC Query task leaves NULL columns out of
# `row`, but the n8n Code nodes saw them as null, which prints as "null" (golden finding G-3).
SUM1_COLUMNS = (
    "id",
    "email_inbox_label",
    "email_date",
    "email_from",
    "email_subject",
    "to_translate",
    "translate_language",
    "email_raw_content",
    "summary_retry",
)
SND1_COLUMNS = (
    "id",
    "email_inbox_label",
    "telegram_chat_id",
    "email_category",
    "email_from",
    "email_subject",
    "email_date",
    "translated",
    "model_used",
    "summary_content",
    "sent_retry",
)
INSERT_COLUMNS = (
    "email_from",
    "email_subject",
    "email_date",
    "email_raw_content",
    "email_inbox_label",
    "to_translate",
    "email_category",
)


def restore_null_columns(row: dict[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: row.get(column) for column in columns}


def emit(outputs: dict[str, Any]) -> None:
    print("::" + json.dumps({"outputs": outputs}, ensure_ascii=False) + "::", flush=True)


def read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ingest() -> None:
    """ING-1 to ING-3 for every UNSEEN message; ING-4's column values go to rows.ion."""
    items = imap_simple.fetch_unseen(
        host=os.environ["IMAP_HOST"],
        port=int(os.environ["IMAP_PORT"]),
        secure=os.environ["IMAP_SECURE"].lower() == "true",
        user=os.environ["IMAP_USER"],
        password=os.environ["IMAP_PASSWORD"],
        mailbox=os.environ["IMAP_MAILBOX"],
    )
    with Path("rows.ion").open("w", encoding="utf-8", newline="\n") as out:
        for item in items:
            values = ported.insert_values(ported.map_content(ported.select_content(item)))
            out.write(json.dumps({c: values[c] for c in INSERT_COLUMNS}, ensure_ascii=False) + "\n")
    emit({"count": len(items)})


def transform() -> None:
    """SUM-3 then SUM-4 on the SUM-1 row."""
    row = restore_null_columns(read_json("row.json"), SUM1_COLUMNS)
    transformed = ported.transform_content(row)
    prompt = ported.build_prompt(
        transformed,
        model=os.environ.get("GEMINI_MODEL") or ported.DEFAULT_MODEL,
        model_fallback=os.environ.get("GEMINI_MODEL_FALLBACK") or ported.DEFAULT_MODEL_FALLBACK,
    )
    emit({"transform": transformed, "prompt": prompt})


def format_message() -> None:
    """SND-2 on the SND-1 row."""
    row = restore_null_columns(read_json("row.json"), SND1_COLUMNS)
    emit(ported.format_as_markdown(row))


STEPS = {"ingest": ingest, "transform": transform, "format": format_message}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in STEPS:
        print(f"usage: python -m summarizer.task {{{','.join(STEPS)}}}", file=sys.stderr)
        return 2
    STEPS[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
