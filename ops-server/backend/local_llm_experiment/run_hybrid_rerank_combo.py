"""run_local_model_combo.py의 hybrid(+rerank) 버전. vector-only 대신 pgvector+BM25
RRF 융합(+Voyage rerank)로 검색한다 - 생성(EXAONE-4.0 로컬)/채점 파이프라인, resume,
로그 저장 방식은 동일 관례를 그대로 따른다. char/token 후보 모두 --table-name +
--index-name(build_opensearch_index.py로 미리 색인해둔 이름, 기본은 table-name과 동일)만
바꿔서 돌릴 수 있다.
API 비용: 로컬 생성은 $0, OpenAI 임베딩(질문 벡터) + Voyage rerank만 소액 발생."""
import argparse
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
from app.eval.log_store import append_run, load_runs
from local_llm_experiment.hybrid_rerank_search import get_opensearch_client, hybrid_rerank_search

_REQUIRED_RAGAS_METRIC_NAMES = {
    "faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness",
}


def _find_already_completed_case_ids(agent_label: str) -> set[str]:
    """run_local_model_combo.py와 동일 관례 - 이어하기용."""
    completed = set()
    for record in load_runs(agent_label=agent_label):
        metric_names = {m.name for m in record.metrics}
        if _REQUIRED_RAGAS_METRIC_NAMES.issubset(metric_names):
            completed.add(record.case_id)
    return completed


LOCAL_SERVER_URL = "http://192.168.1.147:8820/v1"
LOCAL_MODEL_NAME = "EXAONE-4.0-32B"

ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


