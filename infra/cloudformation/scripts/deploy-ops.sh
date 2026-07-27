#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${STACK_NAME:-a360-assistant-dev-ops}"
mapfile -t PARAMS < <(jq -r '.[] | "\(.ParameterKey)=\(.ParameterValue)"' "${ROOT_DIR}/parameters/ops-dev.json")
aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${ROOT_DIR}/ops-stack.yml" \
  --parameter-overrides "${PARAMS[@]}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
