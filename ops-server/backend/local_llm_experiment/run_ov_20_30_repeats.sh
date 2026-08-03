#!/bin/bash
# cs1200_ov240(20%), cs1200_ov360(30%)을 신 방식(정규화 융합)으로 2회씩 더 돌려서
# 10%(이미 3회)와 공정하게 3vs3vs3 비교가 되게 함
set -e
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe

run() {
  local table=$1
  local label=$2
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START $label ==="
  $PY local_llm_experiment/run_normalized_fusion_combo.py \
    --table-name "rag_documents_eval_${table}" --index-name "rag_documents_eval_${table}" \
    --agent-label "$label" \
    --fusion-pool-size 100 --bm25-weight 0.5 --vector-weight 0.5 --rerank-candidates 50 --top-k 5 \
    --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
  echo "=== DONE $label ==="
}

for i in 2 3; do
  run cs1200_ov240 "cs1200_ov240_final_confirmed_run${i}"
done
for i in 2 3; do
  run cs1200_ov360 "cs1200_ov360_final_confirmed_run${i}"
done

echo "=== ALL 20%/30% REPEATS DONE ==="
