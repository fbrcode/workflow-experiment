# Baseline data model (reconstructed)

The n8n exports contain **no DDL**. This schema was rebuilt from what the workflows show:

- the column schema cached in the n8n Postgres node (`insert_raw_news`), which records type, `required`, and enum options
- the columns that the queries and updates read and write

Each column is tagged with how sure the reconstruction is:

- **[export]**: stated directly by the n8n column schema
- **[inferred]**: implied by how the column is used
- **[assumed]**: a guess made so the experiment can run. Confirm it against production.

> **Pending owner action:** the production DDL will be provided as `docs/baseline/prod-schema.sql`
> (see [next-steps.md](../next-steps.md)). It will then replace this reconstruction.

## `public.email_category` (enum) [inferred]

n8n shows `email_category` as type `options` with the values `news`, `finance` and `tech`. n8n
does this for Postgres **enum** columns.

```sql
create type public.email_category as enum ('news', 'finance', 'tech');
```

## `public.news_email`

| column | type | null / default | basis | written by |
|---|---|---|---|---|
| `id` | `bigint` identity / serial | PK | [export] number, not required | DB |
| `email_inbox_label` | `text` | not null | [export] required | ING-4 (sender slug) |
| `email_category` | `email_category` | nullable | [export] options, not required | ING-4 |
| `email_from` | `text` | not null | [export] required | ING-4 |
| `email_subject` | `text` | not null | [export] required | ING-4 |
| `email_date` | `text` | not null | [export] string, required | ING-4 (GMT-3 display string) |
| `email_raw_content` | `text` | not null | [export] required | ING-4 |
| `email_consumed_at` | `timestamptz` | `default now()` | [export] dateTime; **default [assumed]**. Dedup orders by it, but nothing writes it. | DB |
| `to_translate` | `boolean` | `default false` [assumed] | [export] boolean | ING-4 |
| `translate_language` | `text` | **default [assumed]** | [export] string. Never written but read by SUM-1 and SND-1, so it **must** have a default. | DB |
| `model_used` | `text` | nullable | [export] | SUM-6/7a |
| `summary_status` | `text` | nullable | [export] | SUM-6/7a (`'OK'` or error text) |
| `is_summarized` | `boolean` | `not null default false` [assumed] | [export]; `not d.is_summarized` needs a non-null value | SUM-6/7a, SND-5 |
| `summary_content` | `text` | nullable | [export] | SUM-6/7a |
| `summarized_at` | `timestamptz` | nullable | [export] dateTime | SUM-6/7a |
| `summary_retry` | `integer` | `not null default 0` [assumed] | [export] number; `< 5` and `+ 1` need a non-null value | SUM-6/7a |
| `formatted_summary` | `text` | nullable | [export] | SND-4/5 |
| `formatted_length` | `integer` | nullable | [export] number | SND-4/5 |
| `sent_status` | `text` | nullable | [export] | SND-4/5 |
| `was_sent` | `boolean` | `not null default false` [assumed] | [export]; `not ne.was_sent` | SND-4/5 |
| `sent_at` | `timestamptz` | nullable | [export] dateTime | SND-4/5 |
| `sent_retry` | `integer` | `not null default 0` [assumed] | [export] number | SND-4/5 |

**Open question (Q-14): the default for `translate_language`.** The sender header renders
`"translated to " || translate_language` or `"original on " || translate_language`. The prompt says
`"... translated to <x> language"`. The newsletters are Brazilian and the dates are GMT-3, so
until confirmed the experiment assumes **`'Brazilian Portuguese'`**.

## `public.telegram_chat` [inferred]

This table is only used in the SND-1 join. It maps each category to a destination chat.

| column | type | basis |
|---|---|---|
| `email_category` | `email_category` (probably the PK or unique) | join key |
| `chat_id` | `bigint` or `text` | sent to Telegram as `chat_id`. **[assumed]** `text`, because negative group IDs and `@channel` names are both valid. |

Seed data comes from env or fixtures. Real chat IDs must not be committed.

## Indexes

Unknown. The experiment adds none beyond the PK. At this volume (a few dozen emails a day) none are needed.
