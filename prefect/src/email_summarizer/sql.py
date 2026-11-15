"""SQL for both flows.

The three queries the spec requires verbatim (SUM-1, DUP-1, SND-1) are copied byte for byte from
`baseline/EmailSummarizerFromPgCron.json`, tabs and trailing spaces included; `tests/test_sql.py`
checks them against the JSON. The writes reproduce what the n8n Postgres nodes do (insert, or
update matched on `id`) with bound parameters (`:name`, SQLAlchemy `text()` style).
"""

from __future__ import annotations

from typing import Final

# SUM-1, node `pg-get-dedup-raw-news`
PICK_ROW_TO_SUMMARIZE: Final = (
    "with email_dups as (\n"
    "\tselect \n"
    "\t\trank() over (partition by n.email_inbox_label, n.email_subject, n.email_date order by n.email_consumed_at) as dup_rank,\n"
    "\t\tn.*\n"
    "\tfrom public.news_email n\n"
    ")\n"
    "select \n"
    "\td.id,\n"
    "\td.email_inbox_label,\n"
    "\td.email_date,\n"
    "\td.email_from,\n"
    "\td.email_subject,\n"
    "\td.to_translate,\n"
    "\td.translate_language,\n"
    "\td.email_raw_content,\n"
    "    d.summary_retry\n"
    "from email_dups d \n"
    "where dup_rank = 1\n"
    "and not d.is_summarized\n"
    "and d.summary_retry < 5\n"
    "order by id\n"
    "limit 1"
)

# DUP-1, node `pg-drop-dups`
DROP_DUPLICATES: Final = (
    "with email_dups as (\n"
    "\tselect \n"
    "\t\trank() over (\n"
    "          partition by n.email_inbox_label, n.email_subject, n.email_date \n"
    "          order by n.email_consumed_at\n"
    "        ) as dup_rank,\n"
    "\t\tn.*\n"
    "\tfrom public.news_email n\n"
    ")\n"
    "delete from news_email \n"
    "where id in (select id from email_dups where dup_rank > 1)"
)

# SND-1, node `pg-get-summary`
PICK_ROW_TO_SEND: Final = (
    "select\n"
    "\tne.id,\n"
    "\tne.email_inbox_label,\n"
    "\ttc.chat_id as telegram_chat_id,\n"
    "\tne.email_category,\n"
    "\tinitcap(ne.email_from) as email_from,\n"
    "\tinitcap(ne.email_subject) as email_subject,\n"
    "\tne.email_date,\n"
    "\tcase when ne.to_translate then 'translated to ' else 'original on ' end || ne.translate_language as translated,\n"
    "\tne.model_used,\n"
    "\tne.summary_content,\n"
    "\tne.sent_retry\n"
    "from public.news_email ne\n"
    "inner join public.telegram_chat tc on tc.email_category = ne.email_category\n"
    "where ne.is_summarized \n"
    "and not ne.was_sent\n"
    "and ne.sent_retry < 5\n"
    "order by ne.id\n"
    "limit 1;"
)

# ING-4, node `insert_raw_news`. Every other column takes its database default.
INSERT_RAW_NEWS: Final = (
    "insert into public.news_email"
    " (email_from, email_subject, email_date, email_raw_content, email_inbox_label, to_translate, email_category)"
    " values (:email_from, :email_subject, :email_date, :email_raw_content, :email_inbox_label, :to_translate,"
    " :email_category)"
)

# SUM-6, node `pg-update-ai-summary`
UPDATE_SUMMARY_OK: Final = (
    "update public.news_email set summary_status = 'OK', summary_content = :summary_content,"
    " summarized_at = :summarized_at, model_used = :model_used, is_summarized = true,"
    " summary_retry = :summary_retry where id = :news_id"
)

# SUM-7a, node `pg-update-ai-error`
UPDATE_SUMMARY_ERROR: Final = (
    "update public.news_email set summarized_at = :summarized_at, model_used = :model_used,"
    " summary_status = :error, summary_content = null, summary_retry = :summary_retry, is_summarized = false"
    " where id = :news_id"
)

# SND-4, node `pg-update-summary-sent`
UPDATE_SENT_OK: Final = (
    "update public.news_email set sent_at = :sent_at, was_sent = true, formatted_length = :formatted_length,"
    " formatted_summary = :formatted_summary, sent_status = 'OK', sent_retry = :sent_retry where id = :news_id"
)

# SND-5, node `pg-update-sent-error`
UPDATE_SENT_ERROR: Final = (
    "update public.news_email set sent_retry = :sent_retry, sent_at = :sent_at, sent_status = :error,"
    " was_sent = false, formatted_summary = :formatted_summary, formatted_length = :formatted_length,"
    " is_summarized = false where id = :news_id"
)
