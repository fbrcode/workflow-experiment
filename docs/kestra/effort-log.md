# Kestra effort log

Dated record of friction, errors, doc gaps and workarounds while building the Kestra
implementation. It's primary evidence for C4 (authoring effort), C5 (developer loop) and C13
(learning curve). Times are wall-clock on the dev host (Apple Silicon, Docker Desktop).

## 2026-11-14: Phase 3 build

**Time:** about 1 h of wall-clock time (19:45 to 20:46 -03), from the first version check to a
verified clean run, with the happy path and the fault paths checked. Most of it went into finding out what Kestra 2.0 actually accepts, and into reading
n8n's source for behavior the spec hadn't pinned down. The second part benefits Prefect too.

### Versions and image

| What happened | Detail |
|---|---|
| The current release is newer than what I knew | Kestra 2.0.10 (2026-11-10) is `latest` and `latest-lts`; 1.3.x is still maintained and 2.1 is in RC. 2.0 has breaking changes (RFC 9457 API errors, task outputs moved out of the execution JSON). Everything below was checked against the running 2.0.10, not memory. |
| The full image is huge | `kestra/kestra:v2.0.10` is about 5 GB compressed (every plugin). `-no-plugins` is 243 MB. A custom image with 3 plugins is 830 MB, built in 2 min 12 s. `plugin-ai` alone is 291 MB. |
| Plugin versions had to be discovered | Maven Central's search index stops at 0.23 (mid-2025); `maven-metadata.xml` per artifact is current. `kestra plugins install <plugin>:LATEST` resolves the newest version compatible with the running core, which gave the versions to pin. |
| Plugin docs offline | `kestra plugins doc <dir>` writes Markdown docs for installed plugins. The REST API (`/api/v1/plugins/<class>`) returns the JSON schema of core tasks. Both were more reliable than memory. |

### Choosing plugins: what couldn't be used, and why

| Plugin / task | Problem | Used instead |
|---|---|---|
| `plugin-email` `MailReceivedTrigger` (native IMAP trigger) | Read its source at v1.6.8: `from` is only the bare address (`InternetAddress.getAddress()`), so the display name ING-3 needs is lost; `body` concatenates every text part; mail is selected by received date after the last check, in a READ_ONLY folder, never by UNSEEN and never marked read. | Schedule trigger + Python script task that reproduces n8n's IMAP read (`imap_simple.py`). |
| `plugin-gemini` `ChatCompletion` | No base-URL property, so it can't target the mock. | `plugin-ai` `ChatCompletion` with the `GoogleGemini` provider, which has `baseUrl`. |
| `plugin-ai` `Fallback` provider | Falls back only on connection failures, timeouts, 5xx and 429. n8n's agent falls back on any error, including 4xx (a retired model returns 404). | Two `ChatCompletion` tasks: primary with `allowFailure`, fallback with `runIf`. |
| `plugin-telegram` `TelegramSend` | Returns no outputs (the send method is `void`), so SND-4 can't store Telegram's rendered `result.text`. It also can't send `disable_web_page_preview`, which n8n's node sends. | Core `io.kestra.plugin.core.http.Request` for both Telegram calls; plugin dropped from the image. |

### Kestra 2.0 surprises (doc vs reality)

