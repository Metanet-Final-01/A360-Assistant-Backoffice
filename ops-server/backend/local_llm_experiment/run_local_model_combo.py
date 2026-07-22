"""로컬 모델(EXAONE-4.5-33B, OpenAI 호환 /v1/chat/completions)로 생성+채점(RAGAS judge도
로컬 모델)하는 vector-only(reranker 없음) 실험. 임베딩은 OpenAI(text-embedding-3-small)
그대로 씀. 문자 기준 후보 테이블(rag_documents_eval_cs{N}_ov0)과 토큰 기준 후보 테이블
(rag_documents_eval_tok{N}_ov0)을 --table-name으로 받아서 그대로 검색한다.
API 비용: 로컬 모델은 $0, OpenAI 임베딩만 소액 발생."""
import argparse
import json
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(".env")

import os

import psycopg

from app.eval.ragas_eval import chunk_experiment_runner as R
from app.eval.log_store import append_run
from app.eval.ragas_eval import usage_log

LOCAL_SERVER_URL = "http://192.168.1.147:8820/v1"
LOCAL_MODEL_NAME = "EXAONE-4.5-33B"  # main()에서 --local-model로 덮어씀(전역, 함수들이 호출 시점에 읽음)

ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


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


def search_and_generate(*, database_connection, gen_client, gen_model, cases, table_name, top_k, question_vectors_by_case_id):
    from ragas import SingleTurnSample

    ragas_samples = []
    extra_info_by_case_id: dict = {}

    with database_connection.cursor() as cursor:
        for case_index, case in enumerate(cases):
            case_started = time.time()
            vec = question_vectors_by_case_id[case.case_id]
            retrieved_ids, content_by_id = vector_search(cursor, table_name, vec, top_k)
            retrieved_chunk_texts = [content_by_id[d] for d in retrieved_ids]
            print(f"  [{case_index + 1}/{len(cases)}] {case.case_id}: 검색 완료({len(retrieved_ids)}건), 생성 요청 중...", flush=True)

            context_block = "\n\n".join(f"[문서 {i + 1}]\n{t}" for i, t in enumerate(retrieved_chunk_texts))
            user_message = f"[검색된 문서]\n{context_block}\n\n[질문]\n{case.question}"

            response = gen_client.chat.completions.create(
                model=gen_model,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
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
                **hit_and_rank_metrics,
            }

    return ragas_samples, extra_info_by_case_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table-name", required=True, help="예: rag_documents_eval_cs1200_ov0 또는 rag_documents_eval_tok600_ov0")
    parser.add_argument("--agent-label", required=True, help="예: cs1200_local 또는 tok600_local")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--judge", choices=["gpt4o-mini", "local-reasoning", "local-no-reasoning"],
                         default="gpt4o-mini")
    parser.add_argument("--local-model", default="EXAONE-4.5-33B")
    parser.add_argument("--generator", choices=["local", "gpt4o-mini"], default="local")
    args = parser.parse_args()

    global LOCAL_MODEL_NAME
    LOCAL_MODEL_NAME = args.local_model

    api_key = os.getenv("OPENAI_API_KEY")
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key)
    local_client = OpenAI(api_key="not-needed", base_url=LOCAL_SERVER_URL, timeout=180.0)

    if args.generator == "gpt4o-mini":
        gen_client, gen_model = openai_client, R.GENERATOR_MODEL
    else:
        gen_client, gen_model = local_client, LOCAL_MODEL_NAME

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

    # RAGAS to_pandas()는 최종 점수만 주고 판단 근거(reason) 원문은 안 준다 — 콜백으로
    # judge LLM의 실제 응답 원문을 직접 캡처해서 별도 파일에 저장한다(디버깅용).
    class _JudgeCaptureHandler(BaseCallbackHandler):
        def __init__(self):
            self.captured: list[str] = []

        def on_llm_end(self, response, **kwargs):
            for generation_list in response.generations:
                for generation in generation_list:
                    text = getattr(generation, "text", None) or getattr(generation.message, "content", "")
                    self.captured.append(text)

    judge_capture = _JudgeCaptureHandler()

    # EXAONE-4.5는 reasoning 모드가 기본이라 reasoning_content만 잔뜩 쓰다 max_tokens를
    # 다 써버려서 RAGAS가 요구하는 최종 content를 못 내놓는 문제를 실측으로 확인했다.
    # chat_template_kwargs={"enable_thinking": False}로 reasoning을 끌 수 있다는 것도
    # 실측 확인함(1+1 질문 기준 reasoning_len 716 -> 0). 세 조건(gpt-4o-mini / 로컬
    # reasoning 유지 / 로컬 reasoning 끔) 비교용으로 --judge 옵션을 둔다.
    if args.judge == "gpt4o-mini":
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=R.GENERATOR_MODEL, api_key=api_key, temperature=0, callbacks=[judge_capture],
        ))
    elif args.judge == "local-reasoning":
        # EXAONE-4.0은 reasoning이 기본 꺼짐이라 enable_thinking:true를 명시해야 실제로
        # 켜진다(4.5는 기본 켜짐이라 원래 안 줘도 켜졌었음 — 모델 무관하게 항상 명시).
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=LOCAL_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
            max_tokens=2048, extra_body={"chat_template_kwargs": {"enable_thinking": True}},
            callbacks=[judge_capture],
        ))
    else:  # local-no-reasoning
        judge_llm = LangchainLLMWrapper(ChatOpenAI(
            model=LOCAL_MODEL_NAME, api_key="not-needed", base_url=LOCAL_SERVER_URL, temperature=0,
            max_tokens=1024, extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            callbacks=[judge_capture],
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

    all_cases = [c for c in R.load_all_cases() if c.status == "approved" and c.dataset_membership == "active"]
    cases = all_cases[: args.max_cases] if args.max_cases else all_cases
    print(f"{args.agent_label}: 대상 케이스 {len(cases)}건 (table={args.table_name})", flush=True)

    with psycopg.connect(R._build_local_database_connection_string()) as database_connection:
        case_id_to_source_type = R._tag_case_source_types(database_connection, cases)
        question_vectors_by_case_id = R._embed_all_questions_once(openai_client, cases, [0])

        started = time.time()
        ragas_samples, extra_info_by_case_id = search_and_generate(
            database_connection=database_connection, gen_client=gen_client, gen_model=gen_model, cases=cases,
            table_name=args.table_name, top_k=args.top_k,
            question_vectors_by_case_id=question_vectors_by_case_id,
        )
        print(f"{args.agent_label}: 검색+생성 완료 ({len(ragas_samples)}건, 로컬모델이라 $0)", flush=True)

        evaluation_id = f"{args.agent_label}_{int(started)}"[:40]
        saved = 0
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
            # 이 배치 케이스들의 judge 원문 응답을 통째로 저장(케이스 단위 정확 매핑은
            # RAGAS 내부 병렬 호출 순서상 어려워서, 배치 단위로 같이 남긴다).
            judge_log_path = f"data/judge_raw_{args.agent_label}.jsonl"
            with open(judge_log_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps({
                    "batch_case_ids": [c.case_id for c in batch_cases],
                    "judge_raw_responses": list(judge_capture.captured),
                }, ensure_ascii=False) + "\n")
            batch_df = batch_result.to_pandas()
            for row_index, case in enumerate(batch_cases):
                result_row = batch_df.iloc[row_index]
                extra_info = extra_info_by_case_id[case.case_id]
                # table_name(rag_documents_eval_cs1200_ov0 또는 ..._tok600_ov0)에서 실제 크기/overlap을
                # 파싱한다 — 0/0으로 넘기면 _build_result_record 내부에서 overlap/chunk_size가
                # ZeroDivisionError로 죽는다(실제로 겪은 버그).
                m = re.search(r"_(?:cs|tok)(\d+)_ov(\d+)", args.table_name)
                parsed_size = int(m.group(1)) if m else 1
                parsed_overlap = int(m.group(2)) if m else 0
                record = R._build_result_record(
                    result_row=result_row, case=case, extra_info=extra_info,
                    source_type=case_id_to_source_type.get(case.case_id, "unknown"),
                    evaluation_id=evaluation_id, agent_label=args.agent_label,
                    chunk_size=parsed_size, overlap=parsed_overlap, top_k=args.top_k,
                )
                append_run(record)
                saved += 1
            print(f"{args.agent_label}: 채점 진행 {min(batch_end, len(cases))}/{len(cases)}건", flush=True)

        elapsed_min = (time.time() - started) / 60
        result = {"ok": True, "agent_label": args.agent_label, "cases": len(cases), "saved": saved,
                  "elapsed_min": round(elapsed_min, 1)}
        print("RESULT_JSON: " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
