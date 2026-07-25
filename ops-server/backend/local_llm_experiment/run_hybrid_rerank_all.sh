#!/bin/bash
# hybrid+rerank 전체 실험 순차 실행 - char 2개(gpt-4o-mini 우승조합 재현/확장) + token 2개(신규)
# llama-server가 단일 GPU/모델이라 동시에 두 스크립트를 못 돌린다(HANDOFF.md 기록된 실측
# 타임아웃 연쇄 문제) - 반드시 하나씩 순차 실행.
set -e
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe

run() {
  local table=$1
  local index=$2
  local label=$3
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) START $label ==="
  $PY local_llm_experiment/run_hybrid_rerank_combo.py \
    --table-name "$table" --index-name "$index" --agent-label "$label" \
    --mode hybrid_rerank --generator local --judge local-no-reasoning \
    --local-model EXAONE-4.0-32B --log-dir data
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) DONE $label ==="
}

run rag_documents_eval_cs1200_ov0   rag_documents_eval_cs1200_ov0   cs1200_ov0_hybrid_rerank
run rag_documents_eval_cs1200_ov120 rag_documents_eval_cs1200_ov120 cs1200_ov120_hybrid_rerank
run rag_documents_eval_tok900_ov0   rag_documents_eval_tok900_ov0   tok900_hybrid_rerank
run rag_documents_eval_tok1024_ov0  rag_documents_eval_tok1024_ov0  tok1024_hybrid_rerank

echo "=== ALL DONE ==="
