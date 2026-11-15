# Decision log

Every decision that shapes the experiment is recorded here: scope, assumptions, and deviations
from the baseline spec. Add new entries at the bottom and never delete old ones. If a decision is
reversed, add a new entry that supersedes it.

Statuses: `accepted` · `proposed` (waiting for the owner) · `superseded by D-xxx`

---

### D-001: Tool B is Prefect OSS · `accepted`

"perfect" in the original request means **Prefect**. The owner confirmed this on 2026-11-14.
We use the open-source, self-hosted Prefect server and workers. No Prefect Cloud (see D-006).

### D-002: Spec-first, parity-gated · `accepted`

Both implementations are built from [`baseline/workflow-spec.md`](baseline/workflow-spec.md),
not from each other and not by mechanically translating the n8n JSON. Parity (criterion C1) gates
the comparison.

### D-003: Keep the baseline quirks by default · `accepted`

Quirks Q-1 to Q-16 in the spec keep their baseline behavior unless an entry here says otherwise.
Any deviation is applied the **same way** in both tools.

### D-004: Mocks by default, real services optional · `accepted`

Runs are local and reproducible: GreenMail stands in for IMAP, and HTTP mocks stand in for the
Telegram Bot API and Gemini. A `real` mode using credentials from `.env` is supported as an
end-to-end smoke test only. Measurements use mocks.
*Consequence:* each tool must let us point Telegram and Gemini at another base URL. Whether it
can do that natively is a data point for C10.

### D-005: Keep the two-workflow split · `accepted`

Ingest and summarize/send stay as separate flows in both tools (Q-9). Both tools can express
either shape, and keeping the split keeps the comparison simple.

### D-006: Self-hosted OSS editions only · `accepted`

Kestra open-source and the self-hosted Prefect server: no Kestra Enterprise and no Prefect Cloud.
Features gated behind paid editions are noted under C12 but not used.

### D-007: Reconstructed schema until the real DDL arrives · `superseded by D-018`

We use the schema in [`baseline/data-model.md`](baseline/data-model.md). `translate_language`
defaults to `'Brazilian Portuguese'` as an assumption (Q-14). Replace it when the production
schema is provided.

### D-008: Error-path `model_used` = primary model (Q-3) · `accepted`

In n8n, `pg-update-ai-error` writes `model_used = $json.model` from the error item, which is
probably empty. Both tools write the **primary model name** (`GEMINI_MODEL`) instead. This is a
deliberate deviation from the baseline. The owner confirmed it on 2026-11-14.

### D-009: n8n is not a contestant · `accepted`

n8n is **not** run locally and isn't measured. The `baseline/` exports are used only as the
source of the behavior spec. The comparison covers Kestra and Prefect only. The owner decided
this on 2026-11-14.

### D-010: Redact the admin chat ID in the baseline before publishing · `accepted`

The repo is public. The owner approved one edit to `baseline/EmailSummarizerFromPgCron.json`
before the first commit: in node `telegram-send-failed-exec`, `chatId` was changed from the real
Telegram ID to `"REDACTED_TELEGRAM_ALERT_CHAT_ID"`. No other byte changed. Implementations read
the value from `TELEGRAM_ALERT_CHAT_ID`. Apart from this one approved change, `baseline/` stays read-only.

### D-011: Model names keep the `models/` prefix in config · `accepted`

`GEMINI_MODEL=models/gemini-flash-lite-latest` and `GEMINI_MODEL_FALLBACK=models/gemini-2.5-flash`
are written exactly as the baseline has them. The prefixed string is what n8n stores in `model_used`
(SUM-6, and SUM-7a per D-008) and shows in the Telegram header (SND-2), so parity needs it. Each
implementation strips the prefix only when it builds the API URL. This is a config convention,
not a behavior deviation.

### D-012: The Telegram mock validates and renders like Telegram · `accepted`

