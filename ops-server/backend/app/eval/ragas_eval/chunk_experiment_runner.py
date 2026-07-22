"""chunk_size(1단계) 등 검색 하이퍼파라미터 그리드서치 실행기.

runner.py(라이브 Backend 검색 채점)와 달리, 로컬 chunk_size 후보 테이블
(rag_documents_eval_cs{size}_ov{overlap}, rag-server 쪽에서 미리 만들어 임베딩까지
끝내둔 평가 전용 테이블)에 벡터 유사도 검색만 붙여서 chunk_size 자체의 효과를
다른 변수 없이 비교한다. 결과는 새 저장소를 안 만들고 기존 EvalRunRecord/log_store에
source="ragas_chunk_experiment", agent_label=f"cs{chunk_size}_ov{overlap}"로 저장한다
— ragas_evaluation.py의 결과 비교 UI(agent_label별 그룹핑)를 그대로 재사용하기 위함.

CLI 버전(scripts/ragas_eval/chunk_candidates/run_chunk_size_comparison.py)에서
겪은 문제 두 가지를 여기서도 반영한다:

1) RAGAS 기본 동시성(max_workers=16)이 타임아웃을 대량 유발해 4로, 그다음 2로
   낮췄지만 100케이스 실측에서 매번 OpenAI rate limit(gpt-4o-mini 실측:
   200K TPM/500 RPM)에 걸려 재시도가 누적되며 항목당 처리시간이 1초→11~14초로
   급감하는 현상이 반복됐다. "안전한 동시성 값"을 계속 찾기보다 완전 순차
   (max_workers=1)로 확정했다 — 느리지만 버스트로 한도를 넘겨 재시도 폭주에
   빠질 위험 자체가 없다.

2) RAGAS 채점을 작은 배치(RAGAS_JUDGE_BATCH_SIZE)로 나눠서, 배치 하나가 끝날
   때마다 바로 로그를 남기고 append_run()으로 저장한다. 100케이스를 한 번에
   통째로 채점하면 그 호출이 끝나기 전까지는 로그도 저장도 전혀 없어서, 중간에
   죽으면 그 chunk_size에서 이미 낸 비용이 결과물 없이 전부 사라진다 —
   실제로 겪은 문제라 배치로 나눴다.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

import psycopg

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from . import usage_log
from .runner import RagasGoldsetError, RagasNotConfiguredError, load_all_cases

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------

MAX_LOG_LINES_KEPT = 200

CHUNK_SIZES = [300, 600, 900, 1200, 1500]
DEFAULT_OVERLAP = 0
DEFAULT_TOP_K = 5

GENERATOR_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
PROMPT_VERSION = "answer_v1"

# RAGAS 채점을 이 크기 단위로 쪼개서 호출한다. 값이 작을수록 진행 로그와 중간
# 저장이 자주 일어나지만, RAGAS evaluate() 호출 자체의 오버헤드도 늘어난다.
RAGAS_JUDGE_BATCH_SIZE = 10

# gpt-4o-mini 공개 단가(2026-07 기준) — 실제 프로덕션 모델(gpt-5.4-mini)과 다르다.
# 그리드서치는 저렴한 모델로, 최종 확정 설정만 프로덕션 모델로 재검증하는 2단 구성.
GPT4O_MINI_INPUT_COST_PER_MILLION_TOKENS = 0.15
GPT4O_MINI_OUTPUT_COST_PER_MILLION_TOKENS = 0.60
EMBEDDING_INPUT_COST_PER_MILLION_TOKENS = 0.02

# 실행 상태 — main.py의 상태 조회/SSE 엔드포인트가 이 딕셔너리를 그대로 읽는다.
state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "saved": 0,
    "cases": 0,
    "error": None,
    "log": [],
}

# state["log"]는 메모리뿐이라 백엔드가 재시작되면 사라진다. 실행 전체 이력을
# 남기려고 파일에도 이어 쓴다(append-only, log_store.py의 JSONL과 같은 발상).
LOCAL_LOG_FILE_PATH = Path(__file__).resolve().parents[3] / "data" / "chunk_experiment_runs.log"

# 질문 임베딩은 chunk_size와 무관하게 항상 동일하다(같은 질문, 같은 모델이면 같은
# 벡터). 그래서 실행 한 번 안에서만 재사용하는 게 아니라 파일에 영구 저장해서
# 다음 실행에서도 재사용한다. 실험 도중 죽어서 재실행하거나 나중에 2단계
# (overlap)를 돌릴 때도 이미 승인된 케이스 질문은 다시 임베딩하지 않는다.
# 캐시 키는 (임베딩 모델, 질문 원문)을 합쳐 만든 해시값이다.
EMBEDDING_CACHE_FILE_PATH = Path(__file__).resolve().parents[3] / "data" / "chunk_experiment_embed_cache.json"

ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


# ---------------------------------------------------------------------------
# 로그 + 실행 상태
# ---------------------------------------------------------------------------


def _append_log(message: str) -> None:
    timestamp_for_display = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_line = f"{timestamp_for_display} {message}"
    state["log"].append(log_line)

    if len(state["log"]) > MAX_LOG_LINES_KEPT:
        state["log"] = state["log"][-MAX_LOG_LINES_KEPT:]

    try:
        LOCAL_LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp_for_file = datetime.now(timezone.utc).isoformat()
        with open(LOCAL_LOG_FILE_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp_for_file} {message}\n")
    except OSError as error:
        # 로컬 로그 파일 기록이 실패해도 실험 자체는 계속 진행해야 한다.
        logger.warning("로컬 실행 로그 기록 실패: %s", error)


def reserve() -> bool:
    """runner.py::reserve()와 같은 규칙을 쓴다. 다만 별도 실행기이므로 running
    플래그도 따로 둔다 — chunk 실험과 일반 RAGAS 평가는 동시에 돌려도 서로
    막지 않지만, chunk 실험끼리는 중복 실행을 막는다."""
    if state["running"]:
        return False

    state.update({
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "saved": 0,
        "cases": 0,
        "error": None,
        "log": [],
    })
    return True


# ---------------------------------------------------------------------------
# 질문 임베딩 캐시
# ---------------------------------------------------------------------------


def _build_embedding_cache_key(question: str) -> str:
    raw_key = f"{EMBEDDING_MODEL}:{question}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _load_embedding_cache() -> dict[str, list[float]]:
    try:
        with open(EMBEDDING_CACHE_FILE_PATH, encoding="utf-8") as cache_file:
            return json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_embedding_cache(cache: dict[str, list[float]]) -> None:
    EMBEDDING_CACHE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 저장 도중 죽어도 캐시 파일 자체가 깨지지 않도록, 임시 파일에 먼저 쓰고
    # 다 쓴 다음에 원래 이름으로 바꾼다.
    temporary_path = EMBEDDING_CACHE_FILE_PATH.with_suffix(".tmp")
    with open(temporary_path, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, ensure_ascii=False)
    temporary_path.replace(EMBEDDING_CACHE_FILE_PATH)


def prepare_question_embeddings() -> dict:
    """승인된 골드셋 질문을 실험 실행과 별개로 미리 임베딩해서 캐시를 채운다.

    실험 '실행' 버튼을 누르기 전에 따로 호출할 수 있게 분리했다. 실험 실행
    자체는 이 캐시를 읽기만 하고, 캐시에 없는 질문만 그 자리에서 추가로
    임베딩한다.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RagasNotConfiguredError("OPENAI_API_KEY가 설정되지 않았습니다.")

    from openai import OpenAI

    openai_client = OpenAI(api_key=api_key)
    # dataset_membership="active"인 케이스만 — 실험 세트 범위와 어긋나게 후보/제외
    # 케이스까지 미리 임베딩해두면 캐시만 낭비된다.
    approved_cases = [
        case for case in load_all_cases()
        if case.status == "approved" and case.dataset_membership == "active"
    ]
    embedding_cache = _load_embedding_cache()

    cache_hit_count = 0
    newly_embedded_count = 0
    total_embedding_tokens_used = 0

    for case in approved_cases:
        cache_key = _build_embedding_cache_key(case.question)

        if cache_key in embedding_cache:
            cache_hit_count += 1
            continue

        response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=case.question)
        embedding_cache[cache_key] = response.data[0].embedding
        total_embedding_tokens_used += response.usage.total_tokens
        newly_embedded_count += 1

    _save_embedding_cache(embedding_cache)

    if total_embedding_tokens_used > 0:
        cost_usd = total_embedding_tokens_used * EMBEDDING_INPUT_COST_PER_MILLION_TOKENS / 1_000_000
        usage_log.record_usage(
            purpose="chunk_experiment_question_embedding_prepare",
            model=EMBEDDING_MODEL,
            input_tokens=total_embedding_tokens_used,
            output_tokens=0,
            cost_usd=cost_usd,
        )

    return {
        "total": len(approved_cases),
        "cache_hits": cache_hit_count,
        "newly_embedded": newly_embedded_count,
    }


