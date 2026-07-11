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
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .schema import RagasCase, RagasCaseResult

logger = logging.getLogger(__name__)

_CASES_PATH = Path(__file__).resolve().parent / "cases" / "rag_goldset_v1.json"

# app/eval/executor.state와 같은 발상 — 폴링용 실행 상태(subprocess가 아니라
# BackgroundTasks로 인프로세스 실행되지만, 프론트가 "실행 중/완료" 폴링하는 UX는 동일).
state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "saved": 0,
    "cases": 0,
    "error": None,
}

_ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


class RagasNotConfiguredError(RuntimeError):
    """OPENAI_API_KEY 미설정 — ops-server/backend/.env에 채워야 함."""


def load_cases() -> list[RagasCase]:
    if not _CASES_PATH.exists():
        return []
    import json

    raw = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return [RagasCase.model_validate(c) for c in raw]


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


def run_ragas_eval(backend_url: str | None = None, judge_model: str = "gpt-4o-mini") -> list[RagasCaseResult]:
    """골드셋 전체를 실행하고 케이스별 결과를 반환한다. 실패한 케이스는 error 필드에
    이유를 남기고 계속 진행한다(한 케이스 실패가 전체를 막지 않음)."""
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

    for case in cases:
        try:
            hits = _search_backend(backend_url, case.question)
            contexts = [h.get("content") or "" for h in hits if h.get("content")]
            if not contexts:
                results.append(RagasCaseResult(
                    case_id=case.case_id, question=case.question, retrieved_contexts=[],
                    response="", ground_truth=case.ground_truth,
                    error="검색 결과가 없습니다(RAG 인덱스가 비어있거나 백엔드 연결 실패 가능성).",
                ))
                continue
            answer = _generate_answer(client, judge_model, case.question, contexts)
            samples.append(SingleTurnSample(
                user_input=case.question, retrieved_contexts=contexts,
                response=answer, reference=case.ground_truth,
            ))
            sample_cases.append(case)
            results.append(RagasCaseResult(
                case_id=case.case_id, question=case.question, retrieved_contexts=contexts,
                response=answer, ground_truth=case.ground_truth,
            ))
        except (httpx.HTTPError, ValidationError) as e:
            results.append(RagasCaseResult(
                case_id=case.case_id, question=case.question, retrieved_contexts=[],
                response="", ground_truth=case.ground_truth, error=str(e),
            ))

    if not samples:
        return results

    scored = ragas_evaluate(dataset=EvaluationDataset(samples=samples), metrics=metrics)
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
    BackgroundTasks로 호출되므로 예외를 여기서 삼키고 state.error에 남긴다 — 그러지
    않으면 FastAPI 백그라운드 태스크 예외가 로그에만 찍히고 프론트는 영원히 "실행 중"
    으로 보게 된다."""
    state.update({"running": True, "started_at": datetime.now(timezone.utc).isoformat(),
                  "finished_at": None, "saved": 0, "cases": 0, "error": None})
    try:
        results = run_ragas_eval(judge_model=judge_model)
        saved = 0
        for r in results:
            record = EvalRunRecord(
                case_id=r.case_id, source="ragas", agent_label=agent_label,
                metrics=_to_metrics(r),
                raw={
                    "question": r.question, "retrieved_contexts": r.retrieved_contexts,
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