The mock parses MarkdownV2 and HTML and rejects invalid text with Telegram-style 400 errors. On
success it returns the rendered plain text in `result.text`. Two reasons: SND-4 stores
`result.text`, so the rendered value matters for parity; and escaping bugs (F-3, Q-15) should
come up naturally rather than only through injected faults. Known approximations are listed in
[infra.md](infra.md#mock-telegram). Phase 7 (real Telegram) is where any gaps would show.

### D-013: Fixtures are loaded with IMAP APPEND, not SMTP · `accepted`

The plan said SMTP. APPEND puts each message straight into the `n8n` folder (the Gmail label)
with no flags, so it's UNSEEN, and keeps the original `Date` as INTERNALDATE. Delivering over
SMTP would put mail in INBOX and need a server-side filter. Ingest behavior doesn't depend on
how mail arrived in the folder.

### D-014: Reference n8n version is 2.47.7 (stable) · `superseded by D-017`

The production n8n version that ran the baseline isn't recorded in the exports. The node
`typeVersion`s are recent and consistent with n8n 2.x. Wherever n8n's own runtime decides behavior
(IMAP decoding, Node/V8), the golden vectors use **n8n 2.47.7**, the stable release on 2026-11-14:
`@n8n/imap@0.33.0` and Node 26.13.0. The IMAP glue in `EmailReadImap/v2/utils.ts` is the same on
master. If the owner's version differs materially, the vectors get regenerated against it.

### D-015: The ING-1 referee is n8n's own IMAP code · `accepted`

Phase 2 originally covered only the Code nodes. Email decoding (ING-1) is just as easy to get
subtly wrong (CRLF, header quoting, RFC 2047), and both tools' DB rows depend on it. So the
generator reads the seeded GreenMail mailbox through the published `@n8n/imap` package, using the
node's search and fetch calls, and records what n8n would hand to ING-2. Golden findings G-1
(CRLF kept) and G-2 (quotes kept) come from this. The spec's ING-1 section now states both.

### D-016: Golden vectors carry a Telegram verdict · `accepted`

SND-2 and SUM-7b cases include `mock_telegram`: what the mock (D-012) does with the message. When
`ok` is true, `result_text` is the expected `formatted_summary` (SND-4). When `ok` is false, the
send fails and SND-5 applies. The verdict is generated, not hand-written, so it moves with D-012.

### D-017: Reference n8n version is 2.40.3 (production) · `accepted`

The owner confirmed on 2026-11-14 that production runs **n8n 2.40.3**. This supersedes D-014.
The golden generator now matches that release:

- `@n8n/imap@0.26.0`, which is the older client built on `imap` 0.8.19, `quoted-printable`, `utf8` and `iconv-lite`. 2.47.7 uses `imapflow` and `libmime` instead.
- The node glue from `EmailReadImap/v2/utils.ts` at tag `n8n@2.40.3`: search `['UNSEEN']`, fetch `{bodies: ['TEXT','HEADER'], markSeen: false, struct: true}`.
- Node 26.7.0, the version in the 2.40.3 image.

Cross-check: regenerating with the 2.40.3 stack changed **none** of the 115 case outputs compared
with the 2.47.7 stack. Only the provenance fields differ. So G-1 (CRLF kept) and G-2 (raw header
quoting) hold on both n8n versions, at least for these fixtures.

### D-018: The local database uses the production schema · `accepted`

The owner provided the production schema on 2026-11-14 as psql `\d+` output
([baseline/prod-schema.md](baseline/prod-schema.md)). This supersedes D-007.
`infra/db/init/001_schema.sql` now reproduces it, and `make schema-check` confirms that app-db's
`\d+` output matches it, ignoring whitespace. What changed compared with the reconstruction:

- `translate_language` defaults to **`'pt-BR'`**, not the assumed `'Brazilian Portuguese'`. That resolves Q-14, and the SUM-4 prompts and SND-2 headers in the golden vectors change with it.
- `summary_retry` and `sent_retry` default to **-1**, not 0, so each `< 5` guard allows 6 attempts (Q-17).
- `summary_status` and `sent_status` default to `'OK'`. `email_category` is `not null default 'tech'`. `to_translate` and `translate_language` are `not null`. `id` is a bigserial, not an identity column.
- There's a new audit table, `news_email_history`, filled by an AFTER INSERT OR UPDATE trigger.
- `telegram_chat` has `chat_name` and `environment` (default `'prod'`), with the primary key `(email_category, environment)`. SND-1 doesn't filter on `environment` (Q-18). Locally there's one `'prod'` row per category, as the workflow needs.

Three pieces aren't in the output, so they stay as they were:

- the enum labels, [inferred] from n8n
- the trigger function's body, [assumed]
- the database locale, the Postgres image default

None of them changes a workflow output, except that the locale can affect `initcap()` on non-ASCII
text (G-9). The golden generator now reads a fresh row's defaults from app-db instead of
hard-coding them, so the vectors follow the schema.

### D-019: Behavior details from the n8n source, and how both tools handle them · `accepted`

Building the Kestra flows (Phase 3) meant reading the n8n 2.40.3 code behind each node. These
findings correct or extend the spec. The JSON and the n8n runtime win over the spec, and every
choice below applies to **both** tools.

**Corrections and additions, verified in the n8n source at tag `n8n@2.40.3`:**

- **Unhandled errors end the run.** Only `ai-summary` (SUM-5) and `telegram-send-summary-markdown`
  (SND-3) have `onError: continueErrorOutput`. If any other node fails, n8n stops the execution,
  and the later branches don't run that tick. The spec said the opposite. Fixed.
- **SUM-7a runs before SUM-7b** (canvas order), so a failed alert still records the error.
- **Telegram node v1.2** sends `disable_web_page_preview: true` on every `sendMessage`, and
  appends a "sent automatically with n8n" footer in HTML or Markdown mode, which covers the
  SUM-7b alert but not the MarkdownV2 summary.
- **SND-5 `sent_status`** is `error.description ?? error.message` of n8n's `NodeApiError`. For a
  Telegram error response that's Telegram's `description` field, as golden.md assumed.
- **The model nodes set no sampling options** (`options: {}`), so Gemini's defaults apply.
- **One ingest batch is one transaction.** The Postgres node v2.6 defaults to
  `queryBatching: 'single'`, so emails read together share `email_consumed_at`, and duplicates
  within a batch tie in DUP-1's `rank()` (new quirk Q-19; F-5 updated).

**Choices, the same in both tools:**

- **Ingest polls every minute** (Q-8). n8n used IMAP IDLE.
- **The n8n attribution footer is left out of the alert** (deviation). It's n8n branding with the
  n8n instance ID in a link, not workflow logic.
- **The alert's `$now` uses America/Sao_Paulo.** The workflows don't set a time zone, so n8n used
  its instance setting, which isn't in the export. GMT-3 matches the newsletters' date logic and
  the golden vector's frozen `$now`. The owner can correct this.
- **Retries inside the model client aren't part of parity.** n8n's LangChain client retries a
  model up to 6 times with backoff (not on 400 to 409) before falling back. Each tool keeps its
  own client's default, and the comparison records it (C7, C10).
- **Error texts aren't compared byte for byte** where they come from a client library: SUM-7a's
  `summary_status`, the alert's `{error}`, and SND-5's `sent_status` after a network failure.
  Telegram's `description` (SND-5 after a rejection) is compared.

### D-020: How the parity suite judges a run · `accepted`

The parity suite (Phase 5, [parity.md](parity.md)) needs a few rules that the spec doesn't spell
out. They apply to both tools the same way, and none of them changes either implementation.

- **Outcome.** n8n reports an execution as succeeded or failed. Kestra's `SUCCESS` and `WARNING`,
  and Prefect's `Completed`, count as succeeded. Kestra uses `WARNING` when a task failed and the
  flow carried on, which is exactly the handled-error case (SUM-5, SND-3) that n8n reports as a
  success. `FAILED`/`KILLED` and `Failed`/`Crashed` count as failed.
- **F-4 takes app-db away before the tick starts.** "Unavailable during a tick" isn't
  deterministic at any finer grain, and a tick isn't atomic in n8n either.
- **An outage settles for 15 s before the next run.** It stands in for the 10 minutes until the
  next real tick. Kestra's JVM caches a failed DNS lookup for 10 s, so a run right after a
  restart can still fail, which no real tick would see.
- **Q-15 and the skipped branches are set up in app-db directly.** The alert needs a subject
  with `<`, and the skip needs a later duplicate and a row ready to send. No fixture has them,
  and the database is the state the two workflows share by design (Q-9).
- **Runs left over from before a session are cancelled** after two minutes, and the results
  file lists them. In practice that's a run whose worker process was replaced mid-run (Prefect
  leaves it `Running`; see its effort log).
