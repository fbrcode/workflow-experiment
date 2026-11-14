-- Sanity check for `make infra-check` (piped into psql; not an init script).
\pset footer off
\echo '[tables]'
select table_name from information_schema.tables where table_schema = 'public' order by 1;
\echo '[email_category enum]'
select string_agg(enumlabel, ',' order by enumsortorder) from pg_enum
 where enumtypid = 'public.email_category'::regtype;
\echo '[telegram_chat]'
select email_category || ' / ' || environment || ' -> ' || chat_id || ' (' || chat_name || ')'
  from public.telegram_chat order by email_category, environment;
\echo '[news_email rows]'
select count(*) from public.news_email;
\echo '[news_email_history rows (audit trigger)]'
select count(*) from public.news_email_history;
