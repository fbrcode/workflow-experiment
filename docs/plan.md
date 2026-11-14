# Implementation plan

Claude works through these phases in order. A box gets ticked only once its verification step
has actually run and passed. If a step was skipped or is partial, it stays unticked with a note.

Legend: `[ ]` todo · `[~]` in progress / partial (see note) · `[x]` done and verified

## Phase 0: Foundations

- [x] Analyze the n8n baseline and write the [behavior spec](baseline/workflow-spec.md)
- [x] Reconstruct the [data model](baseline/data-model.md)
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

## Phase 2: Golden vectors from the original n8n code (`tests/golden/`)

Done 2026-11-14. Details are in [golden.md](golden.md).

- [x] A Node generator (`tests/golden/src/`) that pulls the code and expressions out of `baseline/*.json` and runs them unmodified behind a minimal n8n shim. It covers the 4 planned Code nodes, plus the Set node (ING-2), the insert column map (ING-4) and the alert template (SUM-7b).
- [x] ING-1 reference captured from GreenMail with **n8n's own IMAP code** (`@n8n/imap@0.26.0`, glue matching production `n8n@2.40.3`; D-017), so ports have a referee for email decoding too (D-015)
- [x] 8 vector files in `tests/golden/vectors/`: 115 cases (62 from fixtures, 53 edge cases). They cover MarkdownV2 specials, nested bullets, literal `\n`, headings, `---`, quotes, emoji, the length limit, date and `From` corner cases, and nulls.
- [x] Runs in the pinned `node:26.7.0-alpine3.24` (the Node version of n8n 2.40.3) with a locked `package-lock.json`. `make golden` and `make golden-check` added.
- [ ] Both implementations reproduce these outputs **byte for byte** (checked in Phases 3 and 4)
- **Verified:** 3 runs, one against freshly recreated infra and DB, gave identical files. Every fixture's SND-2 message is valid MarkdownV2 per mock-telegram. The only rejections are the deliberate edge cases `too-long-after-escaping` and Q-15.

## Phase 3: Kestra (`kestra/`)

- [ ] `kestra/compose.yaml`: Kestra (pinned tag) and its own Postgres, plus the shared infra via `include`
- [ ] Flow `email-ingest`: ING-1 to ING-4
- [ ] Flow `email-summarize-send`: 10-min schedule; SUM, DUP and SND branches in spec order
- [ ] Secrets and config through Kestra's OSS mechanism; base-URL overrides for the mocks
- [ ] Flows deployed from the repo (no editing in the UI as the source of truth)
- [ ] `docs/kestra/`: `README.md` (setup, architecture, versions), node mapping table, `effort-log.md`
- **Verify:** golden tests pass for the ported logic; parity scenario passes (Phase 5)

## Phase 4: Prefect (`prefect/`)

- [ ] `prefect/compose.yaml`: Prefect server (pinned) with its own Postgres, a worker, and the shared infra via `include`
- [ ] Python package `prefect/src/email_summarizer/` with flows `email_ingest` and `email_summarize_send`
- [ ] Deployments with schedules defined as code (`prefect.yaml` or `serve`/`deploy`)
- [ ] Secrets and config through Prefect blocks or env; base-URL overrides for the mocks
- [ ] Unit tests (pytest) against the golden vectors, runnable without the server
- [ ] `docs/prefect/`: `README.md`, node mapping table, `effort-log.md`
- **Verify:** pytest is green; parity scenario passes (Phase 5)

> Phases 3 and 4 are independent. Neither implementation may copy logic from the other. Both port from the spec and the golden vectors.

## Phase 5: Parity suite (`tests/parity/`)

- [ ] A tool-agnostic scenario runner: reset the infra, seed the mail, trigger ingest and N ticks
  through each tool's API, then assert the final `news_email` state and the recorded Telegram calls
- [ ] Scenarios: happy path, F-1 to F-7 from criteria.md
- [ ] `make parity-kestra` and `make parity-prefect`
- **Verify:** both are green, or the failures are documented with requirement IDs

## Phase 6: Measurements and report (`docs/comparison/`)

- [ ] Scripts that capture C2/C3 numbers into `docs/comparison/data/` (with timestamp, host and versions)
- [ ] UI walkthrough screenshots for C9
- [ ] `docs/comparison/results.md`: scorecard, evidence links, recommendation
- **Verify:** every score links to evidence

## Phase 7 (optional): Real-service smoke test

- [ ] Run against real Gmail IMAP, Gemini and Telegram, using a test bot and test chats from `.env`
- [ ] Document anything that behaves differently from the mocks

## Open questions for the owner

| # | Question | Answer (2026-11-14) |
|---|---|---|
| 1 | Is "perfect" Prefect? | Yes, Prefect OSS. See [D-001](decisions.md). |
| 2 | Can the production DDL be shared? | Yes, it will be provided. Tracked in [next-steps.md](next-steps.md). |
| 3 | Q-3: what goes in `model_used` on the error path? | The primary model. See [D-008](decisions.md). |
| 4 | Run n8n as a third contestant? | No. See [D-009](decisions.md). |
| 5 | The external reference doc (Gemini share link) | Still open. Tracked in [next-steps.md](next-steps.md). |
| 6 | Production n8n version? | 2.40.3. See [D-017](decisions.md). |

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
