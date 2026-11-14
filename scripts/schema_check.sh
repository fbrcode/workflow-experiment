#!/bin/sh
# Compares app-db's psql `\d+` output (stdin) with the production output kept verbatim in
# docs/baseline/prod-schema.md (D-018). Whitespace and blank lines are ignored, because psql pads
# columns to the longest value. Used by `make schema-check`.
#
# Not covered, because the production output doesn't show them: enum labels, the audit
# function's body, and table sizes/owners (the `\dt+` part).
set -eu

prod_file=$(mktemp)
app_file=$(mktemp)
trap 'rm -f "$prod_file" "$app_file"' EXIT

awk '/^```text$/ {f = 1; next} /^```$/ {f = 0} f' docs/baseline/prod-schema.md |
  sed -n '/^postgres=# \\d+ news_email$/,$p' | grep -v -e '^postgres=#' -e '^$' > "$prod_file"
grep -v '^$' > "$app_file" || true

if [ ! -s "$app_file" ]; then
  echo "schema-check: no psql output from app-db (is the infra up?)" >&2
  exit 1
fi
if diff -w "$prod_file" "$app_file"; then
  echo "schema-check: app-db matches docs/baseline/prod-schema.md (news_email, news_email_history, telegram_chat)"
else
  echo "schema-check: differs (< production, > app-db)" >&2
  exit 1
fi
