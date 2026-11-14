# Next steps

What's needed to move forward, split by who acts. Phase details are in [plan.md](plan.md).

## Owner actions

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
3. **Phases 3 and 4: Kestra and Prefect OSS implementations**, independent of each other.
4. **Phase 5: parity suite.** Happy path plus fault scenarios F-1 to F-7.
5. **Phase 6: measurements and the final report.**
