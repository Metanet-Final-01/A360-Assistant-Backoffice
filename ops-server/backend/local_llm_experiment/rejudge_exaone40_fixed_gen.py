"""cs1200_judge_local_noreason(EXAONE-4.5로 생성한 답변, 검색 cs1200_ov0)을 그대로
재사용하고 judge만 EXAONE-4.0(추론끔)으로 다시 채점한다. EXAONE-4.5가 지금 서버에
안 올라와 있어서 새로 생성을 못 하니, 이미 저장된 답변 텍스트를 재사용하고
검색(vector_search)만 다시 돌려서(결정적이라 완전히 동일한 결과) retrieved_contexts를
복원한다 - 생성모델을 4.5로 완전히 고정한 채 judge만 바꾸는 공정한 비교를 위함.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
import os
import psycopg
from app.eval.ragas_eval import chunk_experiment_runner as R
from app.eval.log_store import append_run

BASE_LABEL = "cs1200_judge_local_noreason"
NEW_LABEL = "cs1200_exaone40_nr_fixedgen"
TABLE_NAME = "rag_documents_eval_cs1200_ov0"
LOCAL_SERVER_URL = "http://192.168.1.147:8820/v1"
JUDGE_MODEL_NAME = "EXAONE-4.0-32B"


def load_latest_by_case(eval_runs_path: Path, label: str) -> dict:
    by_eval_id: dict = {}
    with eval_runs_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("agent_label") == label:
                by_eval_id.setdefault(rec["evaluation_id"], []).append(rec)
    latest = max(by_eval_id, key=lambda e: int(e.rsplit("_", 1)[1]))
    return {r["case_id"]: r for r in by_eval_id[latest]}


def vector_search(cursor, table_name, vec, k):
    cursor.execute(
        f"SELECT parent_id, content FROM {table_name} ORDER BY embedding <=> %s::vector LIMIT %s",
        (vec, k),
    )
    rows = cursor.fetchall()
    ids, content = [], {}
    for parent_id, text in rows:
        if parent_id not in content:
            ids.append(parent_id)
            content[parent_id] = text
    return ids, content


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)

    base_records = load_latest_by_case(Path("data/eval_runs.jsonl"), BASE_LABEL)
    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    cases = [c for c in all_cases if c.case_id in base_records]
    print(f"기준 답변 재사용 대상: {len(cases)}건 (label={BASE_LABEL})", flush=True)

    from ragas import SingleTurnSample
    from ragas.dataset_schema import EvaluationDataset
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.evaluation import evaluate as ragas_evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        AnswerCorrectness, AnswerRelevancy, Faithfulness,
        LLMContextPrecisionWithReference, LLMContextRecall,
    )
    from ragas.run_config import RunConfig
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    judge_llm = LangchainLLMWrapper(ChatOpenAI(
        model=JUDGE_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
        max_tokens=1024, extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    ))
    judge_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(api_key=api_key, model=R.EMBEDDING_MODEL))
    ragas_metrics = [
        Faithfulness(llm=judge_llm),
        AnswerRelevancy(llm=judge_llm, embeddings=judge_embeddings),
        LLMContextPrecisionWithReference(llm=judge_llm),
        LLMContextRecall(llm=judge_llm),
        AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings),
    ]
    ragas_run_config = RunConfig(timeout=300, max_workers=2, max_retries=5)

    with psycopg.connect(R._build_local_database_connection_string()) as database_connection:
        case_id_to_source_type = R._tag_case_source_types(database_connection, cases)
        question_vectors_by_case_id = R._embed_all_questions_once(openai_client, cases, [0])

        ragas_samples = []
        extra_info_by_case_id = {}
        with database_connection.cursor() as cursor:
            for case in cases:
                base = base_records[case.case_id]
                vec = question_vectors_by_case_id[case.case_id]
                retrieved_ids, content_by_id = vector_search(cursor, TABLE_NAME, vec, 5)
                retrieved_chunk_texts = [content_by_id[d] for d in retrieved_ids]
                if retrieved_ids != base["raw"]["retrieved_parent_ids"]:
                    print(f"  경고: {case.case_id} 검색 결과가 기존 실행과 다름! {retrieved_ids} vs {base['raw']['retrieved_parent_ids']}", flush=True)
                reused_answer = base["raw"]["answer"]
                ragas_samples.append(SingleTurnSample(
                    user_input=case.question, retrieved_contexts=retrieved_chunk_texts,
                    response=reused_answer, reference=case.ground_truth,
                ))
                relevant_document_ids = R._get_relevant_document_ids(case)
                hit_and_rank_metrics = R._compute_hit_rate_and_reciprocal_rank(retrieved_ids, relevant_document_ids)
                extra_info_by_case_id[case.case_id] = {
                    "answer": reused_answer,
                    "retrieved_parent_ids": retrieved_ids,
                    "retrieved_total_chars": sum(len(t) for t in retrieved_chunk_texts),
                    "evidence_coverage": R._compute_evidence_coverage(case, retrieved_chunk_texts),
                    **hit_and_rank_metrics,
                }

        print(f"검증 완료, {JUDGE_MODEL_NAME}(추론끔)으로 채점 시작...", flush=True)
        started = time.time()
        evaluation_id = f"{NEW_LABEL}_{int(started)}"[:40]
        saved = 0
        for batch_start in range(0, len(cases), R.RAGAS_JUDGE_BATCH_SIZE):
            batch_end = batch_start + R.RAGAS_JUDGE_BATCH_SIZE
            batch_cases = cases[batch_start:batch_end]
            batch_samples = ragas_samples[batch_start:batch_end]
            batch_result = ragas_evaluate(
                dataset=EvaluationDataset(samples=batch_samples),
                metrics=ragas_metrics,
                run_config=ragas_run_config,
            )
            batch_df = batch_result.to_pandas()
            for row_index, case in enumerate(batch_cases):
                result_row = batch_df.iloc[row_index]
                extra_info = extra_info_by_case_id[case.case_id]
                record = R._build_result_record(
                    result_row=result_row, case=case, extra_info=extra_info,
                    source_type=case_id_to_source_type.get(case.case_id, "unknown"),
                    evaluation_id=evaluation_id, agent_label=NEW_LABEL,
                    chunk_size=1200, overlap=0, top_k=5,
                )
                append_run(record)
                saved += 1
            print(f"채점 진행 {min(batch_end, len(cases))}/{len(cases)}건", flush=True)

        elapsed_min = (time.time() - started) / 60
        result = {"ok": True, "agent_label": NEW_LABEL, "cases": len(cases), "saved": saved,
                  "elapsed_min": round(elapsed_min, 1)}
        print("RESULT_JSON: " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
