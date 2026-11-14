-- Application schema for the Email Summarizer baseline, reproducing production (D-018).
-- Source: docs/baseline/prod-schema.md (owner-provided psql \d+ output), explained in
-- docs/baseline/data-model.md, which also has the command that diffs this schema against production.
--
-- Two pieces are NOT in the production output and are marked [inferred] / [assumed] below:
-- the enum labels and the body of the audit trigger function.
--
-- Runs once, on first start of an empty app-db volume (docker-entrypoint-initdb.d).

create type public.email_category as enum ('news', 'finance', 'tech');  -- labels [inferred]: n8n "options"

create table public.news_email (
    id                 bigserial                primary key,
    email_inbox_label  text                     not null,
    email_category     public.email_category    not null default 'tech',
    email_from         text                     not null,
    email_subject      text                     not null,
    email_date         text                     not null,
    email_raw_content  text                     not null,
    email_consumed_at  timestamp with time zone not null default current_timestamp,
    to_translate       boolean                  not null default false,
    translate_language text                     not null default 'pt-BR',
    model_used         text,
    summary_status     text                              default 'OK',
    is_summarized      boolean                  not null default false,
    summary_content    text,
    summarized_at      timestamp with time zone,
    summary_retry      integer                  not null default -1,
    formatted_summary  text,
    formatted_length   integer,
    sent_status        text                              default 'OK',
    was_sent           boolean                  not null default false,
    sent_at            timestamp with time zone,
    sent_retry         integer                  not null default -1
);

create table public.news_email_history (
    id             bigserial                primary key,
    news_email_id  integer                  not null,
    operation      text                     not null check (operation in ('INSERT', 'UPDATE')),
    changed_at     timestamp with time zone not null default clock_timestamp(),
    changed_by     text                              default session_user,
    old_row        jsonb,
    new_row        jsonb,
    changed_fields text[]
);
create index news_email_history_news_email_id_changed_at_idx on public.news_email_history (news_email_id, changed_at);
create index news_email_history_operation_idx on public.news_email_history (operation);

-- [assumed] body. Production shows only the trigger and the function name. This version stores
-- the whole row as jsonb and, on UPDATE, the sorted names of the columns whose values changed.
-- Replace it with the real source (`\sf public.log_news_email_changes`) when available.
create function public.log_news_email_changes() returns trigger
language plpgsql as $$
declare
    v_old jsonb := case when tg_op = 'UPDATE' then to_jsonb(old) end;
    v_new jsonb := to_jsonb(new);
begin
    insert into public.news_email_history (news_email_id, operation, old_row, new_row, changed_fields)
    values (
        new.id, tg_op, v_old, v_new,
        case when tg_op = 'UPDATE' then
            array(select n.key from jsonb_each(v_new) n where n.value is distinct from v_old -> n.key order by n.key)
        end
    );
    return null;  -- AFTER trigger: the return value is ignored
end;
$$;

create trigger tr_news_email_audit
    after insert or update on public.news_email
    for each row execute function public.log_news_email_changes();

create table public.telegram_chat (
    email_category public.email_category not null,
    chat_id        text                  not null,
    chat_name      text                  not null,
    environment    text                  not null default 'prod',
    primary key (email_category, environment)
);
