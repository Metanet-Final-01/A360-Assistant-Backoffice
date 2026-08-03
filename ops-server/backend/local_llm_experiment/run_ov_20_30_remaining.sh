#!/bin/bash
# run_ov_20_30_repeats.sh 재개분: run3(ov240)는 채점 전에 중단됐으므로 처음부터 재실행,
# 그 다음 ov360 run2/run3
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

run cs1200_ov240 "cs1200_ov240_final_confirmed_run3"
for i in 2 3; do
  run cs1200_ov360 "cs1200_ov360_final_confirmed_run${i}"
done

echo "=== ALL REMAINING REPEATS DONE ==="
