#!/bin/sh
# Kestra OSS reads secrets only from environment variables named SECRET_<NAME>, and their values
# must be base64-encoded (io.kestra.core.secret.SecretService). Compose can't encode values, so
# compose passes them in plain text and this shim encodes each name listed in KESTRA_SECRET_NAMES,
# removes the plain copy, and starts Kestra. Flows read them with {{ secret('<NAME>') }}.
set -eu

for name in ${KESTRA_SECRET_NAMES:-}; do
  case "$name" in
    *[!A-Z0-9_]*) echo "invalid secret name: $name" >&2; exit 1 ;;
  esac
  eval "value=\${$name?secret $name is listed in KESTRA_SECRET_NAMES but not set}"
  export "SECRET_$name=$(printf '%s' "$value" | base64 -w0)"
  unset "$name"
done
unset value KESTRA_SECRET_NAMES

exec docker-entrypoint.sh "$@"
