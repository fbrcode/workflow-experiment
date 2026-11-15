"""The two n8n workflows as Prefect flows (spec: docs/baseline/workflow-spec.md).

Each n8n node is a task with the node's name, so a run in the Prefect UI reads like the n8n
canvas. Error handling follows n8n (D-019): only the model call (SUM-5) and the summary send
(SND-3) have error paths, taken by asking the task for its final state (`return_state=True`)
instead of letting it raise. Any other failure raises out of the flow, which fails the run and
skips the branches after it until the next tick.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prefect import flow, get_run_logger, task
from prefect.states import State

from email_summarizer import clients, config, imap, ingest, markdown, sql, summarize

# --- Workflow 1: EmailSummarizerMainUnique (ingest) ----------------------------------------------


@task(name="fetch-n8n-inbox")
def fetch_n8n_inbox() -> list[dict[str, str]]:
    """ING-1: UNSEEN mail from the mailbox, marked as read once fetched."""
    settings = config.imap()
    return imap.fetch_unseen(
        host=settings.host,
        port=settings.port,
        secure=settings.secure,
        user=settings.user,
        password=settings.password,
        mailbox=settings.mailbox,
    )


@task(name="select-n8n-content-generic")
def select_n8n_content_generic(emails: list[dict[str, str]]) -> list[dict[str, Any]]:
    """ING-2."""
    return [ingest.select_content(item) for item in emails]


@task(name="map-content")
def map_content(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ING-3."""
    return [ingest.map_content(item) for item in items]


@task(name="insert_raw_news")
def insert_raw_news(items: list[dict[str, Any]]) -> int:
    """ING-4: one INSERT per email, all in one transaction, as n8n's Postgres node batches them (Q-19)."""
    rows = [ingest.insert_values(item) for item in items]
    with config.app_db() as db:
        db.execute_many(sql.INSERT_RAW_NEWS, seq_of_parameters=rows)
    return len(rows)


@flow(name="email-ingest")
def email_ingest() -> int:
    """Read new newsletters and store them (ING-1 to ING-4). Returns the number of emails inserted."""
    emails = fetch_n8n_inbox()
    if not emails:
        return 0  # the n8n trigger emits nothing when there's no new mail
    inserted = insert_raw_news(map_content(select_n8n_content_generic(emails)))
    get_run_logger().info("Inserted %d email(s)", inserted)
    return inserted


# --- Workflow 2: EmailSummarizerFromPgCron (summarize and send) -----------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_one(query: str) -> dict[str, Any] | None:
    with config.app_db() as db:
        row = db.fetch_one(query)
    return dict(row._mapping) if row is not None else None


@task(name="pg-get-dedup-raw-news")
def pg_get_dedup_raw_news() -> dict[str, Any] | None:
    """SUM-1: the next row to summarize, or None."""
    return _fetch_one(sql.PICK_ROW_TO_SUMMARIZE)


@task(name="transform-content")
def transform_content(row: dict[str, Any]) -> dict[str, Any]:
    """SUM-3."""
    return summarize.transform_content(row)


@task(name="prompt-builder")
def prompt_builder(item: dict[str, Any]) -> dict[str, Any]:
    """SUM-4, with the model names from configuration (defaults in the spec)."""
    return summarize.build_prompt(item, config.variable("gemini_model"), config.variable("gemini_model_fallback"))


@task(name="gemini")
def ask_gemini(model: str, prompt: str) -> str:
    """SUM-5: one model call. Used twice, as `gemini-flash` and `gemini-flash-fallback`."""
    return clients.generate(
        base_url=config.variable("gemini_base_url"),
        api_key=config.secret("gemini-api-key"),
        model=model,
        prompt=prompt,
    )


@task(name="pg-update-ai-summary")
def pg_update_ai_summary(row: dict[str, Any], prompt: dict[str, Any], summary: str) -> None:
    """SUM-6: model_used is the primary model even when the fallback answered (Q-2)."""
    with config.app_db() as db:
        db.execute(
            sql.UPDATE_SUMMARY_OK,
            {
                "summary_content": summary,
                "summarized_at": _now(),
                "model_used": prompt["model"],
                "summary_retry": row["summary_retry"] + 1,
                "news_id": row["id"],
            },
        )


@task(name="pg-update-ai-error")
def pg_update_ai_error(row: dict[str, Any], prompt: dict[str, Any], error: str) -> None:
    """SUM-7a: model_used is the primary model (D-008)."""
    with config.app_db() as db:
        db.execute(
            sql.UPDATE_SUMMARY_ERROR,
            {
                "summarized_at": _now(),
                "model_used": prompt["model"],
                "error": error,
                "summary_retry": row["summary_retry"] + 1,
                "news_id": row["id"],
            },
        )


