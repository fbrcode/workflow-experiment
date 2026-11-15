# Prefect effort log

Dated record of friction, errors, doc gaps and workarounds while building the Prefect
implementation. It's primary evidence for C4 (authoring effort), C5 (developer loop) and C13
(learning curve). Times are wall-clock on the dev host (Apple Silicon, Docker Desktop).

## 2026-11-15: Phase 4 build

**Time:** about 35 minutes (01:55 to 02:30 -03) from the first version check to a verified clean
run with all scenarios checked and the scheduled runs observed; the docs were written alongside.

**Read this number with care.** The same author built Kestra the day before. The Prefect port was
written fresh from the spec, the baseline JSON, the golden vectors and the n8n package sources,
without opening the Kestra code (CLAUDE.md rule 3). But the n8n research was already done: what
the spec got wrong (D-019), what n8n's IMAP code does to headers and bodies, which JavaScript
behaviors the vectors depend on. That knowledge doesn't carry over to a team that picks only one
tool. For C4 the useful comparison is the orchestration work (flows, deployment, configuration,
the stack), not the porting of the n8n logic.

### Versions

| What happened | Detail |
|---|---|
| Current release | Prefect 3.9.3 (2026-11-13) on PyPI and Docker Hub, with arm64 images for Python 3.11 to 3.15. `3.9.4.dev1` already exists; 3.9.3 is the latest stable. |
| Docs moved | docs.prefect.io now serves from prefect.io/docs/v3, and every page has a `.md` version listed in `llms.txt`. That made checking settings, CLI flags and YAML keys quick. |
| CLI and settings checked in the image | `prefect config view --show-defaults`, `prefect <command> --help` and the server source in the venv answered every question the docs left open (for example, which paths basic auth exempts). |

### Choosing integrations: what was used, and what wasn't

| Need | Prefect option | Decision |
|---|---|---|
| Read IMAP (ING-1) | No email trigger. `prefect-email` 0.4.3 only sends over SMTP. | Schedule + task with stdlib `imaplib`/`email`; n8n's decoding rules ported from `@n8n/imap` 0.26.0 and `imap` 0.8.19 (read from the npm tarballs). |
| Postgres | `prefect-sqlalchemy` 0.6.1 (`SqlAlchemyConnector` block, credentials stored in Prefect) | Used. `execute_many` gives one transaction per batch, as Q-19 needs. |
| Gemini | No Prefect integration. The Pydantic AI integration (`PrefectDurability` capability, which replaced `PrefectAgent`) is Prefect's documented AI path, with a `FallbackModel` that matches n8n's agent | Not used: it is built for agents with tools and caches model calls as tasks, more machinery than one prompt with a fallback needs. google-genai, Google's SDK, in a plain task. |
| Telegram | No integration | httpx in a task. |
| Secrets | `Secret` blocks (encrypted at rest), or env vars on the worker | Blocks, written from `.env` at deploy time. |
| Execution | `serve()` in a long-running process, or work pool + worker (process, Docker, Kubernetes, …) | Process work pool + worker in a container, the shape of Prefect's own compose guide. A Docker work pool would mirror Kestra's per-task containers but needs the Docker socket and an image per run. |

### Surprises (doc vs reality)

| Surprise | Workaround |
|---|---|
| `Secret.get()` runs `json.loads` on the stored string. The alert chat id `-1009999999999` came back as an int and went to Telegram as a JSON number, where n8n sends a string. A numeric password or `"true"` would change type too. The docs only show `.get()`. Caught by checking the recorded Telegram call in F-2. | `Secret.load(name).value.get_secret_value()`, which returns the stored string. |
| `SqlAlchemyConnector.fetch_one` keeps the result set and its transaction open, and a repeated call with the same SQL returns the *next* row instead of re-running the query (documented in the docstring, easy to miss). | Each task opens the connector in a `with` block, so connections close when the task ends. |
| A redeploy (`prefect deploy --all`) re-activates schedules that were paused, because `prefect.yaml` declares them. | Expected for a repo-as-source-of-truth setup; the manual scenarios paused them again after each redeploy. |
| `prefect deployment schedule pause --all` takes no deployment name (the error says so). | `pause --all` for every deployment. |
| google-genai 3.3.0 prints two `SyntaxWarning: invalid escape sequence` on import, in every new process. | `UV_COMPILE_BYTECODE=1` in the image: compiled once at build time, quiet afterwards, and each flow run's subprocess starts from bytecode. |
| The scheduler books runs about an hour ahead, so the run list shows ~60 future ingest runs right after deploying. | Nothing needed; pausing a schedule removes them. |
| The worker's healthcheck server answers before the worker has created its work pool, so "worker healthy" doesn't mean the pool exists. | `deploy.sh` creates the pool itself (`work-pool create --overwrite`). |
| Writing the Python files: `\uXXXX` escapes in a string literal were turned into the literal (invisible) characters, the same tooling problem noted in Phase 3. Ruff flagged them (RUF001). | The JavaScript whitespace table is built from code points (`chr`), which also reads better. |

