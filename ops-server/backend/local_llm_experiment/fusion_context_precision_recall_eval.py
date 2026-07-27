"""2단계 재채점: fusion 16개 설정을 실제 RAGAS Context Precision/Recall(LLMContextPrecisionWithReference
+ LLMContextRecall, 로컬 EXAONE judge)로 다시 채점한다 - GPT 지적 반영(2026-07-26 2차).

이전 fusion_comparison_eval.py는 Hit@K/MRR/자체 evidence_coverage(스니펫 substring
매칭)만 썼는데, ranking_type과 coverage_type이 evidence_coverage는 완전히 같은데
(0.9457) 실제 Context Precision(0.9554 vs 0.9457)·Recall(0.9864 vs 0.9767)은 다르다는
게 최종 3개 비교에서 드러나 - 중간 스크리닝(1~4단계)이 진짜 목적함수(Context P/R)가
아니라 대리 지표로 이뤄졌다는 지적을 받았다. 두 metric 모두 response(생성된 답변)가
필요 없어(required_columns 확인 완료: user_input/retrieved_contexts/reference만),
생성 없이 검색 결과만으로 재채점 가능 - 이게 이 스크립트의 존재 이유.

각 fusion 설정의 fused 순위 top-5(anchor/승자들과 동일한 top_k, 향후 비교축 고정)를
retrieved_contexts로 넣어 채점한다.
"""
import sys
import time
from collections import defaultdict

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")

import os
import psycopg

from app.eval.ragas_eval import chunk_experiment_runner as R
from local_llm_experiment.hybrid_rerank_search import (
    bm25_branch, get_opensearch_client, reciprocal_rank_fusion, vector_branch,
)

TABLE_NAME = "rag_documents_eval_tok1024_ov0"
INDEX_NAME = "rag_documents_eval_tok1024_ov0"
FETCH_POOL = 200
POOLS_TO_TEST = [100, 150]
RRF_CONSTANTS = [1, 5, 10, 20, 60]
NORMALIZED_WEIGHTS = [(0.3, 0.7), (0.5, 0.5), (0.7, 0.3)]
TOP_K = 5  # anchor/승자들과 동일 축 고정
LOCAL_SERVER_URL = "http://192.168.1.147:8820/v1"
LOCAL_MODEL_NAME = "EXAONE-4.0-32B"


