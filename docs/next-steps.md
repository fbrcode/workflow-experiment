# Next steps

What's needed to move forward, split by who acts. Phase details are in [plan.md](plan.md).

## Owner actions

### 0. Before choosing the Retool replacement

From [reports/retool-replacement.md](reports/retool-replacement.md#before-deciding):

- **Decide where people do their part:** in the workflow tool (Kestra, option A) or in apps
  (Step Functions behind Retool apps, option C).
- **Find out which Retool limit you hit.** On Cloud, it's the 10-minute block maximum. On
  self-hosted, check the block's timeout, `WORKFLOW_DBCONNECTOR_QUERY_TIMEOUT_MS` (12 minutes
  by default) and the proxy timeout.
- **Check how promotion works today:** one branch per environment, or one `main` with
  multi-instance releases.
- **Check your Retool version.** Retool 4.0 supports only Kubernetes, not ECS.
- **Pilot the finalist in an AWS sandbox account.** Nothing here ran against AWS. Cover:
  - a 30-minute statement;
  - a gate with a human step and a file;
  - a webhook;
  - a 14-minute Lambda;
  - an ECS task that exits non-zero.

  For Step Functions, follow its [pilot plan](reports/step-functions.md#pilot-plan).
- **Set up the network for long statements:** direct connections, client keepalives at 240 s
  or less, and a deliberate `statement_timeout`.
- **Decide how approvals are controlled:** a shared login, a paid edition, or apps in front.
- **Ask for quotes** if governance features are needed: Kestra Enterprise, and Prefect Cloud or
  Customer-Managed Prefect.

### 1. ~~Provide the production schema~~

Provided 2026-11-14 as psql `\d+` output ([baseline/prod-schema.md](baseline/prod-schema.md)).
The local DB now reproduces it (D-018). Q-14 is resolved (`'pt-BR'`), and two new quirks came out
of it: Q-17 (retries start at -1, so there are 6 attempts) and Q-18 (SND-1 ignores
`telegram_chat.environment`).

**Optional follow-up.** None of this blocks Phases 3 and 4. These read-only queries fill the
last gaps:

```sql
\sf public.log_news_email_changes      -- the audit trigger's body (local copy is [assumed])
\dT+ public.email_category             -- enum labels (local: news, finance, tech [inferred])
show server_version;
select datcollate, datctype, datlocprovider from pg_database where datname = current_database();  -- initcap() locale, G-9
select email_category, environment, count(*) from public.telegram_chat group by 1, 2 order by 1, 2;  -- Q-18; no chat IDs
```

The repo is public. Check the function source for anything sensitive before sharing it.

### 2. ~~Confirm the n8n version that runs the baseline~~

Answered 2026-11-14: **2.40.3**. The golden vectors were regenerated against it (D-017).

### 3. Provide the external reference doc

The shared Gemini conversation (`https://share.gemini.google/zIXCw75KhWSA`) renders client-side
and can't be fetched. Paste its content into `docs/reference/gemini-comparison.md`. Claude will
then reconcile [comparison/criteria.md](comparison/criteria.md) with it.

## Claude actions (in order)

1. ~~**Phase 1: shared infra.**~~ Done 2026-11-14. See [infra.md](infra.md).
2. ~~**Phase 2: golden vectors.**~~ Done 2026-11-14. See [golden.md](golden.md).
3. ~~**Phase 3: Kestra.**~~ Done 2026-11-14. See [kestra/README.md](kestra/README.md).
4. ~~**Phase 4: Prefect.**~~ Done 2026-11-15. See [prefect/README.md](prefect/README.md).
5. ~~**Phase 5: parity suite.**~~ Done 2026-11-15. Both tools pass 13 of 13. See [parity.md](parity.md).
6. ~~**Phase 6: measurements and the final report.**~~ Done 2026-11-15. See [comparison/results.md](comparison/results.md).
7. **Phase 7 (optional): smoke test against the real Gmail, Gemini and Telegram.** Needs the owner's test credentials in `.env`.
