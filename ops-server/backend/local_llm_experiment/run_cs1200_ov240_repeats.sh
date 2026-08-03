#!/bin/bash
# 현재 큐(run_final_queue_all.sh) 완료 대기 후 cs1200_ov240(20%) 2회 반복실행 자동 시작
set -e
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe

until grep -q "=== ALL QUEUE DONE ===" data/final_queue_all_run.log 2>/dev/null; do
  sleep 60
done

for i in 2 3; do
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START cs1200_ov240 반복실행 #$i ==="
  $PY local_llm_experiment/run_hybrid_rerank_combo.py \
    --table-name rag_documents_eval_cs1200_ov240 --index-name rag_documents_eval_cs1200_ov240 \
    --agent-label "cs1200_ov240_hybrid_rerank_run${i}" --mode hybrid_rerank \
    --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
  echo "=== DONE cs1200_ov240 반복실행 #$i ==="
done

echo "=== cs1200_ov240 반복 전체 완료 ==="
