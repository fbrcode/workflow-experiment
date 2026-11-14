# Production schema (owner-provided)

The owner provided this psql `\dt+` / `\d+` output from the production database on 2026-11-14. It's
kept verbatim as input data. [data-model.md](data-model.md) interprets it, and
`infra/db/init/001_schema.sql` reproduces it (D-018).

It doesn't show three things: the body of `log_news_email_changes()`, the labels of the
`email_category` enum, or the database locale. See [next-steps.md](../next-steps.md).

```text
postgres=# \dt+
                                            List of tables
 Schema |        Name        | Type  |  Owner   | Persistence | Access method |  Size   | Description
--------+--------------------+-------+----------+-------------+---------------+---------+-------------
 public | news_email         | table | postgres | permanent   | heap          | 7480 kB |
 public | news_email_history | table | postgres | permanent   | heap          | 29 MB   |
 public | telegram_chat      | table | postgres | permanent   | heap          | 16 kB   |
(3 rows)

postgres=# \d+ news_email
                                                                      Table "public.news_email"
       Column       |           Type           | Collation | Nullable |                Default                 | Storage  | Compression | Stats target | Description
--------------------+--------------------------+-----------+----------+----------------------------------------+----------+-------------+--------------+-------------
 id                 | bigint                   |           | not null | nextval('news_email_id_seq'::regclass) | plain    |             |              |
 email_inbox_label  | text                     |           | not null |                                        | extended |             |              |
 email_category     | email_category           |           | not null | 'tech'::email_category                 | plain    |             |              |
 email_from         | text                     |           | not null |                                        | extended |             |              |
 email_subject      | text                     |           | not null |                                        | extended |             |              |
 email_date         | text                     |           | not null |                                        | extended |             |              |
 email_raw_content  | text                     |           | not null |                                        | extended |             |              |
 email_consumed_at  | timestamp with time zone |           | not null | CURRENT_TIMESTAMP                      | plain    |             |              |
 to_translate       | boolean                  |           | not null | false                                  | plain    |             |              |
 translate_language | text                     |           | not null | 'pt-BR'::text                          | extended |             |              |
 model_used         | text                     |           |          |                                        | extended |             |              |
 summary_status     | text                     |           |          | 'OK'::text                             | extended |             |              |
 is_summarized      | boolean                  |           | not null | false                                  | plain    |             |              |
 summary_content    | text                     |           |          |                                        | extended |             |              |
 summarized_at      | timestamp with time zone |           |          |                                        | plain    |             |              |
 summary_retry      | integer                  |           | not null | '-1'::integer                          | plain    |             |              |
 formatted_summary  | text                     |           |          |                                        | extended |             |              |
 formatted_length   | integer                  |           |          |                                        | plain    |             |              |
 sent_status        | text                     |           |          | 'OK'::text                             | extended |             |              |
 was_sent           | boolean                  |           | not null | false                                  | plain    |             |              |
 sent_at            | timestamp with time zone |           |          |                                        | plain    |             |              |
 sent_retry         | integer                  |           | not null | '-1'::integer                          | plain    |             |              |
Indexes:
    "news_email_pkey" PRIMARY KEY, btree (id)
Not-null constraints:
    "news_email_id_not_null" NOT NULL "id"
    "news_email_email_inbox_label_not_null" NOT NULL "email_inbox_label"
    "news_email_email_category_not_null" NOT NULL "email_category"
    "news_email_email_from_not_null" NOT NULL "email_from"
    "news_email_email_subject_not_null" NOT NULL "email_subject"
    "news_email_email_date_not_null" NOT NULL "email_date"
    "news_email_email_raw_content_not_null" NOT NULL "email_raw_content"
    "news_email_email_consumed_at_not_null" NOT NULL "email_consumed_at"
    "news_email_to_translate_not_null" NOT NULL "to_translate"
    "news_email_translate_language_not_null" NOT NULL "translate_language"
    "news_email_is_summarized_not_null" NOT NULL "is_summarized"
    "news_email_summary_retry_not_null" NOT NULL "summary_retry"
    "news_email_was_sent_not_null" NOT NULL "was_sent"
    "news_email_sent_retry_not_null" NOT NULL "sent_retry"
Triggers:
    tr_news_email_audit AFTER INSERT OR UPDATE ON news_email FOR EACH ROW EXECUTE FUNCTION log_news_email_changes()
Access method: heap

postgres=# \d+ news_email_history
                                                                    Table "public.news_email_history"
     Column     |           Type           | Collation | Nullable |                    Default                     | Storage  | Compression | Stats target | Description
----------------+--------------------------+-----------+----------+------------------------------------------------+----------+-------------+--------------+-------------
 id             | bigint                   |           | not null | nextval('news_email_history_id_seq'::regclass) | plain    |             |              |
 news_email_id  | integer                  |           | not null |                                                | plain    |             |              |
 operation      | text                     |           | not null |                                                | extended |             |              |
 changed_at     | timestamp with time zone |           | not null | clock_timestamp()                              | plain    |             |              |
 changed_by     | text                     |           |          | SESSION_USER                                   | extended |             |              |
 old_row        | jsonb                    |           |          |                                                | extended |             |              |
 new_row        | jsonb                    |           |          |                                                | extended |             |              |
 changed_fields | text[]                   |           |          |                                                | extended |             |              |
Indexes:
    "news_email_history_pkey" PRIMARY KEY, btree (id)
    "news_email_history_news_email_id_changed_at_idx" btree (news_email_id, changed_at)
    "news_email_history_operation_idx" btree (operation)
Check constraints:
    "news_email_history_operation_check" CHECK (operation = ANY (ARRAY['INSERT'::text, 'UPDATE'::text]))
Not-null constraints:
    "news_email_history_id_not_null" NOT NULL "id"
    "news_email_history_news_email_id_not_null" NOT NULL "news_email_id"
    "news_email_history_operation_not_null" NOT NULL "operation"
    "news_email_history_changed_at_not_null" NOT NULL "changed_at"
Access method: heap

postgres=# \d+ telegram_chat
                                                Table "public.telegram_chat"
     Column     |      Type      | Collation | Nullable |   Default    | Storage  | Compression | Stats target | Description
----------------+----------------+-----------+----------+--------------+----------+-------------+--------------+-------------
 email_category | email_category |           | not null |              | plain    |             |              |
 chat_id        | text           |           | not null |              | extended |             |              |
 chat_name      | text           |           | not null |              | extended |             |              |
 environment    | text           |           | not null | 'prod'::text | extended |             |              |
Indexes:
    "telegram_chat_pkey" PRIMARY KEY, btree (email_category, environment)
Not-null constraints:
    "telegram_chat_email_category_not_null" NOT NULL "email_category"
    "telegram_chat_chat_id_not_null" NOT NULL "chat_id"
    "telegram_chat_chat_name_not_null" NOT NULL "chat_name"
    "telegram_chat_environment_not_null" NOT NULL "environment"
Access method: heap
```
