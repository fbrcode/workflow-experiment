# Next steps

What's needed to move forward, split by who acts. Phase details are in [plan.md](plan.md).

## Owner actions

### 1. Provide the production schema (unblocks Q-14 and replaces D-007)

Run this against the production database and save the output as
`docs/baseline/prod-schema.sql`:

```bash
pg_dump --schema-only --no-owner --no-privileges \
  -t public.news_email -t public.telegram_chat \
  "$PROD_DATABASE_URL" > docs/baseline/prod-schema.sql

# the category enum, if pg_dump didn't include it:
psql "$PROD_DATABASE_URL" -c '\dT+ public.*'
```

Most needed: the **default value of `news_email.translate_language`**. Also check the defaults
for `email_consumed_at`, `is_summarized`, `was_sent`, `summary_retry` and `sent_retry`, and the
type of `telegram_chat.chat_id`.

Don't include data. Schema only. The repo is public, so check the file for anything sensitive
(owner names, comments) before committing it.

Once it's in, Claude updates [baseline/data-model.md](baseline/data-model.md) and
`infra/db/init/`, and marks D-007 as superseded.

### 2. Provide the external reference doc

The shared Gemini conversation (`https://share.gemini.google/zIXCw75KhWSA`) renders client-side
and can't be fetched. Paste its content into `docs/reference/gemini-comparison.md`. Claude will
then reconcile [comparison/criteria.md](comparison/criteria.md) with it.

## Claude actions (in order)

1. ~~**Phase 1: shared infra.**~~ Done 2026-11-14. See [infra.md](infra.md).
2. **Phase 2: golden vectors.** Run the original n8n JS from `baseline/` under Node to produce reference outputs.
3. **Phases 3 and 4: Kestra and Prefect OSS implementations**, independent of each other.
4. **Phase 5: parity suite.** Happy path plus fault scenarios F-1 to F-7.
5. **Phase 6: measurements and the final report.**
