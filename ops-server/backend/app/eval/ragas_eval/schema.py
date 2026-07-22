"""RAGAS 기반 RAG 검색 품질 평가용 스키마.

기존 pm4py/worfbench 평가(app/eval/)는 "에이전트가 만든 워크플로우가 정답 액션
시퀀스와 얼마나 맞는가"를 본다 — 이건 RAG 검색 자체("질문에 맞는 문서를 찾아왔는가,
그 문서로 답을 만들 수 있는가")를 보는 별개 축이라 케이스 형태도 다르다(question/
ground_truth, workflow 아님).
"""

from typing import Literal

from pydantic import BaseModel, Field


class ReferenceContext(BaseModel):
    """정답의 근거가 되는 원문 발췌 — chunk_size 후보마다 청크 경계/id가 달라지므로,
    id가 아니라 원문 그대로의 텍스트로 저장해야 어느 후보 테이블에서든(부분 문자열
    매칭으로) 재사용할 수 있다. source_document_id는 scripts/ragas_eval/datasets/
    build_source_documents.py가 채운 로컬 source_documents.id를 가리킨다."""

    source_document_id: str = Field(min_length=1)
    snippet: str = Field(min_length=1, description="원문에서 그대로 발췌 — 지어내면 안 됨")


class RagasCase(BaseModel):
    """RAG 검색 품질 평가 케이스 1건. ground_truth는 사람이 검증한 정답 요약이고,
    reference_doc_ids는 "이상적으로는 이 문서(들)가 검색돼야 한다"는 참고용 기록—
    RAGAS 지표 계산에는 안 쓰이고(LLM judge가 관련성을 직접 판단), 사람이 결과를
    검토할 때 참고하는 용도. reference_contexts는 chunk_size 후보 비교(runner.py가
    아니라 scripts/ragas_eval 쪽 grid search)에서 쓰는 원문 발췌."""

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    ground_truth: str = Field(min_length=1, description="사람이 검증한 정답 요약")
    reference_doc_ids: list[str] = Field(default_factory=list, description="참고용 — 채점에 안 쓰임")
    reference_contexts: list[ReferenceContext] = Field(
        default_factory=list, description="chunk_size 후보 비교용 원문 발췌(여러 개 가능)"
    )
    status: Literal["draft", "approved", "rejected"] = Field(
        default="draft", description="draft=검수 전, approved=평가에 사용, rejected=제외"
    )
    review_note: str | None = Field(default=None, description="검수 메모(반려 사유 등)")
    question_type: Literal["단순 조회", "조건 조회", "절차 설명", "비교·판단"] | None = Field(
        default=None,
        description=(
            "질문 유형별 채점 분석용. retrieval_scope(단일/복수 근거)는 reference_contexts "
            "개수로 계산 가능해 별도 필드로 안 둠 — difficulty도 기준이 모호해서 의도적으로 제외."
        ),
    )
    created_at: str | None = Field(
        default=None, description="등록 시각(UTC ISO 8601, goldset_admin.append_case가 채움). "
        "이 필드 추가 이전 기존 케이스는 None."
    )
    updated_at: str | None = Field(
        default=None, description="마지막 수정 시각(UTC ISO 8601, goldset_admin이 쓰기 때마다 갱신). "
        "이 필드 추가 이전 기존 케이스는 None."
    )
    dataset_membership: Literal["active", "candidate", "excluded"] | None = Field(
        default=None,
        description=(
            "현재 평가 데이터셋 소속 — status(approved/rejected, 문항 품질 판정)와는 별개 축. "
            "active=현재 실험 세트에 포함(러너가 이걸로 케이스를 고름), "
            "candidate=승인됐지만 아직 실험 세트에 안 넣음(신규 후보), "
            "excluded=문항은 유효하나 지금 평가 목적엔 안 씀. "
            "rejected 케이스는 이 필드를 안 씀(None)."
        ),
    )


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
