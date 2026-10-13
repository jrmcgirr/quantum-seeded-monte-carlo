#!/usr/bin/env bash
# make drift-check (QMC-145). Run by the INTEGRATOR from the main checkout (D33), never by WS4.
# plan = 0 → destroy → orphan scan → apply (timed) → make e2e --fresh → plan = 0.
# Asks once (the Makefile runs terraform with -input=false), then destroys and re-applies with
# -auto-approve. Afterwards, confirm the new SNS subscription (R12).
set -euo pipefail
ENV="${ENV:-dev}"
export AWS_REGION="${AWS_REGION:-us-east-2}"
step() { printf '\n=== %s  %s\n' "$(date -u +%TZ)" "$*"; }

plan_zero() {
  set +e; make plan ENV="$ENV" EXTRA_VARS="-detailed-exitcode"; local rc=$?; set -e
  [ "$rc" -eq 0 ] || { echo "FAIL: plan exit $rc (2 = drift/changes)"; exit 1; }
  echo "PASS: plan shows no changes"
}

orphans() {
  local found=0
  check() { local what="$1" out="$2"; if [ -n "$out" ] && [ "$out" != "None" ]; then
    echo "ORPHAN $what: $out"; found=1; else echo "none: $what"; fi; }
  check "tagged Project=qmc Env=$ENV" "$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=Project,Values=qmc Key=Env,Values="$ENV" --query 'ResourceTagMappingList[].ResourceARN' --output text)"
  check "log groups /aws/lambda/qmc-$ENV-*" "$(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/lambda/qmc-$ENV-" --query 'logGroups[].logGroupName' --output text)"
  check "SNS subscriptions on qmc-$ENV-alerts" "$(aws sns list-subscriptions \
    --query "Subscriptions[?contains(TopicArn, 'qmc-$ENV-alerts')].[SubscriptionArn]" --output text)"
  check "DynamoDB table qmc-$ENV" "$(aws dynamodb list-tables --query "TableNames[?@=='qmc-$ENV']" --output text)"
  check "SQS queues qmc-$ENV-*" "$(aws sqs list-queues --queue-name-prefix "qmc-$ENV-" --query 'QueueUrls' --output text)"
  check "alarms qmc-$ENV-*" "$(aws cloudwatch describe-alarms --alarm-name-prefix "qmc-$ENV-" --query 'MetricAlarms[].AlarmName' --output text)"
  check "IAM roles/users qmc-$ENV-*" "$(aws iam list-roles --query "Roles[?starts_with(RoleName, 'qmc-$ENV-')].RoleName" --output text; \
    aws iam list-users --query "Users[?starts_with(UserName, 'qmc-$ENV-')].UserName" --output text)"
  check "Lambda functions qmc-$ENV-*" "$(aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'qmc-$ENV-')].FunctionName" --output text)"
  return $found
}

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "run from the main checkout (D33)"; exit 1; }
# HANDOFF §4.1: "no changes" holds only at the sha of the last apply (QMC_BUILD_SHA).
make -s build >/dev/null
built="$(python3 -c 'import json; print(json.load(open("build/manifest.json"))["git_sha"])')"
deployed="$(uv run python -c 'from clients.qmc import request; print(request("GET", "/health")[2].get("build_sha"))')"
[ "$built" = "$deployed" ] || {
  echo "deployed build_sha $deployed != this checkout $built: make apply first, then rerun"; exit 1; }
step "1/6 plan at build_sha $built (expect no changes)"; plan_zero
read -r -p "Destroy and re-apply qmc-$ENV (region $AWS_REGION)? Type the env name to proceed: " answer
[ "$answer" = "$ENV" ] || { echo "aborted"; exit 1; }
step "2/6 destroy"; make destroy ENV="$ENV" EXTRA_VARS=-auto-approve
step "3/6 orphan scan"
if ! orphans; then
  echo "(the tagging API can lag deletes by a minute; rescanning in 90 s)"; sleep 90
  orphans || { echo "FAIL: orphans after destroy; report to WS1"; exit 1; }
fi
echo "PASS: no orphans"
step "4/6 apply from empty (timed)"; t0=$SECONDS; make apply ENV="$ENV" EXTRA_VARS=-auto-approve; apply_s=$((SECONDS - t0))
echo "apply from empty: ${apply_s} s (target < 600 s)"
step "5/6 make e2e --fresh"; make e2e ENV="$ENV" ARGS=--fresh
step "6/6 plan (expect no changes)"; plan_zero
printf '\nPASS drift-check. apply-from-empty %s s. NOW: confirm the new SNS subscription email (R12).\n' "$apply_s"