### What went smoothly

- `make prefect-up` worked on the first attempt: server, worker, deploy, both deployments created
  (47 s with the image build; 21 s from a clean state afterwards).
- The port passed all 106 golden cases on its first correct run (the first run failed on a pytest
  parametrization mistake, not the port), and ING-1 matched all 9 fixtures from the fixture files
  before GreenMail was involved.
- n8n's error semantics map directly onto task states: `return_state=True` for the two handled
  errors, plain exceptions for everything else. A run with a handled failure ends `Completed`,
  as n8n reports it, and the failed task run is still visible.
- The deployment concurrency limit with `CANCEL_NEW` did exactly what the ingest needs.
- Every scenario matched n8n on the first run once the chat-id type was fixed: F-1, F-2 (alert
  equals the golden vector), F-3, network error, Q-15, F-5, F-6.

### Hands-off run after a clean start

`make prefect-down`, `make prefect-up` (02:22:11, 21 s), `make prefect-seed`, `make test-prefect`
(128 passed), then nothing but the schedules:

| Started | Flow run | State | Run time |
|---|---|---|---|
| 02:23:01 | `email-ingest` (scheduled) | Completed: the 9 fixtures inserted | 0.79 s |
| 02:24:00 to 02:30:00 | `email-ingest` (scheduled, every minute) | Completed: no new mail | 0.26 to 0.33 s |
| 02:30:00 | `email-summarize-send` (scheduled) | Completed: row 1 summarized and sent (one Gemini call, one Telegram call at 02:30:01) | 0.61 s |

### Developer loop (C5)

- Logic changes: edit `prefect/src/email_summarizer/*.py`, `make test-prefect` (0.1 s, no
  Prefect needed). The alert text is plain Python, so SUM-7b is unit-tested too.
- To run changed code in the stack: `make prefect-up` rebuilds the worker image, recreates the
  worker and redeploys: 21 s. There is no hot reload: the worker runs what is installed in its image.
- `make prefect-run` triggers a deployment from the host and prints each task's state. Runs start
  5 to 15 s after the trigger, because the worker polls every 10 s and each run is a new Python
  process; the tasks of a tick take about 0.5 s.

### n8n facts that reached the spec before this phase

None new. Phase 3 had already corrected the spec from the n8n source (D-019), and this port
found nothing that contradicts it. Reading `@n8n/imap`'s `PartData` and `uudecode.js` again
added one detail the Prefect port now follows: a `UUENCODE` part drops its first line and last
three before decoding.

## 2026-11-15: Parity suite (Phase 5)

`make parity-prefect` passes all 13 scenarios ([parity.md](../parity.md); the recorded run is in
[comparison/data/](../comparison/data/)). Driving Prefect from the suite took about 75 lines of
HTTP calls against the server's REST API, with no Prefect SDK. What the suite showed about Prefect:

| Observation | Detail |
|---|---|
| A run orphaned by a worker restart stays `Running` | The suite resumes the schedules when it ends. A scheduled ingest started at 14:35:00.50, and `make prefect-up` replaced the worker container 0.1 s later to install a new image. The run's process died with it, and the server kept the run `Running`; the next session's idle check timed out on it after 5 minutes. Prefect 3.9.3 emits a heartbeat every 180 s by default (`PREFECT_FLOWS_HEARTBEAT_FREQUENCY`), but nothing acts on missing heartbeats until you create an automation. Prefect Cloud has a one-click "unresponsive run detection" policy; self-hosted servers need the automation from the docs ([detect zombie flows](https://www.prefect.io/docs/v3/advanced/detect-zombie-flows)). This stack doesn't configure one; the suite now cancels such leftovers at session start (D-020). Evidence for C12. |
| Pausing the schedules | `PATCH /api/deployments/{id}/schedules/{schedule_id}` with `{"active": false}` deleted the 68 runs booked ahead at once, so nothing scheduled starts during a session. |
| Run time | Each run takes 4 to 13 s from the API call to the end state, almost all of it waiting for the worker's 10 s poll, so the suite takes about twice as long as on Kestra. |
| Failures fail fast | Database down: the tick is `Failed` about 10 s after the trigger, including the poll. Telegram unreachable: SND-5 stored `[Errno -2] Name or service not known`, since the stopped container's name no longer resolves. |
| Retries and error text | One attempt per model. `summary_status` and the alert's error are the API's message (`Mock fault 500 for models/gemini-2.5-flash`). |
| Run states | `Completed` whenever n8n's execution would succeed, including handled failures; `Failed` for F-4 and Q-15. |
