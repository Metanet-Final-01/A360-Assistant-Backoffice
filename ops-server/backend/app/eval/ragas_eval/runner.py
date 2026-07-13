"""RAGAS 기반 RAG 검색 품질 평가 실행기.

pm4py/worfbench(app/eval/executor.py)와 달리 서브프로세스가 아니라 인프로세스로
돈다 — a360-eval-sandbox의 별도 .venv-verify가 필요 없고, 순수 Python(HTTP 호출 +
OpenAI API 호출)만으로 끝나기 때문. 흐름:

1. 골드셋 케이스마다 A360-Assistant-Backend의 GET /api/rag/search를 호출해 실제
   운영 검색 파이프라인(하이브리드+RRF+Voyage 리랭크)이 찾아온 문서를 그대로 가져온다.
2. 그 문서들만 근거로 OpenAI에 직접 답변을 생성시킨다(에이전트 전체를 다시 태우지
   않음 — RAG 검색 자체의 품질을 보는 게 목적이라 생성 단계는 최대한 얇게 유지).
3. RAGAS의 4개 핵심 지표(faithfulness/answer_relevancy/context_precision/
   context_recall)를 OpenAI를 judge로 써서 계산한다.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .schema import RagasCase, RagasCaseResult

logger = logging.getLogger(__name__)

_CASES_PATH = Path(__file__).resolve().parent / "cases" / "rag_goldset_v1.json"
_MAX_LOG_LINES = 200

# app/eval/executor.state와 같은 발상 — 폴링용 실행 상태(subprocess가 아니라
# BackgroundTasks로 인프로세스 실행되지만, 프론트가 "실행 중/완료" 폴링하는 UX는 동일).
state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "saved": 0,
    "cases": 0,
    "error": None,
    "log": [],
}


def _append_log(log: list[str], message: str) -> None:
    """bfcl_eval.runner._append_log과 같은 발상 — 케이스별 진행 로그를 state에 쌓아
    프론트가 폴링하며 보여준다."""
    log.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}")
    del log[:-_MAX_LOG_LINES]


def reserve() -> bool:
    """실행을 예약한다 — main.py가 background_tasks.add_task() 전에 호출한다.
    True를 반환하면 이 호출이 예약에 성공한 것(그 즉시 running=True로 바뀜). False면
    이미 실행 중이라는 뜻. execute_and_save()가 시작된 뒤에야 running=True가 되던
    이전 방식은, 응답을 보내기 전(add_task 큐잉)과 실제 태스크 시작 사이에 동시 요청이
    둘 다 통과할 수 있는 경합 창이 있었다(CodeRabbit 지적) — 단일 워커(이 앱은 원래
    단일 프로세스 전제)에서는 GIL 덕분에 이 dict 갱신 자체는 원자적이라, 별도 락 없이도
    check-and-set을 한 함수로 합치는 것만으로 그 창을 없앨 수 있다."""
    if state["running"]:
        return False
    state.update({"running": True, "started_at": datetime.now(timezone.utc).isoformat(),
                  "finished_at": None, "saved": 0, "cases": 0, "error": None, "log": []})
    return True


_ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


class RagasNotConfiguredError(RuntimeError):
    """OPENAI_API_KEY 미설정 — ops-server/backend/.env에 채워야 함."""


class RagasGoldsetError(RuntimeError):
    """골드셋 파일이 없거나 비어 있음 — 패키징/경로 오류를 "0/0건 성공"으로 위장시키지
    않기 위해 별도 예외로 구분한다(CodeRabbit 지적 반영)."""


def load_cases() -> list[RagasCase]:
    if not _CASES_PATH.exists():
        raise RagasGoldsetError(f"골드셋 파일이 없습니다: {_CASES_PATH}")
    import json

    raw = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    cases = [RagasCase.model_validate(c) for c in raw]
    if not cases:
        raise RagasGoldsetError(f"골드셋이 비어 있습니다: {_CASES_PATH}")
    return cases


def _search_backend(backend_url: str, question: str, limit: int = 5) -> list[dict]:
    resp = httpx.get(f"{backend_url}/api/rag/search", params={"q": question, "limit": limit}, timeout=30.0)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _generate_answer(client, model: str, question: str, contexts: list[str]) -> str:
    context_block = "\n\n".join(f"[문서 {i + 1}]\n{c}" for i, c in enumerate(contexts))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"[검색된 문서]\n{context_block}\n\n[질문]\n{question}"},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content or ""


def run_ragas_eval(
    backend_url: str | None = None, judge_model: str = "gpt-4o-mini",
    on_progress: Callable[[str], None] | None = None,
) -> list[RagasCaseResult]:
    """골드셋 전체를 실행하고 케이스별 결과를 반환한다. 실패한 케이스는 error 필드에
    이유를 남기고 계속 진행한다(한 케이스 실패가 전체를 막지 않음).

    on_progress: 케이스 하나(검색+답변 생성) 끝날 때마다, 그리고 RAGAS 배치 채점
    전후에 사람이 읽을 진행 메시지를 받는 콜백 — 실시간 로그 표시용."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RagasNotConfiguredError(
            "OPENAI_API_KEY가 설정되지 않았습니다 — ops-server/backend/.env에 채워주세요."
        )

    from openai import OpenAI

    from ragas import SingleTurnSample
    from ragas.dataset_schema import EvaluationDataset
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.evaluation import evaluate as ragas_evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    backend_url = (backend_url or os.getenv("A360_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
    cases = load_cases()
    if not cases:
        return []

    client = OpenAI(api_key=api_key)
    ragas_llm = LangchainLLMWrapper(ChatOpenAI(model=judge_model, api_key=api_key, temperature=0))
    ragas_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(api_key=api_key, model="text-embedding-3-small"))
    metrics = [
        Faithfulness(llm=ragas_llm),
        AnswerRelevancy(llm=ragas_llm, embeddings=ragas_embeddings),
        LLMContextPrecisionWithReference(llm=ragas_llm),
        LLMContextRecall(llm=ragas_llm),
    ]

    results: list[RagasCaseResult] = []
    samples: list[SingleTurnSample] = []
    sample_cases: list[RagasCase] = []

    for i, case in enumerate(cases, 1):
        try:
            hits = _search_backend(backend_url, case.question)
            contexts = [h.get("content") or "" for h in hits if h.get("content")]
            doc_ids = [h["id"] for h in hits if h.get("id")]
            if not contexts:
                results.append(RagasCaseResult(
                    case_id=case.case_id, question=case.question, retrieved_contexts=[],
                    retrieved_doc_ids=doc_ids, reference_doc_ids=case.reference_doc_ids,
                    response="", ground_truth=case.ground_truth,
                    error="검색 결과가 없습니다(RAG 인덱스가 비어있거나 백엔드 연결 실패 가능성).",
                ))
                if on_progress:
                    on_progress(f"[{i}/{len(cases)}] ⚠ {case.case_id}: 검색 결과 없음")
                continue
            answer = _generate_answer(client, judge_model, case.question, contexts)
            samples.append(SingleTurnSample(
                user_input=case.question, retrieved_contexts=contexts,
                response=answer, reference=case.ground_truth,
            ))
            sample_cases.append(case)
            results.append(RagasCaseResult(
                case_id=case.case_id, question=case.question, retrieved_contexts=contexts,
                retrieved_doc_ids=doc_ids, reference_doc_ids=case.reference_doc_ids,
                response=answer, ground_truth=case.ground_truth,
            ))
            if on_progress:
                on_progress(f"[{i}/{len(cases)}] ✓ {case.case_id}: 검색+답변 생성 완료 ({len(contexts)}개 문서)")
        except Exception as e:  # noqa: BLE001 - 검색 HTTP 오류·JSON 파싱 실패·OpenAI 클라이언트
            # 오류 등 케이스 하나에서 날 수 있는 어떤 예외든 그 케이스만 실패 처리하고
            # 나머지 골드셋은 계속 돈다(한 케이스 문제로 전체 배치가 죽으면 안 됨).
            logger.warning("RAGAS 케이스 실패: %s", case.case_id, exc_info=True)
            results.append(RagasCaseResult(
                case_id=case.case_id, question=case.question, retrieved_contexts=[],
                reference_doc_ids=case.reference_doc_ids,
                response="", ground_truth=case.ground_truth, error=str(e),
            ))
            if on_progress:
                on_progress(f"[{i}/{len(cases)}] ⚠ {case.case_id} 오류: {e}")

    if not samples:
        return results

    if on_progress:
        on_progress(f"RAGAS 지표 채점 중... ({len(samples)}개 샘플, judge={judge_model})")
    scored = ragas_evaluate(dataset=EvaluationDataset(samples=samples), metrics=metrics)
    if on_progress:
        on_progress("RAGAS 지표 채점 완료")
    scored_df = scored.to_pandas()
    for i, case in enumerate(sample_cases):
        row = scored_df.iloc[i]
        target = next(r for r in results if r.case_id == case.case_id)
        target.faithfulness = _safe_float(row.get("faithfulness"))
        target.answer_relevancy = _safe_float(row.get("answer_relevancy"))
        target.context_precision = _safe_float(row.get("llm_context_precision_with_reference"))
        target.context_recall = _safe_float(row.get("context_recall"))

    return results


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        f = float(value)
        return f if f == f else None  # NaN 체크
    except (TypeError, ValueError):
        return None


def _to_metrics(result: RagasCaseResult) -> list[EvalMetric]:
    pairs = [
        ("ragas_faithfulness", result.faithfulness),
        ("ragas_answer_relevancy", result.answer_relevancy),
        ("ragas_context_precision", result.context_precision),
        ("ragas_context_recall", result.context_recall),
    ]
    return [EvalMetric(name=name, value=value) for name, value in pairs if value is not None]


def execute_and_save(agent_label: str, judge_model: str = "gpt-4o-mini") -> None:
    """run_ragas_eval()을 돌리고 케이스별로 EvalRunRecord(source="ragas")를 저장한다.
    reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다(main.py가 add_task 전에
    reserve() 호출). BackgroundTasks로 호출되므로 예외를 여기서 삼키고 state.error에
    남긴다 — 그러지 않으면 FastAPI 백그라운드 태스크 예외가 로그에만 찍히고 프론트는
    영원히 "실행 중"으로 보게 된다."""
    try:
        results = run_ragas_eval(judge_model=judge_model, on_progress=lambda msg: _append_log(state["log"], msg))
        evaluation_id = uuid4().hex[:12]  # 배치 전체를 하나로 묶는 id — 버전 비교가
        # agent_label만으로 그룹핑하면 같은 라벨로 재실행할 때마다 케이스가 섞여
        # 평균·건수가 뒤섞이는 걸 방지한다(CodeRabbit 지적 반영).
        saved = 0
        for r in results:
            record = EvalRunRecord(
                evaluation_id=evaluation_id,
                case_id=r.case_id, source="ragas", agent_label=agent_label,
                metrics=_to_metrics(r),
                raw={
                    "question": r.question, "retrieved_contexts": r.retrieved_contexts,
                    "retrieved_doc_ids": r.retrieved_doc_ids, "reference_doc_ids": r.reference_doc_ids,
                    "response": r.response, "ground_truth": r.ground_truth, "error": r.error,
                },
            )
            append_run(record)
            saved += 1
        state.update({"saved": saved, "cases": len(results)})
    except Exception as e:  # noqa: BLE001 - 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("RAGAS 평가 실행 실패")
        state["error"] = str(e)
    finally:
        state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})