def search_and_generate(
    *, database_connection, os_client, gen_client, gen_model, cases, table_name, index_name, top_k,
    question_vectors_by_case_id, mode, candidate_pool_size, rerank_candidates, rrf_k,
    generation_extra_body=None,
):
    from ragas import SingleTurnSample

    ragas_samples = []
    extra_info_by_case_id: dict = {}

    with database_connection.cursor() as cursor:
        for case_index, case in enumerate(cases):
            case_started = time.time()
            vec = question_vectors_by_case_id[case.case_id]
            retrieved_ids, content_by_id, diagnostics = hybrid_rerank_search(
                cursor=cursor, os_client=os_client, table_name=table_name, index_name=index_name,
                query_text=case.question, query_vec=vec, final_top_k=top_k,
                candidate_pool_size=candidate_pool_size, rerank_candidates=rerank_candidates, rrf_k=rrf_k,
                mode=mode,
            )
            retrieved_chunk_texts = [content_by_id[d] for d in retrieved_ids]
            print(f"  [{case_index + 1}/{len(cases)}] {case.case_id}: 검색 완료({len(retrieved_ids)}건, mode={mode}), 생성 요청 중...", flush=True)

            context_block = "\n\n".join(f"[문서 {i + 1}]\n{t}" for i, t in enumerate(retrieved_chunk_texts))
            user_message = f"[검색된 문서]\n{context_block}\n\n[질문]\n{case.question}"

            create_kwargs = {}
            if generation_extra_body:
                create_kwargs["extra_body"] = generation_extra_body
            response = gen_client.chat.completions.create(
                model=gen_model,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
                **create_kwargs,
            )
            generated_answer = response.choices[0].message.content or ""
            print(f"  [{case_index + 1}/{len(cases)}] {case.case_id}: 생성 완료 ({time.time() - case_started:.1f}초)", flush=True)

            ragas_samples.append(SingleTurnSample(
                user_input=case.question,
                retrieved_contexts=retrieved_chunk_texts,
                response=generated_answer,
                reference=case.ground_truth,
            ))

            relevant_document_ids = R._get_relevant_document_ids(case)
            hit_and_rank_metrics = R._compute_hit_rate_and_reciprocal_rank(retrieved_ids, relevant_document_ids)
            extra_info_by_case_id[case.case_id] = {
                "answer": generated_answer,
                "retrieved_parent_ids": retrieved_ids,
                "retrieved_total_chars": sum(len(t) for t in retrieved_chunk_texts),
                "evidence_coverage": R._compute_evidence_coverage(case, retrieved_chunk_texts),
                "retrieval_diagnostics": diagnostics,
                **hit_and_rank_metrics,
            }

    return ragas_samples, extra_info_by_case_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table-name", required=True, help="예: rag_documents_eval_cs1200_ov0 또는 ..._tok900_ov0")
    parser.add_argument("--index-name", default=None, help="build_opensearch_index.py로 만든 OpenSearch 인덱스 이름 (생략 시 table-name과 동일)")
    parser.add_argument("--agent-label", required=True, help="예: cs1200_hybrid_rerank 또는 tok900_hybrid_rerank")
    parser.add_argument("--mode", choices=["vector", "hybrid", "hybrid_rerank"], default="hybrid_rerank")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-size", type=int, default=50, help="RRF 융합 전 벡터/BM25 각 branch 후보 수 (프로덕션 기본값)")
    parser.add_argument("--rerank-candidates", type=int, default=20, help="RRF 융합 후 reranker에 넘길 상한 (프로덕션 기본값)")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF 상수 k (프로덕션 기본값)")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--judge", choices=["gpt4o-mini", "local-reasoning", "local-no-reasoning"], default="local-no-reasoning")
    parser.add_argument("--local-model", default="EXAONE-4.0-32B")
    parser.add_argument("--generator", choices=["local", "gpt4o-mini"], default="local")
    parser.add_argument("--local-gen-reasoning", action="store_true")
    parser.add_argument("--log-dir", default="data")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    index_name = args.index_name or args.table_name

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    global LOCAL_MODEL_NAME
    LOCAL_MODEL_NAME = args.local_model

    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)
    local_client = OpenAI(api_key="not-needed", base_url=LOCAL_SERVER_URL, timeout=420.0)

    if args.generator == "gpt4o-mini":
        gen_client, gen_model = openai_client, R.GENERATOR_MODEL
        generation_extra_body = None
    else:
        gen_client, gen_model = local_client, LOCAL_MODEL_NAME
        generation_extra_body = {"chat_template_kwargs": {"enable_thinking": args.local_gen_reasoning}}

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
    from langchain_core.callbacks import BaseCallbackHandler

    from local_llm_experiment.custom_ragas_prompts import LanguageMatchedResponseRelevancePrompt

    class _JudgeCaptureHandler(BaseCallbackHandler):
        def __init__(self):
            self.captured: list[str] = []

        def on_llm_end(self, response, **kwargs):
            for generation_list in response.generations:
                for generation in generation_list:
                    text = getattr(generation, "text", None) or getattr(generation.message, "content", "")
                    self.captured.append(text)

    judge_capture = _JudgeCaptureHandler()

    if args.judge == "gpt4o-mini":
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=R.GENERATOR_MODEL, api_key=api_key, temperature=0, callbacks=[judge_capture],
        ))
        evaluator_model_name = R.GENERATOR_MODEL
        evaluator_reasoning_flag = None
    elif args.judge == "local-reasoning":
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=LOCAL_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": True}},
            callbacks=[judge_capture],
        ))
        evaluator_model_name = LOCAL_MODEL_NAME
        evaluator_reasoning_flag = True
    else:  # local-no-reasoning
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=LOCAL_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            callbacks=[judge_capture],
        ))
        evaluator_model_name = LOCAL_MODEL_NAME
        evaluator_reasoning_flag = False

    judge_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(api_key=api_key, model=R.EMBEDDING_MODEL))
    ragas_metrics = [
        Faithfulness(llm=judge_llm),
        AnswerRelevancy(
            llm=judge_llm, embeddings=judge_embeddings,
            question_generation=LanguageMatchedResponseRelevancePrompt(),
        ),
        LLMContextPrecisionWithReference(llm=judge_llm),
        LLMContextRecall(llm=judge_llm),
        AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings),
    ]
    ragas_run_config = RunConfig(timeout=420, max_workers=1, max_retries=8)

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    cases = all_cases[: args.max_cases] if args.max_cases else all_cases

    if not args.no_resume:
        already_done = _find_already_completed_case_ids(args.agent_label)
        if already_done:
            before = len(cases)
            cases = [c for c in cases if c.case_id not in already_done]
            print(
                f"{args.agent_label}: 이어하기 — 이미 5개 지표 다 채점된 {before - len(cases)}건 건너뜀 "
                f"(--no-resume로 끌 수 있음)", flush=True,
            )

    print(f"{args.agent_label}: 대상 케이스 {len(cases)}건 (table={args.table_name}, index={index_name}, mode={args.mode})", flush=True)

    os_client = get_opensearch_client()

    with psycopg.connect(R._build_local_database_connection_string()) as database_connection:
        case_id_to_source_type = R._tag_case_source_types(database_connection, cases)
        question_vectors_by_case_id = R._embed_all_questions_once(openai_client, cases, [0])

        started = time.time()
        ragas_samples, extra_info_by_case_id = search_and_generate(
            database_connection=database_connection, os_client=os_client, gen_client=gen_client, gen_model=gen_model,
            cases=cases, table_name=args.table_name, index_name=index_name, top_k=args.top_k,
            question_vectors_by_case_id=question_vectors_by_case_id, mode=args.mode,
            candidate_pool_size=args.candidate_pool_size, rerank_candidates=args.rerank_candidates, rrf_k=args.rrf_k,
            generation_extra_body=generation_extra_body,
        )
        print(f"{args.agent_label}: 검색+생성 완료 ({len(ragas_samples)}건, 로컬모델이라 $0)", flush=True)

        evaluation_id = f"{args.agent_label}_{int(started)}"[:40]
        saved = 0
        this_run_records = []
        for batch_start in range(0, len(cases), R.RAGAS_JUDGE_BATCH_SIZE):
            batch_end = batch_start + R.RAGAS_JUDGE_BATCH_SIZE
            batch_cases = cases[batch_start:batch_end]
            batch_samples = ragas_samples[batch_start:batch_end]

            judge_capture.captured.clear()
            batch_result = ragas_evaluate(
                dataset=EvaluationDataset(samples=batch_samples),
                metrics=ragas_metrics,
                run_config=ragas_run_config,
            )
            judge_log_path = log_dir / f"judge_raw_{args.agent_label}.jsonl"
            with open(judge_log_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps({
                    "batch_case_ids": [c.case_id for c in batch_cases],
                    "judge_raw_responses": list(judge_capture.captured),
                }, ensure_ascii=False) + "\n")
            batch_df = batch_result.to_pandas()
            for row_index, case in enumerate(batch_cases):
                result_row = batch_df.iloc[row_index]
                extra_info = extra_info_by_case_id[case.case_id]
                m = re.search(r"_(?:cs|tok)(\d+)_ov(\d+)", args.table_name)
                parsed_size = int(m.group(1)) if m else 1
                parsed_overlap = int(m.group(2)) if m else 0
                record = R._build_result_record(
                    result_row=result_row, case=case, extra_info=extra_info,
                    source_type=case_id_to_source_type.get(case.case_id, "unknown"),
                    evaluation_id=evaluation_id, agent_label=args.agent_label,
                    chunk_size=parsed_size, overlap=parsed_overlap, top_k=args.top_k,
                    generator_model=gen_model, evaluator_model=evaluator_model_name,
                    evaluator_reasoning=evaluator_reasoning_flag,
                    generator_reasoning=(args.local_gen_reasoning if args.generator == "local" else None),
                )
                append_run(record)
                this_run_records.append(record)
                saved += 1
            print(f"{args.agent_label}: 채점 진행 {min(batch_end, len(cases))}/{len(cases)}건", flush=True)

        results_copy_path = log_dir / f"eval_runs_{args.agent_label}.jsonl"
        with open(results_copy_path, "w", encoding="utf-8") as f:
            for record in this_run_records:
                f.write(record.model_dump_json() + "\n")

        elapsed_min = (time.time() - started) / 60
        result = {"ok": True, "agent_label": args.agent_label, "cases": len(cases), "saved": saved,
                  "elapsed_min": round(elapsed_min, 1)}
        print("RESULT_JSON: " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
