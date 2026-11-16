# Capability probes

Two small probes per tool for the needs of the Retool Workflows replacement
([D-022](decisions.md)) that the n8n workflow doesn't exercise. They aren't parity scenarios
and aren't part of the summarizer. Each tool's summarizer deploy (`make kestra-deploy`,
`make prefect-deploy`) leaves them alone.

| Probe | Question | Kestra | Prefect |
|---|---|---|---|
| **long-query** | Does a single Postgres statement longer than Retool Workflows' 600 s limit finish? | [`probe-long-query.yml`](../kestra/probes/probe-long-query.yml): the JDBC `Query` task runs `select pg_sleep(:seconds::int)` | `probe_long_query` in [`probes.py`](../prefect/src/email_summarizer/probes.py): the `SqlAlchemyConnector` block runs `select pg_sleep(:seconds)` |
| **webhook** | Can an upstream system start a run with an HTTP call, as Retool workflows' webhook triggers allow, and get an answer? | [`probe-webhook.yml`](../kestra/probes/probe-webhook.yml): the core `Webhook` trigger, with an HMAC-SHA256 signature check (`WEBHOOK_SIGNING_SECRET`), `wait: true` and `returnOutputs: true`; the flow registers the batch and returns the row | OSS has no webhooks (Cloud only). The caller posts a `probe.batch.announced` event to `POST /api/events`; a deployment trigger in [`probes.yaml`](../prefect/probes.yaml) maps its payload to the `probe_webhook` flow's parameters |
| **gate** | Can a run wait on a Postgres control table, then for a person to enter values and a file, then validate the file and write the outcome back? | [`probe-gate.yml`](../kestra/probes/probe-gate.yml): `LoopUntil` + `Pause` with `onResume` inputs (STRING, SELECT, FILE) + a Python script task + JDBC `Query` | `probe_gate`: a polling task + `pause_flow_run(wait_for_input=Review)` + a validation task that publishes a table artifact + an UPDATE task |

The control table is `probe.batch` in app-db (`id`, `status`, `approver`, `decision`,
`rows_total`, `rows_valid`, `decided_at`), created by the runner. Its statuses are `PENDING`,
then `READY`, then `APPROVED`, `REJECTED` or `INVALID`.

## Running them

```bash
make probe-kestra                 # or probe-prefect: starts the stack if needed, about 12 minutes
make probe-kestra PROBE_ARGS="--probes webhook"   # one probe; a subset updates the day's results file
make screenshots-review-kestra    # or -prefect: the human step done through the UI form, captured
```

[`scripts/probes.py`](../scripts/probes.py) deploys the probes, recreates the control table,
and starts the long statement. While the statement runs, it does the gate twice: batch 1 with a
good CSV and batch 2 with a bad one. For each batch, it:

1. inserts the row as `PENDING` and starts the run;
2. turns the row `READY` after 20 s, as an upstream process would;
3. waits for the run to ask for review;
4. resumes it through the tool's API with what the UI form would send: a name, `APPROVE`, and
   the CSV;
5. checks the control table.

The results go to `docs/comparison/data/probes-<tool>-<date>.json`.

`make screenshots-review-<tool>` holds a gate run at its review step (`probes.py --hold 99`).
Then [`screenshots.py`](../scripts/screenshots.py) fills in and submits the tool's own resume
form in a browser, and captures the form and the validation result.

## Results (2026-11-15)

