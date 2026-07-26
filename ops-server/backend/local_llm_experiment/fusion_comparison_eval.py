"""2단계: fusion 방식 비교 (GPT 설계, 2026-07-26) - RRF 5종 vs normalized score fusion
(min_max + arithmetic_mean) 3종, 1단계에서 남긴 pool 2개(100/150)에 대해서만.

검색(top-200/branch)은 케이스당 한 번만 하고, 그 위에서 16개 fusion 설정을 전부
계산한다(재검색 없음) - final_top_k별 cutoff(Hit@1/3/5/10/20, MRR, EvidenceCoverage@3/5/10/20)도
같은 fused 순위 리스트에서 한 번에 뽑는다.
"""
import sys
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
NORMALIZED_WEIGHTS = [(0.3, 0.7), (0.5, 0.5), (0.7, 0.3)]  # (bm25_weight, vector_weight)
HIT_CUTOFFS = [1, 3, 5, 10, 20]
EVIDENCE_CUTOFFS = [3, 5, 10, 20]


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


def _rank_metrics(fused_ids, relevant_ids, meta, snippets):
    result = {}
    if relevant_ids:
        rank_of_first = None
        for pos, doc_id in enumerate(fused_ids):
            if doc_id in relevant_ids:
                rank_of_first = pos + 1
                break
        result["mrr"] = 1.0 / rank_of_first if rank_of_first else 0.0
        for k in HIT_CUTOFFS:
            result[f"hit_at_{k}"] = float(rank_of_first is not None and rank_of_first <= k)
    if snippets:
        for k in EVIDENCE_CUTOFFS:
            topk_ids = fused_ids[:k]
            combined = _remove_all_whitespace("\n".join(meta[i]["content"] for i in topk_ids if i in meta))
            found = sum(1 for s in snippets if _remove_all_whitespace(s) in combined)
            result[f"evidence_coverage_at_{k}"] = found / len(snippets)
    return result


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    print(f"대상 케이스 {len(all_cases)}건", flush=True)
    os_client = get_opensearch_client()

    # config_key -> metric_name -> list of per-case values
    acc = defaultdict(lambda: defaultdict(list))

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

                for pool in POOLS_TO_TEST:
                    v_ids = vector_ids[:pool]
                    b_ids = bm25_ids[:pool]
                    v_scores = {d: vector_meta[d]["score"] for d in v_ids}
                    b_scores = {d: bm25_meta[d]["score"] for d in b_ids}

                    for k in RRF_CONSTANTS:
                        scores = reciprocal_rank_fusion([v_ids, b_ids], k=k, weights=[1.0, 1.0])
                        fused_ids = sorted(scores.keys(), key=lambda d: (-scores[d], d))
                        config_key = ("RRF", pool, f"k={k}")
                        for name, val in _rank_metrics(fused_ids, relevant_ids, meta, snippets).items():
                            acc[config_key][name].append(val)

                    v_norm = _min_max_normalize(v_scores)
                    b_norm = _min_max_normalize(b_scores)
                    all_docs = set(v_norm) | set(b_norm)
                    for w_bm25, w_vec in NORMALIZED_WEIGHTS:
                        combined = {d: w_bm25 * b_norm.get(d, 0.0) + w_vec * v_norm.get(d, 0.0) for d in all_docs}
                        fused_ids = sorted(combined.keys(), key=lambda d: (-combined[d], d))
                        config_key = ("normalized", pool, f"w_bm25={w_bm25}_w_vec={w_vec}")
                        for name, val in _rank_metrics(fused_ids, relevant_ids, meta, snippets).items():
                            acc[config_key][name].append(val)

                if (i + 1) % 20 == 0:
                    print(f"  [{i+1}/{len(all_cases)}] 진행 중...", flush=True)

    metric_order = ["mrr"] + [f"hit_at_{k}" for k in HIT_CUTOFFS] + [f"evidence_coverage_at_{k}" for k in EVIDENCE_CUTOFFS]
    rows = []
    for config_key, metrics in acc.items():
        method, pool, param = config_key
        row = {"method": method, "pool": pool, "param": param}
        for m in metric_order:
            v = metrics.get(m, [])
            row[m] = sum(v) / len(v) if v else None
        rows.append(row)
    rows.sort(key=lambda r: (r["pool"], r["method"], r["param"]))

    print(f"\n{'method':12s}{'pool':>6s}{'param':>22s}" + "".join(f"{m[:14]:>15s}" for m in metric_order))
    for row in rows:
        vals = "".join(f"{row[m]:.4f}".rjust(15) if row[m] is not None else "n/a".rjust(15) for m in metric_order)
        print(f"{row['method']:12s}{row['pool']:>6d}{row['param']:>22s}{vals}")

    import csv
    out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\fusion_comparison_tok1024_2026-07-26.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["method", "pool", "param"] + metric_order)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"\nCSV 저장: {out_path}")


if __name__ == "__main__":
    main()
