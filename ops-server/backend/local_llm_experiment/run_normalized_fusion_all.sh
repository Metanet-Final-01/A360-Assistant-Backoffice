#!/bin/bash
# 5단계: normalized fusion 승자 2개(ranking형/coverage형) 실제 생성+RAGAS 평가.
# fusion_pool=150(1단계 포화점), rerank_candidates=50(4단계 승자), top_k=5(anchor와 동일,
# 비교축을 하나로 고정) - anchor(RRF60/pool50/rerank20/top5)는 이미 tok1024_hybrid_rerank로
# 완료돼 있어 재실행하지 않는다.
set -e
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe

run() {
  local label=$1
  local bm25_w=$2
  local vec_w=$3
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START $label ==="
  $PY local_llm_experiment/run_normalized_fusion_combo.py \
    --table-name rag_documents_eval_tok1024_ov0 --index-name rag_documents_eval_tok1024_ov0 \
    --agent-label "$label" --fusion-pool-size 150 --bm25-weight "$bm25_w" --vector-weight "$vec_w" \
    --rerank-candidates 50 --top-k 5 \
    --generator local --judge local-no-reasoning --local-model EXAONE-4.0-32B --log-dir data
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) DONE $label ==="
}

run tok1024_norm_ranking_hr 0.7 0.3
run tok1024_norm_coverage_hr 0.5 0.5

echo "=== ALL DONE ==="