# ---------------------------------------------------------------------------
# 검색 품질 직접 지표 (RAGAS 판정과 별개로 직접 계산하는 값들)
# ---------------------------------------------------------------------------


def _get_relevant_document_ids(case) -> set[str]:
    relevant_ids = set(case.reference_doc_ids)
    for reference_context in case.reference_contexts:
        relevant_ids.add(reference_context.source_document_id)
    return relevant_ids


def _compute_hit_rate_and_reciprocal_rank(
    retrieved_document_ids: list[str],
    relevant_document_ids: set[str],
) -> dict:
    """검색된 문서 목록에서 정답 문서가 몇 번째 순서로 나왔는지로 Hit@1/3/5와
    MRR(reciprocal rank)을 계산한다. 정답 문서 정보가 아예 없는 케이스는 빈
    딕셔너리를 반환해서(이 지표를 아예 안 남긴다), 다른 케이스와 섞여서 평균이
    왜곡되지 않게 한다."""
    if not relevant_document_ids:
        return {}

    rank_of_first_relevant_document = None
    for position, document_id in enumerate(retrieved_document_ids):
        if document_id in relevant_document_ids:
            rank_of_first_relevant_document = position + 1  # 순위는 1부터 센다
            break

    if rank_of_first_relevant_document is None:
        return {
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "hit_at_5": 0.0,
            "reciprocal_rank": 0.0,
        }

    return {
        "hit_at_1": float(rank_of_first_relevant_document <= 1),
        "hit_at_3": float(rank_of_first_relevant_document <= 3),
        "hit_at_5": float(rank_of_first_relevant_document <= 5),
        "reciprocal_rank": 1.0 / rank_of_first_relevant_document,
    }


