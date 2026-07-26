"""1단계: branch candidate pool 포화점 측정 (GPT 설계 반영, 2026-07-26).

fusion/reranker를 아직 고르지 않고, BM25/Vector 각 branch가 top-200까지 낼 때
N=10/20/50/100/150/200에서 잘라 Gold Document Recall / Evidence Coverage / Union
Recall / branch간 중복률을 측정한다 - "fusion이 쓸 수 있는 정답 후보의 상한선"을
찾는 것이 목적이라 검색을 여러 번 반복하지 않고 top-200을 한 번만 뽑아서 자른다.

대상은 tok1024 하나로 고정(튜닝은 tok1024, 최종 검증만 cs1200_ov120에서 재현하는
GPT 설계를 따름). LLM 생성 없이 검색만 하므로 빠르고 $0(질문 임베딩은 이미 캐시됨).
"""
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")

import os
import psycopg

from app.eval.ragas_eval import chunk_experiment_runner as R
from local_llm_experiment.hybrid_rerank_search import bm25_branch, get_opensearch_client, vector_branch

TABLE_NAME = "rag_documents_eval_tok1024_ov0"
INDEX_NAME = "rag_documents_eval_tok1024_ov0"
POOL_MAX = 200
CUTOFFS = [10, 20, 50, 100, 150, 200]


def _remove_all_whitespace(text: str) -> str:
    import re
    return re.sub(r"\s+", "", text)


def _evidence_coverage_at(ordered_ids, meta, snippets):
    if not snippets:
        return None
    combined = _remove_all_whitespace("\n".join(meta[i]["content"] for i in ordered_ids))
    found = sum(1 for s in snippets if _remove_all_whitespace(s) in combined)
    return found / len(snippets)


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    print(f"대상 케이스 {len(all_cases)}건, table={TABLE_NAME}, index={INDEX_NAME}", flush=True)

    os_client = get_opensearch_client()

    # 지표 누적: per N -> list of per-case 값
    acc = {N: defaultdict(list) for N in CUTOFFS}

    with psycopg.connect(R._build_local_database_connection_string()) as conn:
        question_vectors = R._embed_all_questions_once(openai_client, all_cases, [0])

        with conn.cursor() as cursor:
            for i, case in enumerate(all_cases):
                vec = question_vectors[case.case_id]
                vector_ids, vector_meta = vector_branch(cursor, TABLE_NAME, vec, POOL_MAX)
                bm25_ids, bm25_meta, bm25_error = bm25_branch(os_client, INDEX_NAME, case.question, POOL_MAX)
                if bm25_error:
                    print(f"  [{i+1}/{len(all_cases)}] BM25 에러: {bm25_error}", flush=True)

                relevant_ids = R._get_relevant_document_ids(case)
                snippets = [rc.snippet for rc in case.reference_contexts]
                meta = {**bm25_meta, **vector_meta}

                for N in CUTOFFS:
                    bm25_topN = bm25_ids[:N]
                    vector_topN = vector_ids[:N]
                    union_ordered = list(dict.fromkeys(vector_topN + bm25_topN))  # 순서보존 dedup

                    if relevant_ids:
                        acc[N]["bm25_recall"].append(len(relevant_ids & set(bm25_topN)) / len(relevant_ids))
                        acc[N]["vector_recall"].append(len(relevant_ids & set(vector_topN)) / len(relevant_ids))
                        acc[N]["union_recall"].append(len(relevant_ids & set(union_ordered)) / len(relevant_ids))

                    ec_bm25 = _evidence_coverage_at(bm25_topN, meta, snippets)
                    ec_vector = _evidence_coverage_at(vector_topN, meta, snippets)
                    ec_union = _evidence_coverage_at(union_ordered, meta, snippets)
                    if ec_bm25 is not None:
                        acc[N]["bm25_evidence_coverage"].append(ec_bm25)
                        acc[N]["vector_evidence_coverage"].append(ec_vector)
                        acc[N]["union_evidence_coverage"].append(ec_union)

                    if N <= len(bm25_topN) and N <= len(vector_topN):
                        overlap = len(set(bm25_topN) & set(vector_topN))
                        acc[N]["overlap_rate"].append(overlap / N)

                if (i + 1) % 20 == 0:
                    print(f"  [{i+1}/{len(all_cases)}] 진행 중...", flush=True)

    print("\n=== Pool 포화점 측정 결과 (tok1024) ===")
    header = f"{'N':>5s}{'bm25_recall':>13s}{'vec_recall':>12s}{'union_recall':>14s}{'bm25_evid':>11s}{'vec_evid':>10s}{'union_evid':>12s}{'overlap':>9s}{'n':>6s}"
    print(header)
    rows = []
    for N in CUTOFFS:
        d = acc[N]
        def m(key):
            v = d.get(key, [])
            return sum(v) / len(v) if v else None
        row = {
            "N": N, "bm25_recall": m("bm25_recall"), "vector_recall": m("vector_recall"),
            "union_recall": m("union_recall"), "bm25_evidence_coverage": m("bm25_evidence_coverage"),
            "vector_evidence_coverage": m("vector_evidence_coverage"), "union_evidence_coverage": m("union_evidence_coverage"),
            "overlap_rate": m("overlap_rate"), "n": len(d.get("union_recall", [])),
        }
        rows.append(row)
        def f(x):
            return f"{x:.4f}" if x is not None else "n/a"
        print(f"{N:>5d}{f(row['bm25_recall']):>13s}{f(row['vector_recall']):>12s}{f(row['union_recall']):>14s}"
              f"{f(row['bm25_evidence_coverage']):>11s}{f(row['vector_evidence_coverage']):>10s}{f(row['union_evidence_coverage']):>12s}"
              f"{f(row['overlap_rate']):>9s}{row['n']:>6d}")

    print("\n=== 증분 분석 (한 단계 키울 때 union 지표가 몇 문항 분 증가했나, 129건 기준 1건=0.78%p) ===")
    prev = None
    for row in rows:
        if prev is not None:
            n = row["n"]
            d_recall = (row["union_recall"] - prev["union_recall"]) * n if row["union_recall"] is not None and prev["union_recall"] is not None else None
            d_evid = (row["union_evidence_coverage"] - prev["union_evidence_coverage"]) * n if row["union_evidence_coverage"] is not None and prev["union_evidence_coverage"] is not None else None
            print(f"  {prev['N']:>3d} -> {row['N']:>3d}: union_recall 증가 {d_recall:.2f}건, union_evidence_coverage 증가 {d_evid:.2f}건"
                  if d_recall is not None and d_evid is not None else f"  {prev['N']} -> {row['N']}: n/a")
        prev = row

    import csv
    out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\pool_saturation_tok1024_2026-07-26.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"\nCSV 저장: {out_path}")


if __name__ == "__main__":
    main()
