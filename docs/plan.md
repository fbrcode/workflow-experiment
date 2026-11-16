# Implementation plan

Claude works through these phases in order. A box gets ticked only once its verification step
has actually run and passed. If a step was skipped or is partial, it stays unticked with a note.

Legend: `[ ]` todo · `[~]` in progress / partial (see note) · `[x]` done and verified

## Phase 0: Foundations

- [x] Analyze the n8n baseline and write the [behavior spec](baseline/workflow-spec.md)
- [x] Reconstruct the [data model](baseline/data-model.md), then replace it with the production schema (2026-11-14, D-018)
- [x] Write the [comparison framework](comparison/criteria.md) and the [decision log](decisions.md)
- [x] Write `CLAUDE.md`, `README.md` and `.gitignore`
- [x] Owner answers the open questions (below). Answered 2026-11-14. Remaining owner actions are in [next-steps.md](next-steps.md).

## Phase 1: Shared local infrastructure (`infra/`)

Done 2026-11-14. Details are in [infra.md](infra.md).

- [x] `infra/compose.yaml` with these services. Every one has a healthcheck, and `up --wait` returns only when all of them are healthy.
  - `app-db`: Postgres 18.7 plus `infra/db/init/` (schema from data-model.md, `telegram_chat` seeded from env)
  - `mail`: GreenMail 2.1.15 (IMAP, IMAPS and SMTP) with real auth, a stand-in for Gmail. Mailbox/label `n8n`.
  - `mock-telegram`: validates MarkdownV2 and HTML like Telegram, returns the rendered text, records calls (`/_mock/calls`), and accepts injected faults (`/_mock/faults`)
  - `mock-gemini`: `generateContent` and `streamGenerateContent` for both models, deterministic output, API key required, fault injection (F-1, F-2)
