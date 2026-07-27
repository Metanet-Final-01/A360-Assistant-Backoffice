#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

template="ops-stack.yml"
echo "==> aws cloudformation validate-template: ${template}"
aws cloudformation validate-template \
  --template-body "file://${ROOT_DIR}/${template}" \
  >/dev/null

if command -v cfn-lint >/dev/null 2>&1; then
  cfn-lint "${ROOT_DIR}"/*.yml
else
  echo "cfn-lint not found; skipped"
fi
