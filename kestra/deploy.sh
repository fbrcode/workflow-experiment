#!/usr/bin/env bash
# Deploy the repo's Kestra flows and namespace files to a running Kestra (`make kestra-deploy`).
# The repo is the source of truth (C11): flows in namespace experiment.email that aren't in
# kestra/flows/ are deleted, and the namespace files are replaced as a whole.
#
# Usage: KESTRA_URL=http://localhost:8080 KESTRA_USER=... KESTRA_PASSWORD=... kestra/deploy.sh
set -euo pipefail

NAMESPACE=experiment.email
API="${KESTRA_URL:?}/api/v1/main"
HERE="$(cd "$(dirname "$0")" && pwd)"
CURL=(curl --fail-with-body --silent --show-error --user "${KESTRA_USER:?}:${KESTRA_PASSWORD:?}")

# Namespace files first, so a flow never runs against code older than itself.
"${CURL[@]}" -o /dev/null -X DELETE "$API/namespaces/$NAMESPACE/files?path=/summarizer" || true
count=0
while IFS= read -r -d '' file; do
  rel="${file#"$HERE/files/"}"
  "${CURL[@]}" -o /dev/null -X POST -F "fileContent=@$file" "$API/namespaces/$NAMESPACE/files?path=/$rel"
  count=$((count + 1))
done < <(find "$HERE/files" -type f -name '*.py' -not -path '*/__pycache__/*' -print0 | sort -z)
echo "namespace files: $count uploaded to $NAMESPACE"

# All flows in one call; delete=true removes flows that are no longer in the repo.
bundle="$(mktemp)"
trap 'rm -f "$bundle"' EXIT
first=1
for flow in "$HERE"/flows/*.yml; do
  [[ $first -eq 1 ]] || printf -- '---\n' >> "$bundle"
  cat "$flow" >> "$bundle"
  first=0
done
response="$("${CURL[@]}" -X POST -H 'Content-Type: application/x-yaml' --data-binary "@$bundle" \
  "$API/flows/$NAMESPACE?delete=true")" || { echo "$response" >&2; exit 1; }
python3 -c 'import json, sys; print("flows: " + ", ".join("%s (rev %s)" % (f["id"], f["revision"]) for f in json.loads(sys.argv[1])))' "$response"
