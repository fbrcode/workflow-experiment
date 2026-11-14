#!/bin/sh
# Seeds public.telegram_chat (category -> destination chat) from environment variables.
# Runs once, on first start of an empty app-db volume (docker-entrypoint-initdb.d).
# Values are psql variables, so they are quoted by psql (:'var'), never interpolated into SQL.
set -eu

: "${TELEGRAM_CHAT_ID_NEWS:?must be set}"
: "${TELEGRAM_CHAT_ID_FINANCE:?must be set}"
: "${TELEGRAM_CHAT_ID_TECH:?must be set}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v news="$TELEGRAM_CHAT_ID_NEWS" \
  -v finance="$TELEGRAM_CHAT_ID_FINANCE" \
  -v tech="$TELEGRAM_CHAT_ID_TECH" <<'SQL'
insert into public.telegram_chat (email_category, chat_id) values
  ('news',    :'news'),
  ('finance', :'finance'),
  ('tech',    :'tech');
SQL
