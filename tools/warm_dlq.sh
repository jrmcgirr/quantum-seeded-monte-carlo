#!/usr/bin/env bash
# make warm-dlq (QMC-144, R23): any SQS call on the DLQ, so its CloudWatch metrics exist before a
# drill. Run >= 15 min before `make force-failure`, and again every few hours while drilling.
set -euo pipefail
ENV="${ENV:-dev}"
DLQ_URL="$(make -s outputs ENV="$ENV" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dlq_url"]["value"])')"
aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output table
mkdir -p "$HOME/.qmc"
date -u +%s > "$HOME/.qmc/dlq-warmed-at-$ENV"
echo "DLQ warmed at $(date -u +%FT%TZ). make force-failure no earlier than $(date -u -v+15M +%TZ 2>/dev/null || date -u -d '+15 min' +%TZ)."