- [x] 9 fixtures in `fixtures/emails/*.eml`, generated deterministically by `fixtures/generate.py`. They cover every required case; see the [coverage matrix](infra.md#fixtures-fixturesemails-from-fixturesgeneratepy).
- [x] `scripts/seed_mail.py` loads the fixtures with **IMAP APPEND**, not SMTP as first planned, so they land in the `n8n` folder as UNSEEN (D-013)
- [x] `.env.example` documents every variable; `.env` is gitignored, and the Makefile falls back to `.env.example`
- [x] Root `Makefile` with `infra-up`, `infra-down`, `seed`, `reset`, plus `infra-check`, `seed-check`, `fixtures`, `test-mocks`, `lint`
- [x] Mock unit tests (45 pytest cases) and the repo-wide `ruff.toml`
- **Verified:** `make reset` (down -v, up, seed) took 11 s from scratch. `make infra-check` shows tables `news_email` and `telegram_chat`, the enum `news,finance,tech`, 3 `telegram_chat` rows and 0 `news_email` rows; both mocks are healthy; mailbox `n8n` has 9 messages, all unseen. A wrong IMAP password is rejected. IMAPS works. Re-seeding is idempotent. `make test-mocks`: 45 passed. `make lint`: clean. Running `make fixtures` again gives byte-identical files.
- **Updated 2026-11-14 (D-018):** the schema now reproduces production. `make reset` took 10 s. `make schema-check` matches the production `\d+` output, and a deliberately changed default made it fail as expected. `make infra-check` shows 3 tables (`news_email`, `news_email_history`, `telegram_chat`) and 3 `telegram_chat` rows in environment `prod`. A rolled-back probe confirmed a fresh row gets `pt-BR`, `'OK'` statuses and `-1` counters; that the `< 5` guard allows 6 attempts; that the trigger logs inserts and updates; and that a second environment doubles the SND-1 rows (Q-18). `make test-mocks`: 45 passed. `make lint`: clean.

## Phase 2: Golden vectors from the original n8n code (`tests/golden/`)

Done 2026-11-14. Details are in [golden.md](golden.md).

- [x] A Node generator (`tests/golden/src/`) that pulls the code and expressions out of `baseline/*.json` and runs them unmodified behind a minimal n8n shim. It covers the 4 planned Code nodes, plus the Set node (ING-2), the insert column map (ING-4) and the alert template (SUM-7b).
- [x] ING-1 reference captured from GreenMail with **n8n's own IMAP code** (`@n8n/imap@0.26.0`, glue matching production `n8n@2.40.3`; D-017), so ports have a referee for email decoding too (D-015)
- [x] 8 vector files in `tests/golden/vectors/`: 115 cases (62 from fixtures, 53 edge cases). They cover MarkdownV2 specials, nested bullets, literal `\n`, headings, `---`, quotes, emoji, the length limit, date and `From` corner cases, and nulls.
- [x] Runs in the pinned `node:26.7.0-alpine3.24` (the Node version of n8n 2.40.3) with a locked `package-lock.json`. `make golden` and `make golden-check` added.
- [x] Both implementations reproduce these outputs **byte for byte**: Kestra (Phase 3) and Prefect (Phase 4).
- **Verified:** 3 runs, one against freshly recreated infra and DB, gave identical files. Every fixture's SND-2 message is valid MarkdownV2 per mock-telegram. The only rejections are the deliberate edge cases `too-long-after-escaping` and Q-15.
- **Regenerated 2026-11-14 for D-018.**
  - ING-1 to ING-4: unchanged.
  - SUM-3, SUM-4 and SND-2: changed only where `translate_language` (`pt-BR`) or the retry defaults (-1) appear.
  - SUM-7b: inputs changed, alert texts didn't.
  - Telegram verdicts: same 2 rejections.
  - Determinism: two runs gave identical files.

## Phase 3: Kestra (`kestra/`)

Done 2026-11-14. Details are in [kestra/README.md](kestra/README.md).

- [x] `kestra/compose.yaml`: Kestra OSS 2.0.10 (custom image from `v2.0.10-no-plugins` with 3 pinned plugins) and its own Postgres, plus the shared infra via `include`
- [x] Flow `email-ingest`: ING-1 to ING-4 (polling every minute, D-019)
- [x] Flow `email-summarize-send`: 10-min schedule; SUM, DUP and SND branches in spec order, n8n's error semantics (D-019)
- [x] Secrets and config through Kestra's OSS mechanism (`SECRET_*` env vars, `ENV_*` for `envs`); base-URL overrides for the mocks
- [x] Flows and namespace files deployed from the repo through the REST API (`make kestra-deploy`, deletes flows not in the repo)
- [x] `docs/kestra/`: [README.md](kestra/README.md), [node-mapping.md](kestra/node-mapping.md), [effort-log.md](kestra/effort-log.md)
- **Verified:** `make test-kestra`: 142 passed (all golden vectors for ING-2 to SND-2, ING-1 against GreenMail for all 9 fixtures). `make lint`: clean. Against the running stack: the 9 ingested rows equal the ING-4 vector byte for byte; a happy tick sends the SND-2 vector's message and stores the expected row; F-1, F-2 (alert equals the SUM-7b vector), F-3, an SND-3 network error, Q-15 and Q-19 behave as n8n does. See the README's verification table.
- [x] Parity scenarios as an automated suite: 13 of 13 pass (Phase 5, [parity.md](parity.md))

## Phase 4: Prefect (`prefect/`)

Done 2026-11-15. Details are in [prefect/README.md](prefect/README.md).

- [x] `prefect/compose.yaml`: Prefect OSS 3.9.3 server (official image, pinned by digest) with its own Postgres, a process worker (`wfx-prefect:local`, the package installed from `uv.lock`), plus the shared infra via `include`
- [x] Python package `prefect/src/email_summarizer/` with flows `email-ingest` and `email-summarize-send`; each n8n node is a task with the node's name; n8n's error semantics (D-019)
- [x] Deployments with schedules defined as code in `prefect/prefect.yaml` (`* * * * *` with a concurrency limit and `CANCEL_NEW`; `*/10 * * * *`), applied by `make prefect-deploy`
- [x] Secrets and config through Prefect: `Secret` blocks and a `SqlAlchemyConnector` block for credentials, variables for the rest, written from `.env` at deploy time; base-URL overrides for the mocks
- [x] Unit tests (pytest) against the golden vectors, runnable without the server (`make test-prefect`)
- [x] `docs/prefect/`: [README.md](prefect/README.md), [node-mapping.md](prefect/node-mapping.md), [effort-log.md](prefect/effort-log.md)
- **Verified:** `make test-prefect`: 128 passed (all golden vectors for ING-2 to SND-2 and SUM-7b, ING-1 on the fixture files and against GreenMail for all 9 fixtures, verbatim SQL). `make lint`: clean. `make prefect-up` from a clean state: 21 s. Against the running stack: the 9 ingested rows equal the ING-4 vector byte for byte; a happy tick passes 22 checks against the SUM-4 and SND-2 vectors; F-1, F-2 (alert equals the SUM-7b vector), F-3, an SND-3 network error, Q-15, F-5, F-6 and the ingest concurrency limit behave as n8n does; the scheduled runs work with no manual trigger. See the README's verification table.
- [x] Parity scenarios as an automated suite: 13 of 13 pass (Phase 5, [parity.md](parity.md))

> Phases 3 and 4 are independent. Neither implementation may copy logic from the other. Both port from the spec and the golden vectors.

## Phase 5: Parity suite (`tests/parity/`)

Done 2026-11-15. Details are in [parity.md](parity.md).

- [x] A tool-agnostic scenario runner: before each test it empties `news_email` and its audit log, the mailbox and both mocks; it seeds mail with IMAP APPEND, triggers ingest and ticks through each tool's REST API, and asserts on the rows, the audit log (one UPDATE per step, the baseline's columns), the Gemini and Telegram calls, the mailbox and the run's outcome. Expected values come from the golden vectors. The only tool-specific code is one HTTP driver per tool (run and wait, pause schedules, cancel leftovers).
- [x] Scenarios: ingest (2), the happy path over all 9 fixtures, F-1 to F-7, SND-5 without a response, Q-15 with the skipped branches, and the declared schedules: 13 tests
- [x] `make parity-kestra` and `make parity-prefect` (each starts its stack first; `PARITY_ARGS` goes to pytest)
- [x] Judging rules that the spec leaves open recorded as [D-020](decisions.md)
- **Verified:** from a stopped stack, `make parity-kestra` passed 13 of 13 in 3 min 42 s and `make parity-prefect` 13 of 13 in 7 min 54 s, one stack at a time, with no leftover runs to cancel (recorded runs in [comparison/data/](comparison/data/)). Each had also passed one full development run earlier that day. Two deliberate deviations built into the Prefect image (a changed system message, an HTML-escaped alert) made the happy path, F-1 and Q-15 fail; they were then reverted. `make lint`: clean. After the runs, `make kestra-down` and `make prefect-down` left no containers, volumes or networks.

