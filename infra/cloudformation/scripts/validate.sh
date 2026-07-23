#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for template in network-stack.yml data-stack.yml application-stack.yml access-stack.yml; do
  echo "==> aws cloudformation validate-template: ${template}"
  aws cloudformation validate-template \
    --template-body "file://${ROOT_DIR}/${template}" \
    >/dev/null
done

if command -v cfn-lint >/dev/null 2>&1; then
  cfn-lint "${ROOT_DIR}"/*.yml
else
  echo "cfn-lint not found; skipped"
fi
