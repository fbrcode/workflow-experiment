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

- [ ] `infra/compose.yaml` with these services:
  - `app-db`: Postgres plus `infra/db/init/*.sql` (schema from data-model.md, `telegram_chat` seed from env)
  - `mail`: GreenMail (IMAP and SMTP), a stand-in for Gmail. Mailbox/label `n8n`.
  - `mock-telegram`: records `sendMessage` calls, can be told to fail (F-3), and exposes a `GET /calls` inspection endpoint
  - `mock-gemini`: implements `generateContent` for both model names, deterministic output, can be told to fail (F-1, F-2)
- [ ] Fixtures in `fixtures/emails/*.eml`, covering at least: a translated sender, a non-translated sender, an HTML-body sender (`tech-brew` or `rico-matinal`), each of the 3 categories, a duplicate, non-ASCII content, and a `From` header without a display name
- [ ] `scripts/seed-mail` delivers the fixtures to GreenMail over SMTP
- [ ] `.env.example` with every variable documented; `.env` gitignored
- [ ] Root `Makefile` with `infra-up`, `infra-down`, `seed`, `reset`
- **Verify:** `make infra-up && make seed`, then the IMAP mailbox `n8n` holds N unseen messages and `app-db` has the schema

## Phase 2: Golden vectors from the original n8n code (`tests/golden/`)

- [ ] A Node script that pulls the `jsCode` of `map-content`, `transform-content`, `prompt-builder`
  and `format-as-markdown` out of `baseline/*.json` and runs it with a small `$input`/`$()` shim
- [ ] Generate `tests/golden/<node>.json` (input to output pairs) from the fixtures plus edge cases
  (MarkdownV2 specials, nested bullets, literal `\n`, headings, `---`)
- [ ] Both implementations must reproduce these outputs **byte for byte**
- **Verify:** the generator is deterministic (two runs, no diff)

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

## Planned layout and ports

```text
infra/            shared: app-db, GreenMail, mock-telegram, mock-gemini, db init SQL
fixtures/         .eml fixture emails
tests/golden/     vectors generated from the original n8n JS
tests/parity/     tool-agnostic end-to-end scenarios
kestra/           compose + flows/*.yaml (+ scripts if needed)
prefect/          compose + Dockerfile + src/ + tests/
scripts/          helper scripts (seed, measure)
docs/             all documentation
```

| Service | Host port | Notes |
|---|---|---|
| Kestra UI/API | 8080 | |
| Prefect UI/API | 4200 | |
| app-db (Postgres) | 5433 | avoids a local 5432 |
| GreenMail SMTP / IMAP / API | 3025 / 3143 / 8083 | its API defaults to 8080, so it's remapped |
| mock-telegram | 8090 | |
| mock-gemini | 8091 | |

Ports 5000 and 7000 are taken by macOS AirPlay on this host. Don't use them.
