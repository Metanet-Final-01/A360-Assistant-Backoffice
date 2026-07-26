"""4단계: reranker_pool x final_top_k 소규모 교차 (GPT 설계, 2026-07-26).

3단계에서 고른 fusion 승자 2개(둘 다 normalized min_max+arithmetic_mean, pool=150):
  - ranking형: w_bm25=0.7, w_vector=0.3
  - coverage형: w_bm25=0.5, w_vector=0.5
각각에 대해 reranker_pool in [10,20,50,100]로 Voyage rerank를 케이스당 1번만 부르고,
그 결과를 top_k=3/5/7/10으로 잘라 지표를 계산한다(rerank를 32번이 아니라 8번만 호출).

주의: 이 단계부터는 Voyage rerank API 실호출이 있어 비용이 든다(129케이스 x 2 fusion x
4 reranker_pool = 최대 1032회 호출, rerank-2.5-lite라 저렴하지만 $0은 아님).
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
from local_llm_experiment.hybrid_rerank_search import bm25_branch, get_opensearch_client, vector_branch, voyage_rerank

TABLE_NAME = "rag_documents_eval_tok1024_ov0"
INDEX_NAME = "rag_documents_eval_tok1024_ov0"
FETCH_POOL = 200
FUSION_POOL = 150

FUSION_WINNERS = [
    ("ranking_type", 0.7, 0.3),
    ("coverage_type", 0.5, 0.5),
]
RERANKER_POOLS = [10, 20, 50, 100]
TOPK_CUTS = [3, 5, 7, 10]


def _remove_all_whitespace(text: str) -> str:
    import re
    return re.sub(r"\s+", "", text)


def _min_max_normalize(scores: dict) -> dict:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _metrics_at_k(ordered_ids, relevant_ids, meta, snippets, k):
    topk = ordered_ids[:k]
    result = {}
    if relevant_ids:
        rank_of_first = None
        for pos, doc_id in enumerate(topk):
            if doc_id in relevant_ids:
                rank_of_first = pos + 1
                break
        result["hit"] = float(rank_of_first is not None)
        result["reciprocal_rank"] = 1.0 / rank_of_first if rank_of_first else 0.0
    if snippets:
        combined = _remove_all_whitespace("\n".join(meta[i]["content"] for i in topk if i in meta))
        found = sum(1 for s in snippets if _remove_all_whitespace(s) in combined)
        result["evidence_coverage"] = found / len(snippets)
    return result


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    print(f"대상 케이스 {len(all_cases)}건, Voyage rerank 실호출 발생(과금)", flush=True)
    os_client = get_opensearch_client()

    acc = defaultdict(lambda: defaultdict(list))
    latencies = defaultdict(list)
    fallback_count = 0
    started = time.time()

    with psycopg.connect(R._build_local_database_connection_string()) as conn:
        question_vectors = R._embed_all_questions_once(openai_client, all_cases, [0])
        with conn.cursor() as cursor:
            for i, case in enumerate(all_cases):
                vec = question_vectors[case.case_id]
                vector_ids, vector_meta = vector_branch(cursor, TABLE_NAME, vec, FETCH_POOL)
                bm25_ids, bm25_meta, bm25_error = bm25_branch(os_client, INDEX_NAME, case.question, FETCH_POOL)
                relevant_ids = R._get_relevant_document_ids(case)
                snippets = [rc.snippet for rc in case.reference_contexts]
                meta = {**bm25_meta, **vector_meta}

                v_ids = vector_ids[:FUSION_POOL]
                b_ids = bm25_ids[:FUSION_POOL]
                v_norm = _min_max_normalize({d: vector_meta[d]["score"] for d in v_ids})
                b_norm = _min_max_normalize({d: bm25_meta[d]["score"] for d in b_ids})
                all_docs = set(v_norm) | set(b_norm)

                for fusion_name, w_bm25, w_vec in FUSION_WINNERS:
                    combined = {d: w_bm25 * b_norm.get(d, 0.0) + w_vec * v_norm.get(d, 0.0) for d in all_docs}
                    fused_ids = sorted(combined.keys(), key=lambda d: (-combined[d], d))

                    for pool in RERANKER_POOLS:
                        candidate_ids = fused_ids[:pool]
                        rerank_inputs = [f"{meta[d]['title']}\n\n{meta[d]['content']}" for d in candidate_ids]
                        t0 = time.time()
                        try:
                            reranked = voyage_rerank(case.question, rerank_inputs, top_k=len(candidate_ids))
                            reranked_ids = [candidate_ids[item["index"]] for item in reranked]
                            fell_back = False
                        except Exception as e:
                            reranked_ids = candidate_ids  # rerank 실패 시 fusion 순서 그대로 폴백
                            fell_back = True
                            fallback_count += 1
                        latencies[(fusion_name, pool)].append(time.time() - t0)

                        for k in TOPK_CUTS:
                            config_key = (fusion_name, pool, k)
                            for name, val in _metrics_at_k(reranked_ids, relevant_ids, meta, snippets, k).items():
                                acc[config_key][name].append(val)
                            acc[config_key]["fallback"].append(1.0 if fell_back else 0.0)

                if (i + 1) % 10 == 0:
                    elapsed = time.time() - started
                    print(f"  [{i+1}/{len(all_cases)}] 진행 중... ({elapsed/60:.1f}분 경과)", flush=True)

    metric_order = ["hit", "reciprocal_rank", "evidence_coverage", "fallback"]
    rows = []
    for (fusion_name, pool, k), metrics in acc.items():
        row = {"fusion": fusion_name, "reranker_pool": pool, "top_k": k}
        for m in metric_order:
            v = metrics.get(m, [])
            row[m] = sum(v) / len(v) if v else None
        row["latency_p50_sec"] = sorted(latencies[(fusion_name, pool)])[len(latencies[(fusion_name, pool)]) // 2] if latencies[(fusion_name, pool)] else None
        rows.append(row)
    rows.sort(key=lambda r: (r["fusion"], r["reranker_pool"], r["top_k"]))

    print(f"\n{'fusion':14s}{'pool':>6s}{'top_k':>6s}{'hit':>8s}{'MRR':>8s}{'evid_cov':>10s}{'fallback':>10s}{'lat_p50':>9s}")
    for row in rows:
        def f(x, nd=4):
            return f"{x:.{nd}f}" if x is not None else "n/a"
        print(f"{row['fusion']:14s}{row['reranker_pool']:>6d}{row['top_k']:>6d}{f(row['hit']):>8s}{f(row['reciprocal_rank']):>8s}"
              f"{f(row['evidence_coverage']):>10s}{f(row['fallback']):>10s}{f(row['latency_p50_sec'], 2):>9s}")

    print(f"\n총 rerank 실호출 수: {129 * len(FUSION_WINNERS) * len(RERANKER_POOLS)}, 폴백(실패) 발생: {fallback_count}")

    import csv
    out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\reranker_pool_topk_tok1024_2026-07-26.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["fusion", "reranker_pool", "top_k"] + metric_order + ["latency_p50_sec"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"CSV 저장: {out_path}")


if __name__ == "__main__":
    main()
