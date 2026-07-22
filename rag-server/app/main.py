"""RAG 적재 서버 진입점 (화면 없음, API만).

모니터링 서버(ops-server)가 주기적으로, 또는 사람이 프론트 버튼으로 POST /rag/ingest를
호출하면 크롤→등기→빌드(v2)→검증→pgvector/OpenSearch 적재 파이프라인을 백그라운드로
실행한다. 적재 대상 DB는 A360-Assistant-Backend와 동일 인스턴스라 여기서 적재한 게
실서비스에 그대로 반영된다.

파이프라인은 khub 웹크롤 정본 v2 하나뿐이다(팀 결정: 웹크롤 전용). 과거 JAR 기반 옵션 1~3은
제거됐고, `option` 쿼리 파라미터는 하위호환용으로 받되 무시한다(ops가 옵션 선택을 걷어내는 중).
실행 상태·락·로그는 ingest_jobs가 담당한다(프로세스 재시작/다중 워커에도 살아남는 파일 기반 상태).
"""

from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import ingest_jobs

app = FastAPI(title="A360 RAG Ingest Server")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 RAG Ingest Server가 살아있습니다."}


@app.post("/rag/ingest")
def trigger_rag_ingest(
    background_tasks: BackgroundTasks, clean: bool = False, option: int | None = None
) -> dict:
    """RAG 수집 파이프라인 실행 — khub 웹크롤 v2 정본 (A360-Assistant-Backend와 같은 DB에 적재).

    crawl-khub → registry → build-v2(트리우선 등기) → validate(품질 게이트) → ingest. OPENAI_API_KEY 필요.

    option: 하위호환용 — 받되 무시한다(파이프라인은 v2 하나뿐). ops가 옵션 선택을 제거하면 함께 사라질 예정.
    clean=False(기본): 기존 rag_documents/OpenSearch에 upsert만 — 이번 build에 빠진 옛 row는 유지한다
            (스케줄러 등 기존 자동 호출과 동작 호환).
    clean=True: 적재 전 기존 rag_documents/OpenSearch를 전부 지우고 이번 build로 완전 재적재 — 같은 DB라
            실행 중 RAG 검색이 잠깐 비거나 불완전할 수 있다.
    이미 실행 중이면 409 — 동시 실행은 ingest_jobs의 락으로 막는다(다중 워커/스케줄러와 수동 버튼 겹침 포함).
    """
    state = ingest_jobs.reserve_job(option, clean)
    if state is None:
        raise HTTPException(status_code=409, detail="이미 실행 중입니다 — /rag/ingest/status로 확인하세요")

    background_tasks.add_task(ingest_jobs.run_reserved_job, state)
    return {
        "status": "started",
        "run_id": state["run_id"],
        "pipeline": "v2",
        "clean": clean,
        "log_path": state["log_path"],
    }


@app.get("/rag/ingest/status")
def rag_ingest_status() -> dict:
    return ingest_jobs.status()


class RagasValidationAttemptRequest(BaseModel):
    """근거 검증 시도 1건. doc_id는 필수(빈 문자열 거부) — outcome은 success/failure만
    받는다(CodeRabbit #42 지적: 전에는 dict를 그대로 받아 빈 doc_id·임의 outcome도
    기록됐음)."""

    doc_id: str = Field(min_length=1)
    doc_title: str | None = None
    question: str | None = None
    outcome: Literal["success", "failure"] = "failure"
    failed_snippets: str | None = None


@app.post("/observability/ragas-validation-attempts")
def record_ragas_validation_attempt(req: RagasValidationAttemptRequest) -> dict:
    """RAGAS 골드셋 작성 화면의 근거 검증 시도를 관측 DB에 기록한다 — 통계용, 실패해도
    골드셋 저장 자체는 막지 않는다(ops-server는 이 엔드포인트로만 기록하고 관측 DB에
    직접 쓰지 않는다, 관측 DB 쓰기는 rag-server 적재 경로에만 허용하는 정책)."""
    from app.core.llm import record_ragas_validation_attempt as _record

    _record(
        doc_id=req.doc_id,
        doc_title=req.doc_title,
        question=req.question,
        outcome=req.outcome,
        failed_snippets=req.failed_snippets,
    )
    return {"ok": True}
