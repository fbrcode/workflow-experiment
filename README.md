# Workflow orchestrator experiment: Kestra vs Prefect

A local, reproducible comparison of **[Kestra](https://kestra.io)** and **[Prefect](https://www.prefect.io)**
as replacements for **n8n**. A real production n8n workflow is re-implemented in both tools, run
with docker compose, and judged against one behavior spec and one scorecard.

> **Status:** Phases 0 (foundations), 1 (shared local infrastructure) and 2 (golden vectors from the original n8n code) are done. Neither orchestrator is implemented yet. See [docs/plan.md](docs/plan.md).

## The workflow under test

The baseline is an **email newsletter summarizer**, exported from n8n into [`baseline/`](baseline/):

| n8n workflow | What it does |
|---|---|
| `EmailSummarizerMainUnique` | IMAP (Gmail label `n8n`), then parse the sender, category and translate flag, then insert into Postgres |
| `EmailSummarizerFromPgCron` | Every 10 min: summarize one email with Gemini (with a fallback model; alerts on failure), delete duplicates, send one summary to Telegram (MarkdownV2) |

```mermaid
flowchart LR
  Mail[(IMAP inbox)] --> Ingest[Ingest flow] --> DB[(Postgres<br/>news_email)]
  DB --> Tick((every 10 min))
  Tick --> Sum[Summarize<br/>Gemini primary/fallback] --> DB
  Tick --> Dedup[Drop duplicates] --> DB
  Tick --> Send[Format + send] --> TG[Telegram chat<br/>per category]
  Send --> DB
  Sum -. on error .-> Alert[Telegram admin alert]
```

The full contract, with requirement IDs and known quirks, is in [docs/baseline/workflow-spec.md](docs/baseline/workflow-spec.md).

## Approach

- **Spec first.** The n8n exports are turned into a written behavior spec. Both tools are built from it, independently.
- **Parity-gated.** A tool-agnostic parity suite and golden vectors generated from the original n8n JavaScript decide whether an implementation is "the same workflow". Ergonomics only count after parity.
- **Fully local.** GreenMail stands in for Gmail, and HTTP mocks stand in for Gemini and Telegram, so runs are deterministic and free. A real-service smoke test is optional.
- **Evidence-based scoring.** 13 dimensions (setup, footprint, authoring effort, dev loop, error handling, observability, GitOps, ...). Every score links to a measurement or artifact. See [docs/comparison/criteria.md](docs/comparison/criteria.md).
- **OSS, self-hosted only.** No Kestra Enterprise, no Prefect Cloud.

## Repository layout

```text
baseline/     n8n exports (read-only source of truth)
docs/         all documentation: spec, plan, decisions, per-tool docs, comparison
infra/        shared local services: app Postgres, GreenMail, mocks          (done)
fixtures/     synthetic newsletter emails + generator                        (done)
scripts/      helper scripts (IMAP seeding)                                  (done)
tests/        golden vectors from the original n8n code (done) + parity suite (Phase 5)
kestra/       Kestra compose + YAML flows                                    (Phase 3)
prefect/      Prefect compose + Python flows + unit tests                    (Phase 4)
```

## Prerequisites

- Docker Desktop with Compose v2.20 or later (for `include:`). Give Docker **at least 8 GB** of
  RAM, since Kestra runs on the JVM.
- GNU Make, [uv](https://docs.astral.sh/uv/), and Node.js 20 or later (Node is only for generating golden vectors)
- Optional, for the real-service smoke test: a Gmail app password, a Gemini API key, and a Telegram test bot

## Getting started

The shared infrastructure runs today. The orchestrator stacks (`make kestra-up`, `make prefect-up`)
and the parity suite come in later phases.

```bash
make infra-up      # Postgres, GreenMail (IMAP), mock Telegram, mock Gemini, all healthy
make seed          # load 9 synthetic newsletters into the IMAP folder 'n8n'
make infra-check   # confirm schema, chat seed, mock health, mailbox counts
make infra-down    # stop and remove everything
```

No `.env` is needed: the Makefile falls back to `.env.example` (local-only placeholders).
Details: [docs/infra.md](docs/infra.md).

## Documentation

Start at [docs/README.md](docs/README.md). The key files:

- [docs/plan.md](docs/plan.md): phases, checklist, **open questions for the owner**
- [docs/decisions.md](docs/decisions.md): assumptions and deviations
- [docs/baseline/](docs/baseline/): spec and data model
- [docs/comparison/](docs/comparison/): criteria now, results at the end

## Working with Claude

Claude Code implements and documents this experiment. Its working rules (parity, pinning, secrets,
definition of done, documentation duties) are in [CLAUDE.md](CLAUDE.md).
