# Kestra node mapping

How each n8n node is implemented in Kestra 2.0.10 (template: [criteria.md](../comparison/criteria.md#node-mapping-template)).
**Native** means a Kestra core or plugin task does the work, configured in YAML. **Custom** means
our Python code (namespace files in `kestra/files/summarizer/`) runs in a script task.

## Workflow 1: `email-ingest`

| n8n node | Spec ID | Tool construct | Native / custom | Notes |
|---|---|---|---|---|
| `fetch-n8n-inbox` (trigger) | ING-1 | `core.trigger.Schedule` `* * * * *` + `scripts.python.Commands` task `fetch-n8n-inbox` (`imap_simple.py`) | Custom | `plugin-email`'s `MailReceivedTrigger` exists but can't meet ING-1: it returns only the bare sender address, concatenates all text parts, and picks mail by date instead of UNSEEN. Polling replaces IDLE (Q-8). |
| `select-n8n-content-generic` | ING-2 | Same script task (`ported.select_content`) | Custom | Regex replace; Pebble has no regex replace with these semantics. |
| `map-content` | ING-3 | Same script task (`ported.map_content`) | Custom | JS port; V8 date parsing and JS whitespace rules reproduced in `jscompat.py`. |
| `insert_raw_news` | ING-4 | `jdbc.postgresql.Batch` from `rows.ion` | Native | Prepared `?` statement; `?::public.email_category` cast because values bind as text. One transaction, like n8n's single-query batching (Q-19). |

## Workflow 2: `email-summarize-send`

| n8n node | Spec ID | Tool construct | Native / custom | Notes |
|---|---|---|---|---|
| `10min-runner` | trigger | `core.trigger.Schedule` `*/10 * * * *` | Native | No concurrency limit, as in n8n (Q-16). |
| `pg-get-dedup-raw-news` | SUM-1 | `jdbc.postgresql.Query`, `fetchType: FETCH_ONE` | Native | SQL verbatim. NULL columns are left out of `row`. |
| `results-not-empty` | SUM-2 | `core.flow.If` on `outputs[...].size > 0` | Native | |
| `transform-content` | SUM-3 | `scripts.python.Commands` task `transform-content` (`ported.transform_content`) | Custom | Also restores NULL columns the JDBC row dropped (G-3). |
| `prompt-builder` | SUM-4 | Same script task (`ported.build_prompt`) | Custom | Model names from `GEMINI_MODEL` / `GEMINI_MODEL_FALLBACK`. |
| `ai-summary` + `gemini-flash` + `gemini-flash-fallback` | SUM-5 | Two `ai.completion.ChatCompletion` tasks with the `ai.provider.GoogleGemini` provider: the primary with `allowFailure`, the fallback with `runIf` on the primary's missing `textOutput` | Native | `baseUrl` points at mock-gemini. `plugin-ai`'s `Fallback` provider isn't used: it doesn't fall back on 4xx errors, which n8n does. `plugin-gemini` has no base-URL override. |
| `combine-results` + `pg-update-ai-summary` | SUM-6 | `core.flow.If` + `jdbc.postgresql.Query` with named `parameters` | Native | `:news_id::bigint` etc.: Pebble-rendered parameters bind as `varchar`. |
| `get-error` + `pg-update-ai-error` | SUM-7a | `If` else-branch + `jdbc.postgresql.Query` | Native | Error text from `errorLogs()`; model = primary (D-008). |
| `telegram-send-failed-exec` | SUM-7b | `core.http.Request` POST to `sendMessage`; template in flow `variables`, rendered with `render()` | Native (core HTTP) | `plugin-telegram`'s `TelegramSend` can't set `disable_web_page_preview` (n8n sends it). No n8n footer (D-019). |
| `pg-drop-dups` | DUP-1 | `jdbc.postgresql.Query` | Native | SQL verbatim. |
| `pg-get-summary` | SND-1 | `jdbc.postgresql.Query`, `FETCH_ONE` | Native | SQL verbatim. |
| (none: n8n runs nothing without items) | SND-1a | `core.flow.If` on `size > 0` | Native | |
| `format-as-markdown` | SND-2 | `scripts.python.Commands` task `format-as-markdown` (`ported.format_as_markdown`) | Custom | JS regex semantics and UTF-16 lengths reproduced. |
| `telegram-send-summary-markdown` | SND-3 | `core.http.Request` with `options.allowFailed` and `allowFailure` | Native (core HTTP) | `TelegramSend` returns no response, and SND-4 stores Telegram's rendered `result.text`. |
| `pg-update-summary-sent` | SND-4 | `If` on `code == 200` + `jdbc.postgresql.Query` | Native | `formatted_summary` = `fromJson(body).result.text`. |
| `pg-update-sent-error` | SND-5 | `If` else-branch + `jdbc.postgresql.Query` | Native | `sent_status` = Telegram's `description`, or the error log on a network failure. |

## Count

| | Native | Custom |
|---|---|---|
| n8n nodes (23, without sticky notes) | 17 | 6: `fetch-n8n-inbox`, `select-n8n-content-generic`, `map-content`, `transform-content`, `prompt-builder`, `format-as-markdown` |
| Kestra tasks (20, plus 2 schedule triggers) | 17 | 3 script tasks, each covering one or more of the 6 nodes |

Custom code: 4 Python modules plus `__init__.py`, 900 lines including comments and docstrings,
all of it the logic n8n kept in its IMAP trigger and Code nodes. YAML: 72 lines
(`email-ingest`) and 303 lines (`email-summarize-send`), including comments and the verbatim SQL.