def _remove_all_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _compute_evidence_coverage(case, retrieved_chunk_texts: list[str]) -> float | None:
    """정답 근거 스니펫(reference_contexts)이 실제로 검색된 청크 안에 얼마나
    들어있는지를 직접 확인한다. 공백 차이는 무시하고 문자열 포함 여부만 본다.
    RAGAS의 LLM 판정과는 완전히 별개의, 사람이 검증하기 쉬운 값이다."""
    evidence_snippets = [reference_context.snippet for reference_context in case.reference_contexts]
    if not evidence_snippets:
        return None

    combined_retrieved_text = _remove_all_whitespace("\n".join(retrieved_chunk_texts))

    found_snippet_count = 0
    for snippet in evidence_snippets:
        if _remove_all_whitespace(snippet) in combined_retrieved_text:
            found_snippet_count += 1

    return found_snippet_count / len(evidence_snippets)


def _to_float_or_none(value) -> float | None:
    """RAGAS 결과 컬럼에는 값이 없을 때 None 또는 NaN이 들어올 수 있다. 둘 다
    저장하지 않고 None으로 통일한다."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN은 자기 자신과 같지 않다는 성질을 이용한 NaN 판별
        return None
    return number


def _tag_case_source_types(database_connection, cases: list) -> dict[str, str]:
    """각 케이스가 참조하는 문서들이 doc_page/action_schema/package_overview 중
    어떤 유형인지 조회한다. 케이스 하나가 여러 유형을 섞어 참조하면 "mixed"로
    표시한다. 결과 화면에서 문서 유형별로 나눠 볼 때 쓴다."""
    referenced_document_ids: set[str] = set()
    for case in cases:
        referenced_document_ids.update(case.reference_doc_ids)
        for reference_context in case.reference_contexts:
            referenced_document_ids.add(reference_context.source_document_id)

    with database_connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, source_type FROM source_documents WHERE id = ANY(%s)",
            (list(referenced_document_ids),),
        )
        document_id_to_source_type = dict(cursor.fetchall())

    case_id_to_source_type: dict[str, str] = {}
    for case in cases:
        source_types_seen = set()

        for document_id in case.reference_doc_ids:
            if document_id in document_id_to_source_type:
                source_types_seen.add(document_id_to_source_type[document_id])

        for reference_context in case.reference_contexts:
            document_id = reference_context.source_document_id
            if document_id in document_id_to_source_type:
                source_types_seen.add(document_id_to_source_type[document_id])

        if len(source_types_seen) == 0:
            case_id_to_source_type[case.case_id] = "unknown"
        elif len(source_types_seen) > 1:
            case_id_to_source_type[case.case_id] = "mixed"
        else:
            case_id_to_source_type[case.case_id] = next(iter(source_types_seen))

    return case_id_to_source_type


def _build_local_database_connection_string() -> str:
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5432"
    database_name = os.getenv("DATABASE_NAME") or "a360"
    username = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
    return f"host={host} port={port} dbname={database_name} user={username} password={password}"


# ---------------------------------------------------------------------------
# 실험 실행 본체
# ---------------------------------------------------------------------------


def run_chunk_experiment(
    chunk_sizes: list[int] | None = None,
    overlap: int = DEFAULT_OVERLAP,
    top_k: int = DEFAULT_TOP_K,
    evaluation_id: str | None = None,
    max_cases: int | None = None,
) -> None:
    """chunk_size 후보들을 순서대로 돌면서, chunk_size 하나마다 다음을 수행한다:

        1. 이 chunk_size의 후보 테이블에서 벡터 검색으로 top_k개 문서를 가져온다.
        2. 검색된 문서를 근거로 gpt-4o-mini가 답변을 생성한다.
        3. RAGAS로 답변/검색 품질을 채점한다 (작은 배치로 나눠서 진행).
        4. 배치가 끝날 때마다 즉시 결과를 저장하고 로그를 남긴다.

    reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다 — main.py가
    background task로 등록하기 전에 reserve()를 먼저 호출한다.

    max_cases는 실제 비용을 태우기 전에 코드가 제대로 도는지 소액으로 먼저
    확인하기 위한 스모크테스트용 옵션이다. None이면 승인된 케이스 전부를
    쓴다(실제 운영 실행).
    """
    chunk_sizes = chunk_sizes or CHUNK_SIZES
    evaluation_id = evaluation_id or uuid4().hex[:12]

    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RagasNotConfiguredError("OPENAI_API_KEY가 설정되지 않았습니다.")

        # ragas/openai 관련 라이브러리는 여기서만 쓰이므로, 백엔드가 켜질 때마다
        # 매번 불러오지 않도록 실제로 실험이 시작될 때 불러온다.
        from openai import OpenAI
        from ragas import SingleTurnSample
        from ragas.cost import get_token_usage_for_openai
        from ragas.dataset_schema import EvaluationDataset
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.evaluation import evaluate as ragas_evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            AnswerCorrectness,
            AnswerRelevancy,
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
        )
        from ragas.run_config import RunConfig
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        openai_client = OpenAI(api_key=api_key)

        # status=approved AND dataset_membership=active — 실험 세트로 지정된 케이스만
        # 실제 chunk_size 그리드서치에 쓴다(신규 후보를 추가해도 기존 실험 세트가
        # 의도치 않게 커지지 않도록).
        approved_cases = [
            case for case in load_all_cases()
            if case.status == "approved" and case.dataset_membership == "active"
        ]
        if max_cases is not None:
            approved_cases = approved_cases[:max_cases]
        if len(approved_cases) == 0:
            raise RagasGoldsetError("실험 세트(active)로 지정된 RAGAS 골드셋이 없습니다.")

        state.update({"cases": len(approved_cases) * len(chunk_sizes)})
        _append_log(f"승인 케이스 {len(approved_cases)}건 x chunk_size {len(chunk_sizes)}개 실험 시작")

        judge_llm = LangchainLLMWrapper(ChatOpenAI(model=GENERATOR_MODEL, api_key=api_key, temperature=0))
        judge_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(api_key=api_key, model=EMBEDDING_MODEL))
        ragas_metrics = [
            Faithfulness(llm=judge_llm),
            AnswerRelevancy(llm=judge_llm, embeddings=judge_embeddings),
            LLMContextPrecisionWithReference(llm=judge_llm),
            LLMContextRecall(llm=judge_llm),
            AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings),
        ]

        # max_workers=3에서 Job[22]가 TimeoutError(180초 초과)로 실패하는 게
        # 실측 확인됨 — 항목당 처리시간도 26.34s/item으로 max_workers=1(31.58s/item)
        # 대비 17%밖에 안 빨라져서, 재시도가 병렬 이득을 까먹는 것으로 보임.
        # 2로 낮춰서 재시도 없이 도는지 확인.
        ragas_run_config = RunConfig(timeout=180, max_workers=2, max_retries=10)

        with psycopg.connect(_build_local_database_connection_string()) as database_connection:
            case_id_to_source_type = _tag_case_source_types(database_connection, approved_cases)

            question_vectors_by_case_id = _embed_all_questions_once(
                openai_client, approved_cases, chunk_sizes,
            )

            for chunk_size in chunk_sizes:
                candidate_table_name = f"rag_documents_eval_cs{chunk_size}_ov{overlap}"

                search_and_generate_result = _search_and_generate_answers(
                    database_connection=database_connection,
                    openai_client=openai_client,
                    cases=approved_cases,
                    candidate_table_name=candidate_table_name,
                    top_k=top_k,
                    question_vectors_by_case_id=question_vectors_by_case_id,
                    build_ragas_sample=SingleTurnSample,
                )
                ragas_samples = search_and_generate_result.ragas_samples
                extra_info_by_case_id = search_and_generate_result.extra_info_by_case_id

                usage_log.record_usage(
                    purpose=f"chunk_experiment_answer_generation_cs{chunk_size}_ov{overlap}",
                    model=GENERATOR_MODEL,
                    input_tokens=search_and_generate_result.total_input_tokens,
                    output_tokens=search_and_generate_result.total_output_tokens,
                    cost_usd=search_and_generate_result.total_cost_usd,
                )
                _append_log(f"chunk_size={chunk_size}: 검색+생성 완료 ({len(ragas_samples)}건)")

                # RAGAS 채점을 작은 배치로 나눠서 돌린다. 100케이스를 통째로
                # 한 번에 채점하면 그 호출이 끝나기 전까지 로그도 저장도 전혀
                # 없다 — 이 파일 맨 위 docstring에 적어둔 실제로 겪은 문제다.
                agent_label = f"cs{chunk_size}_ov{overlap}"
                total_judging_cost_for_this_chunk_size = 0.0

                for batch_start_index in range(0, len(approved_cases), RAGAS_JUDGE_BATCH_SIZE):
                    batch_end_index = batch_start_index + RAGAS_JUDGE_BATCH_SIZE
                    batch_cases = approved_cases[batch_start_index:batch_end_index]
                    batch_samples = ragas_samples[batch_start_index:batch_end_index]

                    batch_result = ragas_evaluate(
                        dataset=EvaluationDataset(samples=batch_samples),
                        metrics=ragas_metrics,
                        token_usage_parser=get_token_usage_for_openai,
                        run_config=ragas_run_config,
                    )
                    batch_cost_usd = batch_result.total_cost(
                        cost_per_input_token=GPT4O_MINI_INPUT_COST_PER_MILLION_TOKENS / 1_000_000,
                        cost_per_output_token=GPT4O_MINI_OUTPUT_COST_PER_MILLION_TOKENS / 1_000_000,
                    )
                    total_judging_cost_for_this_chunk_size += batch_cost_usd

                    batch_token_usage = batch_result.total_tokens()
                    usage_log.record_usage(
                        purpose=f"chunk_experiment_ragas_judge_cs{chunk_size}_ov{overlap}",
                        model=GENERATOR_MODEL,
                        input_tokens=batch_token_usage.input_tokens,
                        output_tokens=batch_token_usage.output_tokens,
                        cost_usd=batch_cost_usd,
                    )

                    batch_result_dataframe = batch_result.to_pandas()
                    for row_index, case in enumerate(batch_cases):
                        result_row = batch_result_dataframe.iloc[row_index]
                        extra_info = extra_info_by_case_id[case.case_id]
                        source_type = case_id_to_source_type.get(case.case_id, "unknown")

                        record = _build_result_record(
                            result_row=result_row,
                            case=case,
                            extra_info=extra_info,
                            source_type=source_type,
                            evaluation_id=evaluation_id,
                            agent_label=agent_label,
                            chunk_size=chunk_size,
                            overlap=overlap,
                            top_k=top_k,
                        )
                        append_run(record)
                        state["saved"] += 1

                    cases_done_so_far = min(batch_end_index, len(approved_cases))
                    _append_log(
                        f"chunk_size={chunk_size}: 채점 진행 {cases_done_so_far}/{len(approved_cases)}건 "
                        f"(이 배치 비용 ${batch_cost_usd:.4f}, 전체 누적 {state['saved']}/{state['cases']}건)"
                    )

                _append_log(
                    f"chunk_size={chunk_size}: RAGAS 채점+저장 완료 "
                    f"(이 chunk_size 비용 ${total_judging_cost_for_this_chunk_size:.4f})"
                )

    except Exception as error:
        # 백그라운드 태스크 안에서 발생한 예외라서, 상태에 남겨야 프론트가 알 수 있다.
        logger.exception("chunk_size 실험 실행 실패")
        state["error"] = str(error)
    finally:
        state.update({
            "running": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })


def _embed_all_questions_once(openai_client, cases: list, chunk_sizes: list[int]) -> dict[str, list[float]]:
    """모든 케이스의 질문을 chunk_size 개수와 무관하게 딱 한 번만 임베딩한다.
    영구 캐시(EMBEDDING_CACHE_FILE_PATH)에 이미 있는 질문은 API를 호출하지
    않고 그대로 재사용한다."""
    embedding_cache = _load_embedding_cache()
    question_vectors_by_case_id: dict[str, list[float]] = {}

    cache_hit_count = 0
    total_embedding_tokens_used = 0

    for case in cases:
        cache_key = _build_embedding_cache_key(case.question)

        if cache_key in embedding_cache:
            question_vectors_by_case_id[case.case_id] = embedding_cache[cache_key]
            cache_hit_count += 1
            continue

        response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=case.question)
        question_vectors_by_case_id[case.case_id] = response.data[0].embedding
        embedding_cache[cache_key] = response.data[0].embedding
        total_embedding_tokens_used += response.usage.total_tokens

    if total_embedding_tokens_used > 0:
        _save_embedding_cache(embedding_cache)
        cost_usd = total_embedding_tokens_used * EMBEDDING_INPUT_COST_PER_MILLION_TOKENS / 1_000_000
        usage_log.record_usage(
            purpose="chunk_experiment_question_embedding",
            model=EMBEDDING_MODEL,
            input_tokens=total_embedding_tokens_used,
            output_tokens=0,
            cost_usd=cost_usd,
        )

    newly_embedded_count = len(cases) - cache_hit_count
    _append_log(
        f"질문 임베딩 준비 완료 (캐시 재사용 {cache_hit_count}건, 신규 {newly_embedded_count}건, "
        f"chunk_size {len(chunk_sizes)}개가 공유)"
    )
    return question_vectors_by_case_id


class SearchAndGenerateResult(NamedTuple):
    """_search_and_generate_answers()의 반환값. 필드 이름으로 각 값이 뭔지
    바로 알 수 있게 튜플 대신 이 타입을 쓴다."""

    ragas_samples: list
    extra_info_by_case_id: dict
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float


def _search_and_generate_answers(
    *,
    database_connection,
    openai_client,
    cases: list,
    candidate_table_name: str,
    top_k: int,
    question_vectors_by_case_id: dict[str, list[float]],
    build_ragas_sample,
) -> SearchAndGenerateResult:
    """이 chunk_size의 후보 테이블에서 top_k개를 검색하고, 검색된 문서를 근거로
    gpt-4o-mini가 답변을 생성한다.

    extra_info_by_case_id에는 검색된 문서 id, Hit@k, 생성된 답변처럼 RAGAS
    채점과는 별개로 필요한 값들이 케이스 id를 키로 들어있다.
    """
    ragas_samples = []
    extra_info_by_case_id: dict = {}
    total_generation_input_tokens = 0
    total_generation_output_tokens = 0

    with database_connection.cursor() as cursor:
        for case in cases:
            question_vector = question_vectors_by_case_id[case.case_id]
            cursor.execute(
                f"SELECT parent_id, content FROM {candidate_table_name} "
                f"ORDER BY embedding <=> %s::vector LIMIT %s",
                (question_vector, top_k),
            )
            retrieved_rows = cursor.fetchall()
            retrieved_document_ids = [row[0] for row in retrieved_rows]
            retrieved_chunk_texts = [row[1] for row in retrieved_rows]

            context_block_parts = []
            for index, chunk_text in enumerate(retrieved_chunk_texts):
                context_block_parts.append(f"[문서 {index + 1}]\n{chunk_text}")
            context_block = "\n\n".join(context_block_parts)

            user_message = f"[검색된 문서]\n{context_block}\n\n[질문]\n{case.question}"

            response = openai_client.chat.completions.create(
                model=GENERATOR_MODEL,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
            )
            total_generation_input_tokens += response.usage.prompt_tokens
            total_generation_output_tokens += response.usage.completion_tokens
            generated_answer = response.choices[0].message.content or ""

            ragas_samples.append(build_ragas_sample(
                user_input=case.question,
                retrieved_contexts=retrieved_chunk_texts,
                response=generated_answer,
                reference=case.ground_truth,
            ))

            relevant_document_ids = _get_relevant_document_ids(case)
            hit_and_rank_metrics = _compute_hit_rate_and_reciprocal_rank(
                retrieved_document_ids, relevant_document_ids,
            )

            extra_info_by_case_id[case.case_id] = {
                "answer": generated_answer,
                "retrieved_parent_ids": retrieved_document_ids,
                "retrieved_total_chars": sum(len(text) for text in retrieved_chunk_texts),
                "evidence_coverage": _compute_evidence_coverage(case, retrieved_chunk_texts),
                **hit_and_rank_metrics,
            }

    generation_cost_usd = (
        total_generation_input_tokens * GPT4O_MINI_INPUT_COST_PER_MILLION_TOKENS / 1_000_000
        + total_generation_output_tokens * GPT4O_MINI_OUTPUT_COST_PER_MILLION_TOKENS / 1_000_000
    )

    return SearchAndGenerateResult(
        ragas_samples=ragas_samples,
        extra_info_by_case_id=extra_info_by_case_id,
        total_input_tokens=total_generation_input_tokens,
        total_output_tokens=total_generation_output_tokens,
        total_cost_usd=generation_cost_usd,
    )


def _build_result_record(
    *,
    result_row,
    case,
    extra_info: dict,
    source_type: str,
    evaluation_id: str,
    agent_label: str,
    chunk_size: int,
    overlap: int,
    top_k: int,
) -> EvalRunRecord:
    metric_values_by_name = {
        "faithfulness": _to_float_or_none(result_row.get("faithfulness")),
        "answer_relevancy": _to_float_or_none(result_row.get("answer_relevancy")),
        "context_precision": _to_float_or_none(result_row.get("llm_context_precision_with_reference")),
        "context_recall": _to_float_or_none(result_row.get("context_recall")),
        "answer_correctness": _to_float_or_none(result_row.get("answer_correctness")),
        "hit_at_1": extra_info.get("hit_at_1"),
        "hit_at_3": extra_info.get("hit_at_3"),
        "hit_at_5": extra_info.get("hit_at_5"),
        "reciprocal_rank": extra_info.get("reciprocal_rank"),
        "evidence_coverage": extra_info.get("evidence_coverage"),
    }

    metrics_to_save = []
    for metric_name, metric_value in metric_values_by_name.items():
        if metric_value is not None:
            metrics_to_save.append(EvalMetric(name=metric_name, value=metric_value))

    return EvalRunRecord(
        evaluation_id=evaluation_id,
        case_execution_id=f"{evaluation_id}_{agent_label}_{case.case_id}",
        case_id=case.case_id,
        source="ragas_chunk_experiment",
        agent_label=agent_label,
        config={
            "chunk_size": chunk_size,
            "chunk_overlap": overlap,
            "overlap_ratio": round(overlap / chunk_size, 4),
            "top_k": top_k,
            "embedding_model": EMBEDDING_MODEL,
            "generator_model": GENERATOR_MODEL,
            "evaluator_model": GENERATOR_MODEL,
            "prompt_version": PROMPT_VERSION,
        },
        metrics=metrics_to_save,
        raw={
            "question": case.question,
            "ground_truth": case.ground_truth,
            "answer": extra_info["answer"],
            "source_type": source_type,
            "question_type": case.question_type,
            "retrieved_parent_ids": extra_info["retrieved_parent_ids"],
            "retrieved_total_chars": extra_info["retrieved_total_chars"],
        },
    )