- **Recorded, not compared:** client retries, client error texts (D-019), and run durations.

### D-021: How the stacks are measured (Phase 6) · `accepted`

The C2, C3 and C5 numbers in [comparison/results.md](comparison/results.md) come from
[`scripts/measure.py`](../scripts/measure.py) (`make measure-kestra`, `make measure-prefect`),
one stack at a time, on the same host and Docker Desktop VM (10 CPUs, 7.65 GiB), with the mocks
from `.env.example`. The script refuses to start while any other container runs. Each results
file in [comparison/data/](comparison/data/) records the date, host, versions and command.

- **Cold start (C2).** The stack's images are removed. `docker compose pull` fetches the pulled
  images, then `docker compose build --no-cache --pull` runs in a new, empty BuildKit builder
  (pinned `moby/buildkit:v0.34.1`, deleted afterwards). Removing images alone isn't enough: the
  first attempt reused base layers from BuildKit's cache and built Kestra's image in 12 s. Then
  one start to the first green run, as below. Downloads use this host's network on that day.
- **Start and first green run (C2, C3).** `make <tool>-down`, then the two commands of
  `make <tool>-up` with the images present. Each service's time to healthy comes from polling
  `docker compose ps` every 0.5 s. The first green run is the time from `up` to the end of the
  first tick that Telegram accepted a message from, through `make <tool>-seed`,
  `make <tool>-run FLOW=email-ingest` and `make <tool>-run`. Three starts; the median is reported.
