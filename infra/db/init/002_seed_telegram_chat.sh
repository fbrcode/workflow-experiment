#!/bin/sh
# Seeds public.telegram_chat (category -> destination chat) from environment variables.
# Runs once, on first start of an empty app-db volume (docker-entrypoint-initdb.d).
# Values are psql variables, so they are quoted by psql (:'var'), never interpolated into SQL.
#
# One row per category, with `environment` left at its default ('prod') as in production.
# SND-1 joins on email_category only, so a second environment would duplicate rows (Q-18).
# chat_name isn't read by the workflow; the names here are local placeholders.
set -eu

: "${TELEGRAM_CHAT_ID_NEWS:?must be set}"
: "${TELEGRAM_CHAT_ID_FINANCE:?must be set}"
: "${TELEGRAM_CHAT_ID_TECH:?must be set}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v news="$TELEGRAM_CHAT_ID_NEWS" \
  -v finance="$TELEGRAM_CHAT_ID_FINANCE" \
  -v tech="$TELEGRAM_CHAT_ID_TECH" <<'SQL'
insert into public.telegram_chat (email_category, chat_id, chat_name) values
  ('news',    :'news',    'News (local)'),
  ('finance', :'finance', 'Finance (local)'),
  ('tech',    :'tech',    'Tech (local)');
SQL