| Surprise | Workaround |
|---|---|
| Flow-level `pluginDefaults` is rejected: `Unrecognized field "pluginDefaults"`. The 2.0 release notes and the UI's Blocks editor still mention it. | Each task carries its own settings (DB URL and credentials, task runner, container image). More YAML. |
| Concurrency `behavior: SKIP` doesn't exist; the values are `QUEUE`, `CANCEL`, `FAIL`. | `CANCEL` for the ingest poll. |
| Secrets in OSS are only `SECRET_<NAME>` environment variables, base64-encoded. Compose can't encode. | A 15-line entrypoint shim encodes the listed variables at start. |
| JDBC `Query.parameters` rendered by Pebble bind as `varchar`: `operator does not exist: character varying = integer`. | Explicit casts in our SQL (`:news_id::bigint`, `?::public.email_category`). The verbatim baseline queries have no parameters. |
| A JDBC `FETCH_ONE` row leaves NULL columns out entirely, and `toJson` drops null map entries. Python would then see `undefined` where n8n saw `null` (G-3). | `task.py` puts the expected columns back as `None`; tested with the vectors. |
| Task outputs are no longer in `GET /executions/{id}` (`outputs: null`); they're at `/outputs/tasks/{executionId}/{taskRunId}`. | Noted for the parity suite. |
| Pebble string literals don't interpret `\n`. The first alert had literal backslash-n sequences. | The alert template lives in a YAML block in `variables` and is rendered with `render()`. |
| `outputs.<failed task> is defined` is true even for a failed task. | The fallback tests `outputs['gemini-flash'].textOutput is not defined` instead. |
| Kestra's own compose bind-mounts `/tmp/kestra-wd` for the Docker task runner. In 2.0 the runner's default `fileHandlingStrategy` is `VOLUME`, so the mount isn't needed. | Not mounted; no host directory is left behind. Task containers and volumes are cleaned up after each run. |
| An inline Python command containing `: ` broke YAML parsing (plain scalar). | Block scalars for commands. |
| The HTTP task's default connect timeout is long: a stopped Telegram mock took 71 s to fail. | Left at the default; noted for C7. |

### What went smoothly

- `make kestra-up` reaches healthy in 12 s once the image exists; flows and namespace files deploy
  through the REST API in about a second.
- The REST API is complete enough for GitOps (`POST /flows/{namespace}?delete=true`), and
  `POST /executions/{ns}/{id}?wait=true` runs a flow synchronously, which made testing quick.
- `allowFailure`, `runIf`, `If` and `errorLogs()` were enough to express n8n's error outputs
  exactly, including "an unhandled error stops the run".
- The Python port passed all golden vectors on its first run (136 cases), and the IMAP port
  matched n8n's ING-1 output for all 9 fixtures on its first run.
- A scheduled poll overlapping a manual run is handled by the concurrency limit.

### Developer loop (C5)

- Logic changes: edit `kestra/files/summarizer/*.py`, run `make test-kestra` (0.1 s, no Kestra
  needed), then `make kestra-deploy` (about 1 s).
- Flow changes: edit the YAML, `make kestra-deploy`, then `make kestra-run` for a synchronous run
  that prints each task's state and the error logs. A summarize tick takes 2 to 5 s, about 0.6 s
  of it per Python task container start.
- Pebble expressions can't be unit-tested outside Kestra. The SUM-7b template was checked end to
  end against the golden vector.

### Findings about n8n that the spec was missing (now in D-019)

Reading the n8n 2.40.3 source while mapping nodes turned up: unhandled errors stop the run (the
spec said branches were independent); the Telegram node's `disable_web_page_preview` and HTML
attribution footer; how `sent_status` gets Telegram's description; the LangChain retry policy
(6 retries with backoff); and single-transaction ingest batches, which make same-batch duplicates
survive DUP-1 (Q-19).

## 2026-11-15: Parity suite (Phase 5)

`make parity-kestra` passes all 13 scenarios ([parity.md](../parity.md); the recorded run is in
[comparison/data/](../comparison/data/)). Driving Kestra from the suite took about 75 lines of
HTTP calls. What the suite showed about Kestra:

