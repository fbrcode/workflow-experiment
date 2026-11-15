#!/bin/sh
# Apply this repo's Prefect setup to the running server (make prefect-deploy runs this in the
# prefect-deploy container). Idempotent.
#   1. the process work pool the worker polls
#   2. configuration: blocks and variables from the environment (bootstrap.py)
#   3. the deployments in prefect.yaml, with their schedules
set -eu

prefect --no-prompt work-pool create email-summarizer --type process --overwrite > /dev/null
echo "work pool: email-summarizer (process)"
python -m email_summarizer.bootstrap
prefect --no-prompt deploy --all
