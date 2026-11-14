-- Sanity check for `make infra-check` (piped into psql; not an init script).
\pset footer off
\echo '[tables]'
select table_name from information_schema.tables where table_schema = 'public' order by 1;
\echo '[email_category enum]'
select string_agg(enumlabel, ',' order by enumsortorder) from pg_enum
 where enumtypid = 'public.email_category'::regtype;
\echo '[telegram_chat]'
select email_category || ' -> ' || chat_id from public.telegram_chat order by 1;
\echo '[news_email rows]'
select count(*) from public.news_email;