| Observation | Detail |
|---|---|
| A failed DNS lookup outlives the outage | The image's JVM (OpenJDK 25.0.5) keeps a failed lookup for 10 s (`networkaddress.cache.negative.ttl=10` in `java.security`). Measured over 4 trials (app-db stopped, one tick, app-db restarted and healthy 3.5 s later, then ticks back to back): when the failed tick took 10 s, every run failed with `The connection attempt failed` / `app-db` until 9.8 s after it ended (55 runs in a row), then worked; when it took 20 s, the first run 3.5 s later worked. So the window ends 10 s after the last failed lookup. In the suite's first development session, F-4's recovery tick and the four scenarios after it (each failing in about 0.1 s) all ran inside that window. A real tick comes 10 minutes later, so the suite lets an outage settle for 15 s (D-020). |
| Telegram unreachable takes 73 to 74 s | With mock-telegram stopped, SND-5 stored `No route to host (connect failed)` after 73 to 74 s (71 s in Phase 3). "No route" means the client still had an address, most likely the JVM's cached lookup from an earlier call (successful lookups are kept 30 s by default). Prefect failed the same call at name resolution. |
| Database down | The tick failed after 13 to 20 s, with no writes and no calls. |
| Retries and error text | 3 attempts per model before the fallback or the error path. `summary_status` and the alert's error are the response's whole JSON body (`{"error": {"code": 500, ...}}`), not its message. Both are allowed (D-019). |
| Run states | `WARNING` when a Gemini call or the Telegram connection failed and the flow carried on; `SUCCESS` when Telegram answered 400 (F-3). The suite counts both as n8n's success (D-020). |
| Pausing the schedules | `PUT /api/v1/main/triggers/set-disabled` per trigger. Re-enabling without `recoverMissedSchedules` skips the ticks missed meanwhile (stated in the API description), so a parity session doesn't cause a burst of catch-up runs. |
| Run time | A summarize tick takes 1.5 to 4 s from the API call to the end state; an ingest about 1 s. |

## 2026-11-15: Measurements and screenshots (Phase 6)

The numbers and scores are in [comparison/results.md](../comparison/results.md); the raw data is
in [comparison/data/](../comparison/data/). What the measurements and the UI walkthrough showed:

| Observation | Detail |
|---|---|
| Clean build | With an empty BuildKit builder, the build (base images, the three plugins, the mocks) takes 34 s; a clean clone reaches a green run in 69 s. Phase 3's 2 min 12 s build included the first downloads on that day's network. |
| Memory | The JVM holds about 600 MiB at idle and 710 MiB at peak; with `kestra-db`, 699 and 825 MiB. Idle CPU is about 20% of a core: the JVM and its queue polling Postgres. |
| Speed | A tick runs in 1.6 to 1.9 s end to end. A changed namespace file is live after a 0.3 s deploy. |
| Topology view | The execution overview fits the whole flow into the pane, so a 20-task flow is too small to read until you zoom in (mouse wheel) and turn on "Show more details". |
| Client retries are invisible | plugin-ai retries a model call inside one task attempt, so the UI shows one failed attempt where the mock saw three calls. |
| Outputs | Every task output is browsable, including the model's answer and token use, with an expression debugger next to it. |
| Metrics | `:8081/prometheus` is on by default, with per-flow execution durations and task-run counts (`kestra_executor_*`). |
| UI and the internet | The UI calls `api.kestra.io` and keeps PostHog analytics keys in the browser's local storage, although the server has anonymous usage reporting off. The screenshot browser blocked every outside request. |
| Logging in from a script | The login form ignores Playwright's `fill()`; it needs real keystrokes (`press_sequentially`). |

## 2026-11-15: Capability probes for the Retool replacement (D-022)

[probes.md](../probes.md) has the results. Friction and surprises:

| Observation | Detail |
|---|---|
| `WaitFor` was renamed | The task is now `io.kestra.plugin.core.flow.LoopUntil`. `WaitFor` still works as an alias, and the running server's plugin API serves its schema under that name, so the rename is easy to miss. The probe uses the new name. |
| A paused execution isn't necessarily waiting for a person | `LoopUntil` puts the whole execution in `PAUSED` between its checks. The first probe runner resumed on "execution is `PAUSED`". That only triggered an early check (`RESTARTED` in the history), and the real `Pause` then waited forever. Check the `Pause` task's own state. |
| The resume API path | `POST /api/v1/main/executions/{id}/actions/resume`, `multipart/form-data`, files included. The plugin page gives the path without `/actions`. The kill action is `DELETE .../executions/{id}/actions/kill` as well. |
| The resume form | Drawn from `onResume` with no code: a STRING is a Monaco code editor (inputs accept expressions), a SELECT is a dropdown, a FILE is a normal file chooser. Driving it from a browser script needs keystrokes in the editor. |
| Live executions in the UI | The page of a running or paused execution keeps a stream open, so waiting for network idle never finishes. Wait for the page load instead. |
| Long statement | `select pg_sleep(660)` through the JDBC task needed no setting: `SUCCESS` after 660.6 s. The JDBC plugin has no query-timeout property. |