- **Idle (C3).** A fresh start, deployed, schedules on, no mail: what the stack does between
  emails. After 5 minutes, `docker stats` is streamed for 60 s. The ingest schedule fires once a
  minute, so the window holds one ingest run.
- **Peak (C3).** `docker stats` streamed while the parity suite's happy path runs (9 emails
  ingested, 10 ticks). Memory is `docker stats` MEM USAGE; CPU is % of one core, summed over
  containers per refresh. "Tool" means the tool's own containers (Kestra: `kestra`, `kestra-db`
  and its script-task containers; Prefect: `prefect-server`, `prefect-worker`, `prefect-db`).
  The shared infra is reported separately; it is the same in both stacks. `docker stats`
  refreshes about twice a second, so Kestra's script-task containers, which live about a second,
  are mostly missed. Each uses about 16 MiB, so the memory figures are barely affected.
- **Developer loop (C5).** 9 emails queued (untimed), then three times: append a comment to a
  module of the ported logic, put it into the running stack the documented way
  (`make kestra-deploy`; `make prefect-up`, which rebuilds the worker image), and run one tick
  that summarizes and sends an email with that module. The unit tests are timed with the stack
  down.
- **Lines of code (C4).** [`scripts/loc.py`](../scripts/loc.py) (`make loc`) counts code lines
  (no blanks, comments or docstrings) by role. The file lists which file goes in which role.
- **Screenshots (C9).** [`scripts/screenshots.py`](../scripts/screenshots.py)
  (`make screenshots-<tool>`, after `make parity-<tool>`) captures the same kinds of pages in each
  UI, for the same parity runs, with requests outside the stack blocked.
- **Scoring.** Each criterion gets 1 to 5 per tool, with the weights in
  [criteria.md](comparison/criteria.md) unchanged and C1 as a gate. One run per stack, so small
  differences (a second here, 50 MiB there) aren't treated as meaningful.
