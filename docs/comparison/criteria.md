# Comparison framework: Kestra vs Prefect

How the two implementations of the [baseline spec](../baseline/workflow-spec.md) are judged.
Results go in `docs/comparison/results.md` (created in Phase 6). Raw measurements go in
`docs/comparison/data/`.

> The external reference doc linked by the project owner couldn't be fetched (it renders
> client-side). If it defines criteria, paste its content into `docs/reference/` and reconcile
> this list with it. Log that change in [`../decisions.md`](../decisions.md).

## Ground rules

1. **Evidence or it didn't happen.** Every score links to a measurement, a file, a log excerpt or a doc page.
2. **Same conditions.** Same machine, same Docker resource limits, same fixtures, same mocks, and
   one stack running at a time during measurements. Record the date, host and tool versions with every number.
3. **Parity first.** Ergonomics scores only count once the parity suite passes. An implementation
   that is easier to write because it skipped behavior isn't easier.
4. **Idiomatic implementations.** Each tool is used the way its docs recommend. A tool isn't
   penalized for lacking something n8n-specific unless the spec needs it.

## Dimensions

| # | Dimension | What we measure / observe | Method |
|---|---|---|---|
| C1 | **Behavioral parity** | Share of spec requirements met (`ING-*`, `SUM-*`, `DUP-*`, `SND-*`); deviations needed | Parity suite + golden vectors; deviations from `decisions.md` |
| C2 | **Local setup** | Number of compose services, compose LOC, images and their total size, time from clean clone to first green run | `docker compose config`, `docker image ls`, timed `make <tool>-up` from a clean state |
| C3 | **Resource footprint** | Idle RAM/CPU after 5 min; peak during a scenario run; time to healthy | `docker stats --no-stream` sampled; healthcheck timing |
| C4 | **Authoring effort** | LOC of flow definitions and glue code; native plugin vs. custom script per n8n node; implementation time and friction log | `cloc`/`wc`; [node mapping table](#node-mapping-template); per-tool `effort-log.md` |
| C5 | **Developer loop** | Time to change logic, redeploy and see the result; local unit-testing story; type safety and linting | Timed edit-run cycle; whether the golden tests run outside the orchestrator |
| C6 | **Triggers & scheduling** | Native IMAP or email trigger? Interval or cron schedule? Protection against overlapping runs (Q-16)? Backfill? | Docs + what we actually built |
| C7 | **Error handling** | Retries, model fallback (SUM-5), error branches (SUM-7, SND-5), alerting; how much code each needs | Implementation + fault-injection scenarios (mock returns 500s) |
| C8 | **Secrets & config** | How credentials are stored and injected in the **OSS** edition; per-environment config | Implementation |
| C9 | **Observability** | Run history, per-task logs, inputs and outputs, retries visible in the UI, metrics endpoint | UI walkthrough with screenshots in `docs/comparison/data/` |
| C10 | **LLM integration** | Native Gemini support, provider swap, base-URL override (needed for the mock), fallback support | Implementation |
| C11 | **Version control / GitOps** | Flows as code, deploying from the repo, diff readability, drift between UI and repo | Implementation |
| C12 | **Operations & scaling** | Architecture (server, workers, DB, queue), HA story, upgrade path, OSS vs. enterprise gating, license | Docs + compose |
| C13 | **Learning curve & team fit** | Concepts needed (DSL vs. Python), docs quality, debuggability for someone coming from n8n | Friction log + qualitative notes |

## Scoring

- Each dimension gets a score from **1 to 5** per tool, with a one-line reason and links to evidence.
- Default weights are below. The owner can change them before the final tally, and a change
  should be recorded in `decisions.md`.

| Dimension | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 | C10 | C11 | C12 | C13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Weight | gate | 1 | 1 | 2 | 2 | 1 | 2 | 1 | 2 | 1 | 1 | 1 | 2 |

C1 is a **gate**. A tool that can't reach parity, after documented deviations, is reported as such.
It isn't averaged in.

## Fault-injection scenarios (for C7)

| ID | Scenario | Expected (per spec) |
|---|---|---|
| F-1 | Primary model returns an error, fallback succeeds | SUM-6, `model_used` = primary (Q-2) |
| F-2 | Both models fail | SUM-7a row update + SUM-7b alert |
| F-3 | Telegram rejects the MarkdownV2 message (400) | SND-5; row is summarized again on the next tick |
| F-4 | Postgres unavailable during a tick | Run fails visibly; next tick recovers; no partial writes |
| F-5 | Duplicate email ingested | DUP-1 deletes it; only one Telegram message |
| F-6 | Empty queue | No writes, no Telegram calls (SUM-2, SND-1a) |
| F-7 | 5 failures in a row | Row stops being retried (`*_retry < 5`) |

## Node mapping template

Each tool doc (`docs/kestra/`, `docs/prefect/`) contains this table, filled in:

| n8n node | Spec ID | Tool construct | Native / custom | Notes |
|---|---|---|---|---|
| `fetch-n8n-inbox` | ING-1 | | | |
| `select-n8n-content-generic` | ING-2 | | | |
| `map-content` | ING-3 | | | |
| `insert_raw_news` | ING-4 | | | |
| `10min-runner` | trigger | | | |
| `pg-get-dedup-raw-news` | SUM-1 | | | |
| `results-not-empty` | SUM-2 | | | |
| `transform-content` | SUM-3 | | | |
| `prompt-builder` | SUM-4 | | | |
| `ai-summary` + 2× Gemini | SUM-5 | | | |
| `combine-results` + `pg-update-ai-summary` | SUM-6 | | | |
| `get-error` + `pg-update-ai-error` | SUM-7a | | | |
| `telegram-send-failed-exec` | SUM-7b | | | |
| `pg-drop-dups` | DUP-1 | | | |
| `pg-get-summary` | SND-1 | | | |
| `format-as-markdown` | SND-2 | | | |
| `telegram-send-summary-markdown` | SND-3 | | | |
| `pg-update-summary-sent` | SND-4 | | | |
| `pg-update-sent-error` | SND-5 | | | |
