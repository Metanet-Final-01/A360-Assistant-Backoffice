#!/bin/bash
# 추가실험 전체 큐 - 쉬지 않고 순차 실행
# 1) tok500_ov0 vector-only  2) cs1800_ov0 vector-only
# 3) cs1200_ov240(20% overlap) hybrid+rerank(RRF, 기존 cs1200_ov0/ov120과 동일 관례)
# 4~6) 최종 확정 파이프라인(cs1200_ov120, 정규화융합 w=0.5/0.5, pool=100, rerank=50, top5)
#      3회 반복실행 - 재현성/노이즈 확인
set -e
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START tok500_ov0 vector-only ==="
$PY local_llm_experiment/run_local_model_combo.py \
  --table-name rag_documents_eval_tok500_ov0 --agent-label tok500_exaone40_full129 \
  --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
echo "=== DONE tok500_ov0 vector-only ==="

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START cs1800_ov0 vector-only ==="
$PY local_llm_experiment/run_local_model_combo.py \
  --table-name rag_documents_eval_cs1800_ov0 --agent-label cs1800_ov0_exaone40_full129 \
  --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
echo "=== DONE cs1800_ov0 vector-only ==="

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START cs1200_ov240 hybrid+rerank ==="
$PY local_llm_experiment/run_hybrid_rerank_combo.py \
  --table-name rag_documents_eval_cs1200_ov240 --index-name rag_documents_eval_cs1200_ov240 \
  --agent-label cs1200_ov240_hybrid_rerank --mode hybrid_rerank \
  --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
echo "=== DONE cs1200_ov240 hybrid+rerank ==="

for i in 1 2 3; do
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START cs1200_ov120 최종확정 반복실행 #$i ==="
  $PY local_llm_experiment/run_normalized_fusion_combo.py \
    --table-name rag_documents_eval_cs1200_ov120 --index-name rag_documents_eval_cs1200_ov120 \
    --agent-label "cs1200_ov120_final_confirmed_run${i}" \
    --fusion-pool-size 100 --bm25-weight 0.5 --vector-weight 0.5 --rerank-candidates 50 --top-k 5 \
    --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
  echo "=== DONE cs1200_ov120 최종확정 반복실행 #$i ==="
done

echo "=== ALL QUEUE DONE ==="
