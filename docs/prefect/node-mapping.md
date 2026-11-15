# Prefect node mapping

How each n8n node is implemented in Prefect 3.9.3 (template: [criteria.md](../comparison/criteria.md#node-mapping-template)).
In Prefect everything is Python, so "native" needs a definition. Here:

- **Native**: a Prefect feature (deployment schedule, task states, flow control in the flow
  function) or an official Prefect integration (`prefect-sqlalchemy`) does the work. Our code only
  supplies the SQL or the parameters.
- **Library**: a task that calls a third-party client library directly (google-genai, httpx). No
  Prefect integration exists for Gemini or Telegram. A few lines each.
- **Custom**: logic ported from n8n, in [`prefect/src/email_summarizer/`](../../prefect/src/email_summarizer).

Every n8n node with work of its own is a Prefect task with the node's name, so a flow run in the
Prefect UI lists the same names as the n8n canvas.

## Workflow 1: `email-ingest`

| n8n node | Spec ID | Tool construct | Native / custom | Notes |
|---|---|---|---|---|
| `fetch-n8n-inbox` (trigger) | ING-1 | Deployment schedule cron `* * * * *` (concurrency limit 1, `CANCEL_NEW`) + task `fetch-n8n-inbox` (`imap.fetch_unseen`) | Custom (schedule native) | Prefect has no email trigger; `prefect-email` 0.4.3 only sends over SMTP. stdlib `imaplib` and `email`, with n8n's decoding rules ported from `@n8n/imap`. Polling replaces IDLE (Q-8). |
| `select-n8n-content-generic` | ING-2 | Task `select-n8n-content-generic` (`ingest.select_content`) | Custom | One regex with JavaScript's `\s`. |
| `map-content` | ING-3 | Task `map-content` (`ingest.map_content`) | Custom | Mail dates through `email.utils`, which matches V8 on every vector (G-4). |
| `insert_raw_news` | ING-4 | Task `insert_raw_news`: `SqlAlchemyConnector.execute_many` | Native | One transaction for the batch, like n8n's single-query batching (Q-19). psycopg binds the enum column from a string without a cast. |

## Workflow 2: `email-summarize-send`

| n8n node | Spec ID | Tool construct | Native / custom | Notes |
|---|---|---|---|---|
| `10min-runner` | trigger | Deployment schedule cron `*/10 * * * *` in `prefect.yaml` | Native | No concurrency limit, as in n8n (Q-16). |
| `pg-get-dedup-raw-news` | SUM-1 | Task: `SqlAlchemyConnector.fetch_one` | Native | SQL verbatim. NULL columns come back as `None` with their keys, so the port sees what n8n saw. |
| `results-not-empty` | SUM-2 | `if row is None: return` in the flow | Native | Plain Python control flow. |
| `transform-content` | SUM-3 | Task `transform-content` (`summarize.transform_content`) | Custom | |
| `prompt-builder` | SUM-4 | Task `prompt-builder` (`summarize.build_prompt`) | Custom | Model names from the `gemini_model` / `gemini_model_fallback` variables. |
| `ai-summary` | SUM-5 | The flow calls the primary task with `return_state=True` and, if its state is `Failed`, the fallback | Native | Any exception falls back, 4xx included. Both task runs are visible in the UI. |
| `gemini-flash`, `gemini-flash-fallback` | SUM-5 | One task, `ask_gemini`, run under both names (`with_options(name=…)`): google-genai `generate_content` with the system message and `HttpOptions(base_url=…)` | Library | No Prefect Gemini integration (`prefect-gcp` targets Vertex AI). Pydantic AI's Prefect integration (`PrefectDurability`) was considered: it brings agents, tools and cached model calls, more than one prompt with a fallback needs. |
| `combine-results` + `pg-update-ai-summary` | SUM-6 | Task `pg-update-ai-summary`: `SqlAlchemyConnector.execute` with named parameters | Native | `summary_retry` computed from the row read, as n8n does. |
| `get-error` + `pg-update-ai-error` | SUM-7a | Task `pg-update-ai-error` | Native | Error text = the API's `message` (D-019); model = primary (D-008). |
| `telegram-send-failed-exec` | SUM-7b | Task `telegram-send-failed-exec`: the alert text from `summarize.alert_text`, sent with httpx | Library | No Telegram integration in Prefect. A failure raises out of the flow (Q-15). No n8n footer (D-019). |
| `pg-drop-dups` | DUP-1 | Task `pg-drop-dups`: `SqlAlchemyConnector.execute` | Native | SQL verbatim. |
| `pg-get-summary` | SND-1 | Task: `SqlAlchemyConnector.fetch_one` | Native | SQL verbatim. |
| (none: n8n runs nothing without items) | SND-1a | `if row is None: return` | Native | |
| `format-as-markdown` | SND-2 | Task `format-as-markdown` (`markdown.format_as_markdown`) | Custom | JavaScript regex semantics and UTF-16 lengths reproduced. |
| `telegram-send-summary-markdown` | SND-3 | Task with `return_state=True`: httpx `sendMessage` (`clients.send_message`) | Library | A non-`ok` response raises `TelegramError(description)`; network errors raise httpx's error. |
| `pg-update-summary-sent` | SND-4 | Task `pg-update-summary-sent` | Native | `formatted_summary` = the response's `result.text`. |
| `pg-update-sent-error` | SND-5 | Task `pg-update-sent-error` | Native | `sent_status` = Telegram's description, or the network error's message. |

## Count

| | Native | Library | Custom |
|---|---|---|---|
| n8n nodes (23, without sticky notes) | 13 | 4: `gemini-flash`, `gemini-flash-fallback`, `telegram-send-failed-exec`, `telegram-send-summary-markdown` | 6: `fetch-n8n-inbox`, `select-n8n-content-generic`, `map-content`, `transform-content`, `prompt-builder`, `format-as-markdown` |

Prefect side: 2 flows and 17 task definitions (one of them run under two names).

Code, including comments and docstrings (`wc -l`, 2026-11-15):

| Part | Lines |
|---|---|
| Ported logic: `imap.py` 293, `ingest.py` 100, `markdown.py` 89, `js.py` 89, `summarize.py` 85 | 656 |
| SQL: `sql.py` (the 3 verbatim queries and 5 writes) | 110 |
| Orchestration: `flows.py` 264, `config.py` 85, `bootstrap.py` 57, `clients.py` 53 | 459 |
| Deployment: `compose.yaml` 107, `prefect.yaml` 33, `Dockerfile` 16, `deploy.sh` 12 | 168 |
| Tests: `prefect/tests/` | 180 |