## Phase 6: Measurements and report (`docs/comparison/`)

Done 2026-11-15. The result is in [comparison/results.md](comparison/results.md); the protocol is [D-021](decisions.md).

- [x] [`scripts/measure.py`](../scripts/measure.py) (`make measure-kestra` / `make measure-prefect`) captures C2, C3 and C5 into `docs/comparison/data/`, with date, host, versions and command:
  - images
  - a cold pull and build in a throwaway BuildKit builder
  - three starts with per-service time to healthy and the first green run
  - idle and peak `docker stats`
  - unit tests
  - an edit-redeploy-run loop

  [`scripts/loc.py`](../scripts/loc.py) (`make loc`) counts code lines by role for C4.
- [x] UI walkthrough screenshots for C9: [`scripts/screenshots.py`](../scripts/screenshots.py) (`make screenshots-<tool>`) runs in the pinned Playwright image and captures the same parity runs (F-1, Q-15) in each UI. It also saves a sample of each tool's metrics endpoint.
- [x] [`docs/comparison/results.md`](comparison/results.md): verdict, scorecard with evidence links, measurements, one section per dimension, weight sensitivity, limits, recommendation
- **Verified:**
  - `make measure-kestra MEASURE_ARGS="--cold"` and `make measure-prefect MEASURE_ARGS="--cold"` ran one stack at a time, with nothing else in Docker. Every start reached a green run (Telegram accepted the tick's message), and every loop cycle sent one email. Some phases were re-run with `--update` after script fixes; each results file lists those runs.
  - Both stacks passed the parity suite again (13 of 13) before their screenshots.
  - A link check of `results.md` and `decisions.md` found every relative link and anchor resolving. Every score links to evidence.
  - `make lint`: clean.
  - Teardown left no containers, volumes, networks or buildx builders.

## Retool replacement reports (D-022)

Done 2026-11-15. The owner clarified that the target is Retool Workflows.

- [x] Capability probes in both tools ([probes.md](probes.md)): a 660 s Postgres statement, and a control-table gate with a human step (form fields, a file, validation), run through the API and through each tool's UI
- [x] Vendor facts for Retool Workflows, Kestra and Prefect checked against their docs and sources (cited in the reports)
- [x] [reports/kestra.md](reports/kestra.md) and [reports/prefect.md](reports/prefect.md): running each tool, the development lifecycle (inner loop, spec-driven loop, trunk-based promotion), pros and cons per capability
- [x] [reports/retool-kestra-prefect.md](reports/retool-kestra-prefect.md): the three-way comparison, options and recommendation
- [x] Added 2026-11-16 ([D-023](decisions.md)): webhooks (a third probe, measured in both tools), AWS integration and self-hosted deployment on AWS (from the docs), and pricing against features for self-hosting, in all three reports
- **Verified:**
  - **Probes:** `make probe-kestra` and `make probe-prefect`, one stack at a time. Both long statements finished, Kestra `SUCCESS` in 660.6 s and Prefect `COMPLETED` in 660.3 s. In both tools, both gate batches ended as expected: `APPROVED` with 3 of 3 rows valid, and `INVALID` with 1 of 3.
  - **UI review:** `make screenshots-review-kestra` and `make screenshots-review-prefect` completed the human step through each UI's form. Batch 99 is `APPROVED` by "ui reviewer" in both.
  - **Checks:** `make lint` is clean. A link check of the reports found every relative link and anchor resolving. Teardown left no containers, volumes or networks.
  - **Webhook probe (2026-11-16):** `make probe-kestra PROBE_ARGS="--probes webhook"` returned 401 for unsigned and wrongly signed calls, and 200 in 0.35 s with the flow's output for a signed one. `make probe-prefect PROBE_ARGS="--probes webhook"` returned 401 without the auth string and 204 with it; the triggered run registered the batch 3 to 11 s later. Lint is clean, and the unit tests still pass: Kestra 141, Prefect 127.
  - **Not built or run:** the CI/CD pipelines and the AWS layouts in the reports are proposals. Nothing ran against AWS.

## Phase 7 (optional): Real-service smoke test

- [ ] Run against real Gmail IMAP, Gemini and Telegram, using a test bot and test chats from `.env`
- [ ] Document anything that behaves differently from the mocks

## Open questions for the owner

| # | Question | Answer (2026-11-14) |
|---|---|---|
| 1 | Is "perfect" Prefect? | Yes, Prefect OSS. See [D-001](decisions.md). |
| 2 | Can the production DDL be shared? | Provided 2026-11-14 as psql `\d+` output ([prod-schema.md](baseline/prod-schema.md)). See [D-018](decisions.md). Small follow-ups are in [next-steps.md](next-steps.md). |
| 3 | Q-3: what goes in `model_used` on the error path? | The primary model. See [D-008](decisions.md). |
| 4 | Run n8n as a third contestant? | No. See [D-009](decisions.md). |
| 5 | The external reference doc (Gemini share link) | Still open. Tracked in [next-steps.md](next-steps.md). |
| 6 | Production n8n version? | 2.40.3. See [D-017](decisions.md). |
| 7 | D-019 choices: alert time zone (America/Sao_Paulo assumed), dropping n8n's alert footer, 1-minute ingest polling | Applied in both Kestra and Prefect. The owner can still change any of them; both tools would then change the same way. |

## Planned layout and ports

```text
infra/            shared: app-db, GreenMail, mock-telegram, mock-gemini, db init SQL
fixtures/         .eml fixture emails
tests/golden/     vectors generated from the original n8n JS
tests/parity/     tool-agnostic end-to-end scenarios
kestra/           compose + flows/*.yaml (+ scripts if needed)
prefect/          compose + Dockerfile + src/ + tests/
scripts/          helper scripts (seed_mail.py; measurement scripts later)
docs/             all documentation
```

| Service | Host port | Notes |
|---|---|---|
| Kestra UI/API | 8080 | |
| Prefect UI/API | 4200 | |
| app-db (Postgres) | 5433 | avoids a local 5432 |
| GreenMail SMTP / IMAP / IMAPS / API | 3025 / 3143 / 3993 / 8083 | its API defaults to 8080, so it's remapped |
| mock-telegram | 8090 | |
| mock-gemini | 8091 | |

Ports 5000 and 7000 are taken by macOS AirPlay on this host. Don't use them.