@task(name="telegram-send-failed-exec")
def telegram_send_failed_exec(transformed: dict[str, Any], prompt: dict[str, Any], error: str) -> None:
    """SUM-7b: the admin alert. A failure here isn't handled and fails the run, as in n8n (Q-15)."""
    text = summarize.alert_text(now=summarize.now_iso(_now()), error=error, transformed=transformed, prompt=prompt)
    clients.send_message(
        base_url=config.variable("telegram_base_url"),
        token=config.secret("telegram-bot-token"),
        chat_id=config.secret("telegram-alert-chat-id"),
        text=text,
        parse_mode="HTML",
    )


@task(name="pg-drop-dups")
def pg_drop_dups() -> None:
    """DUP-1."""
    with config.app_db() as db:
        db.execute(sql.DROP_DUPLICATES)


@task(name="pg-get-summary")
def pg_get_summary() -> dict[str, Any] | None:
    """SND-1: the next summarized row to send, or None."""
    return _fetch_one(sql.PICK_ROW_TO_SEND)


@task(name="format-as-markdown")
def format_as_markdown(row: dict[str, Any]) -> dict[str, Any]:
    """SND-2."""
    return markdown.format_as_markdown(row)


@task(name="telegram-send-summary-markdown")
def telegram_send_summary_markdown(chat_id: str, message: str) -> dict[str, Any]:
    """SND-3: returns Telegram's response; raises TelegramError on a rejection."""
    return clients.send_message(
        base_url=config.variable("telegram_base_url"),
        token=config.secret("telegram-bot-token"),
        chat_id=chat_id,
        text=message,
        parse_mode="MarkdownV2",
    )


@task(name="pg-update-summary-sent")
def pg_update_summary_sent(row: dict[str, Any], formatted: dict[str, Any], response: dict[str, Any]) -> None:
    """SND-4: formatted_summary is the plain text Telegram rendered."""
    with config.app_db() as db:
        db.execute(
            sql.UPDATE_SENT_OK,
            {
                "sent_at": _now(),
                "formatted_length": formatted["finalMessageLength"],
                "formatted_summary": response["result"]["text"],
                "sent_retry": row["sent_retry"] + 1,
                "news_id": formatted["news_id"],
            },
        )


@task(name="pg-update-sent-error")
def pg_update_sent_error(row: dict[str, Any], formatted: dict[str, Any], error: str) -> None:
    """SND-5: the row goes back to be summarized again (Q-12)."""
    with config.app_db() as db:
        db.execute(
            sql.UPDATE_SENT_ERROR,
            {
                "sent_retry": row["sent_retry"] + 1,
                "sent_at": _now(),
                "error": error,
                "formatted_summary": formatted["telegramMessage"],
                "formatted_length": formatted["finalMessageLength"],
                "news_id": formatted["news_id"],
            },
        )


def _error_of(state: State) -> str:
    return clients.error_message(state.result(raise_on_failure=False))


def summarizer() -> None:
    """Branch A: SUM-1 to SUM-7."""
    row = pg_get_dedup_raw_news()
    if row is None:
        return  # SUM-2
    transformed = transform_content(row)
    prompt = prompt_builder(transformed)

    answer = ask_gemini.with_options(name="gemini-flash")(prompt["model"], prompt["prompt"], return_state=True)
    if answer.is_failed():  # any error, 4xx included, goes to the fallback (SUM-5)
        answer = ask_gemini.with_options(name="gemini-flash-fallback")(
            prompt["modelFallback"], prompt["prompt"], return_state=True
        )

    if answer.is_completed():
        pg_update_ai_summary(row, prompt, answer.result())
        return
    error = _error_of(answer)
    pg_update_ai_error(row, prompt, error)  # SUM-7a before SUM-7b (D-019)
    telegram_send_failed_exec(transformed, prompt, error)


def sender() -> None:
    """Branch C: SND-1 to SND-5."""
    row = pg_get_summary()
    if row is None:
        return  # SND-1a
    formatted = format_as_markdown(row)
    sent = telegram_send_summary_markdown(row["telegram_chat_id"], formatted["telegramMessage"], return_state=True)
    if sent.is_completed():
        pg_update_summary_sent(row, formatted, sent.result())
    else:
        pg_update_sent_error(row, formatted, _error_of(sent))


@flow(name="email-summarize-send")
def email_summarize_send() -> None:
    """One tick: summarize one row, drop duplicates, send one row, in n8n's canvas order."""
    summarizer()
    pg_drop_dups()
    sender()