| | Kestra 2.0.10 | Prefect 3.9.3 |
|---|---|---|
| `select pg_sleep(660)` | `SUCCESS` after 660.6 s; 11 min 0.6 s in an earlier run | `COMPLETED`; run time 660.3 s, 675 s from the API call including the worker's pickup |
| Timeouts set | None | None |
| While the control row is `PENDING` | Execution shows `PAUSED`: `LoopUntil` pauses it between checks | Run shows `Running`: the poll is a task |
| From `READY` to asking for review | 4.0 s (5 s poll interval) | 4.1 s (5 s poll interval) |
| The review form | Drawn from `onResume`: a code-editor text field, a dropdown, a **file chooser** ([form](comparison/data/screenshots/kestra-review-01-form.png)) | Drawn from the `RunInput` model: a text box, a dropdown, and a text box for the CSV, **no file upload**; Resume is in the run page's menu ([form](comparison/data/screenshots/prefect-review-01-form.png)) |
| Good CSV (3 valid rows) | `APPROVED`, 3 of 3 valid | `APPROVED`, 3 of 3 valid |
| Bad CSV (two bad rows) | `INVALID`, 1 of 3 valid | `INVALID`, 1 of 3 valid |
| Where the reviewer sees the validation | A `validation-report.json` output file, with Preview and Download on the run's Input/Output tab ([shot](comparison/data/screenshots/kestra-review-02-outputs.png)) | A table artifact on the run's Artifacts tab and the Artifacts page ([shot](comparison/data/screenshots/prefect-review-03-artifact-table.png)) |
| Through the UI | Form filled in and submitted in the browser; batch 99 `APPROVED` by "ui reviewer" | The same |
| Data | [probes-kestra](comparison/data/probes-kestra-2026-11-15.json) | [probes-prefect](comparison/data/probes-prefect-2026-11-15.json) |

### Webhook (2026-11-16)

| | Kestra 2.0.10 | Prefect 3.9.3 |
|---|---|---|
| Endpoint | `POST /api/v1/main/executions/webhook/{namespace}/{flow}/{key}` (also GET and PUT; multipart parts land in internal storage) | `POST /api/events` with a list of events; a deployment trigger turns a matching event into a run |
| How the caller authenticates | The key in the URL, plus an HMAC signature of the body when `signature` is set. The server's login isn't needed. | The server's basic-auth string (`PREFECT_API_AUTH_STRING`) |
| Unauthenticated call | 401 "Invalid webhook signature", unsigned or wrongly signed, before any execution exists | 401 "Unauthorized" without the auth string |
| Authenticated call | 200 in 0.35 s, answered with the flow's outputs: `{"batch":{"id":7,"status":"PENDING"}}` | 204 in 0.01 s, with no body: no run id, no result |
| Row in the control table | 0.1 s after the response, which already contained it | 3.1 s and 11.2 s after the call, in two runs: the worker's 10 s poll |
| Data | [probes-kestra (11-16)](comparison/data/probes-kestra-2026-11-16.json) | [probes-prefect (11-16)](comparison/data/probes-prefect-2026-11-16.json) |

Kestra's trigger can also wait without returning outputs, set the response code and content
type, filter calls with a `when` condition, and verify HMAC-SHA1, SHA256 or SHA512 with an optional
prefix (GitHub's `sha256=`). The signature protects integrity, not against replays: the
property's own description says so. Prefect's route gives the caller nothing back, so a caller
that needs the result must create the run through the REST API and poll it. Prefect Cloud has
real webhooks with payload templates.

## What the probes found along the way

- **Kestra: a paused execution isn't necessarily waiting for a person.** `LoopUntil`
  (formerly `WaitFor`; the old name still works) puts the execution in `PAUSED` between its
  checks. The first runner looked only at the execution state. It resumed during the
  control-table wait, which just triggered an early check, and the real review step then
  waited forever. Watch the `Pause` task's own state; the runner now does.
- **Kestra: text inputs in the resume form are a code editor**, because inputs accept
  expressions. The file chooser is a normal one. The resume API is
  `POST /api/v1/main/executions/{id}/actions/resume`, `multipart/form-data`, files included.
  Kestra's plugin page gives the path without `/actions`.
- **Prefect: no file upload in a paused run's form, and Resume is behind the run page's
  menu** in the 3.9 UI. A field typed `str` is drawn as a text box, so the probe asks the
  reviewer to paste the CSV. For real files, upload them somewhere else, such as object storage
  or a Retool app, and put the path in the form.
- **Prefect: `pause_flow_run` gives up after one hour by default.** The probe sets two hours.
  For longer waits, `suspend_flow_run` releases the worker process and has no default timeout,
  but needs a deployment and persisted results. It wasn't probed here.
- **Both: nothing had to be configured for a long statement.** Neither tool sets a default
  timeout on tasks, runs or queries. If you want a limit, set one: Kestra's `timeout` or an SLA;
  Prefect's `timeout_seconds`. Prefect's timeout can't interrupt a blocking database call, so
  use Postgres `statement_timeout` for that.