def _min_max_normalize(scores: dict) -> dict:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cases", type=int, default=None)
    args = ap.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)

    from ragas import SingleTurnSample
    from ragas.dataset_schema import EvaluationDataset
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.evaluation import evaluate as ragas_evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall
    from ragas.run_config import RunConfig
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    judge_llm = LangchainLLMWrapper(ChatOpenAI(
        model=LOCAL_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    ))
    ragas_metrics = [LLMContextPrecisionWithReference(llm=judge_llm), LLMContextRecall(llm=judge_llm)]
    ragas_run_config = RunConfig(timeout=420, max_workers=1, max_retries=8)

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    cases = all_cases[: args.max_cases] if args.max_cases else all_cases
    print(f"대상 케이스 {len(cases)}건, top_k={TOP_K}, 생성 없이 Context Precision/Recall만 재채점", flush=True)
    os_client = get_opensearch_client()

    # config_key -> list of (case, retrieved_contexts) - 먼저 전부 검색해두고, config별로 RAGAS 배치 채점
    config_samples = defaultdict(list)

    with psycopg.connect(R._build_local_database_connection_string()) as conn:
        question_vectors = R._embed_all_questions_once(openai_client, cases, [0])
        with conn.cursor() as cursor:
            for i, case in enumerate(cases):
                vec = question_vectors[case.case_id]
                vector_ids, vector_meta = vector_branch(cursor, TABLE_NAME, vec, FETCH_POOL)
                bm25_ids, bm25_meta, bm25_error = bm25_branch(os_client, INDEX_NAME, case.question, FETCH_POOL)
                meta = {**bm25_meta, **vector_meta}

                for pool in POOLS_TO_TEST:
                    v_ids = vector_ids[:pool]
                    b_ids = bm25_ids[:pool]
                    v_scores = {d: vector_meta[d]["score"] for d in v_ids}
                    b_scores = {d: bm25_meta[d]["score"] for d in b_ids}

                    for k in RRF_CONSTANTS:
                        scores = reciprocal_rank_fusion([v_ids, b_ids], k=k, weights=[1.0, 1.0])
                        fused_ids = sorted(scores.keys(), key=lambda d: (-scores[d], d))[:TOP_K]
                        contexts = [meta[d]["content"] for d in fused_ids if d in meta]
                        config_samples[("RRF", pool, f"k={k}")].append((case, contexts))

                    v_norm = _min_max_normalize(v_scores)
                    b_norm = _min_max_normalize(b_scores)
                    all_docs = set(v_norm) | set(b_norm)
                    for w_bm25, w_vec in NORMALIZED_WEIGHTS:
                        combined = {d: w_bm25 * b_norm.get(d, 0.0) + w_vec * v_norm.get(d, 0.0) for d in all_docs}
                        fused_ids = sorted(combined.keys(), key=lambda d: (-combined[d], d))[:TOP_K]
                        contexts = [meta[d]["content"] for d in fused_ids if d in meta]
                        config_samples[("normalized", pool, f"w_bm25={w_bm25}_w_vec={w_vec}")].append((case, contexts))

                if (i + 1) % 20 == 0:
                    print(f"  검색 진행 [{i+1}/{len(cases)}]", flush=True)

    print(f"\n검색 완료, {len(config_samples)}개 설정 각각 RAGAS 채점 시작 (생성 없음, judge만)", flush=True)

    rows = []
    started = time.time()
    for cfg_index, (config_key, samples) in enumerate(config_samples.items()):
        method, pool, param = config_key
        ragas_samples = [
            SingleTurnSample(user_input=case.question, retrieved_contexts=contexts, reference=case.ground_truth)
            for case, contexts in samples
        ]
        precisions, recalls = [], []
        batch_size = 10
        for b in range(0, len(ragas_samples), batch_size):
            batch = ragas_samples[b:b + batch_size]
            result = ragas_evaluate(dataset=EvaluationDataset(samples=batch), metrics=ragas_metrics, run_config=ragas_run_config)
            df = result.to_pandas()
            for _, r in df.iterrows():
                p = r.get("llm_context_precision_with_reference")
                rc = r.get("context_recall")
                if p == p and p is not None:
                    precisions.append(float(p))
                if rc == rc and rc is not None:
                    recalls.append(float(rc))
        row = {
            "method": method, "pool": pool, "param": param,
            "context_precision": sum(precisions) / len(precisions) if precisions else None,
            "context_recall": sum(recalls) / len(recalls) if recalls else None,
            "n_precision": len(precisions), "n_recall": len(recalls),
        }
        rows.append(row)
        elapsed = (time.time() - started) / 60
        print(f"  [{cfg_index+1}/{len(config_samples)}] {method} pool={pool} {param}: "
              f"precision={row['context_precision']:.4f} recall={row['context_recall']:.4f} "
              f"({elapsed:.1f}분 경과)", flush=True)

    rows.sort(key=lambda r: (r["pool"], r["method"], r["param"]))
    print(f"\n{'method':12s}{'pool':>6s}{'param':>22s}{'ctx_precision':>15s}{'ctx_recall':>12s}")
    for row in rows:
        print(f"{row['method']:12s}{row['pool']:>6d}{row['param']:>22s}{row['context_precision']:>15.4f}{row['context_recall']:>12.4f}")

    import csv
    out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\fusion_context_precision_recall_2026-07-26.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["method", "pool", "param", "context_precision", "context_recall", "n_precision", "n_recall"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"\nCSV 저장: {out_path}")


if __name__ == "__main__":
    main()
