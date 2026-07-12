"""RAGAS 기반 RAG 검색 품질 평가용 스키마.

기존 pm4py/worfbench 평가(app/eval/)는 "에이전트가 만든 워크플로우가 정답 액션
시퀀스와 얼마나 맞는가"를 본다 — 이건 RAG 검색 자체("질문에 맞는 문서를 찾아왔는가,
그 문서로 답을 만들 수 있는가")를 보는 별개 축이라 케이스 형태도 다르다(question/
ground_truth, workflow 아님).
"""

from pydantic import BaseModel, Field


class RagasCase(BaseModel):
    """RAG 검색 품질 평가 케이스 1건. ground_truth는 사람이 검증한 정답 요약이고,
    reference_doc_ids는 "이상적으로는 이 문서(들)가 검색돼야 한다"는 참고용 기록—
    RAGAS 지표 계산에는 안 쓰이고(LLM judge가 관련성을 직접 판단), 사람이 결과를
    검토할 때 참고하는 용도."""

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    ground_truth: str = Field(min_length=1, description="사람이 검증한 정답 요약")
    reference_doc_ids: list[str] = Field(default_factory=list, description="참고용 — 채점에 안 쓰임")


class RagasCaseResult(BaseModel):
    """케이스 1건의 실행 결과 — retrieved_contexts/response까지 원본 보존(raw로 저장돼
    사람이 나중에 "왜 이 점수가 나왔는지" 들여다볼 수 있게)."""

    case_id: str
    question: str
    retrieved_contexts: list[str]
    retrieved_doc_ids: list[str] = Field(default_factory=list, description="실제 검색된 문서 id — reference_doc_ids와 대조용")
    reference_doc_ids: list[str] = Field(default_factory=list)
    response: str
    ground_truth: str
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    error: str | None = None
