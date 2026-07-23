"""RAG 적재 서버 진입점 (화면 없음, API만).

모니터링 서버(또는 사람이 프론트 버튼으로)가 POST /rag/ingest를 호출하면 수집→빌드→
pgvector/OpenSearch 적재 파이프라인을 백그라운드로 실행한다. 적재 대상 DB는
A360-Assistant-Backend와 동일 인스턴스라 여기서 적재한 게 실서비스에 그대로 반영된다.

실행 상태·락·로그 관리는 ingest_jobs가 담당한다(프로세스 재시작/다중 워커에도 살아남는
파일 기반 상태) — 이 모듈은 HTTP 표면만 정의한다.
"""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import ingest_jobs
from .rag import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 기동 시 필수 설정 검증(fail-fast) — RAG_DATABASE_URL이 없으면 여기서 바로 실패시켜,
    # 잘못된 배포가 "정상 기동·헬스 통과"로 보이지 않게 한다(폴백 제거 RPA-262의 짝, Qodo 리뷰 반영).
    # database_dsn()은 URL 존재만 확인(접속하지 않음)하므로 DB 일시 다운으로는 크래시하지 않는다.
    config.database_dsn()
    yield


app = FastAPI(title="A360 RAG Ingest Server", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 RAG Ingest Server가 살아있습니다."}


@app.post("/rag/ingest")
def trigger_rag_ingest(option: int, background_tasks: BackgroundTasks, clean: bool = False) -> dict:
    """RAG 수집 파이프라인 실행 (A360-Assistant-Backend와 같은 DB에 적재).

    옵션 1: JAR 있는 패키지만 action_schema로 적재.
    옵션 2: 옵션 1 + JAR 없는 패키지 리프도 action_candidate로 참고용 적재.
    옵션 3: 옵션 2 + JAR 없는 패키지 리프를 LLM 파싱 에이전트로 action_schema화
            (schema_source=llm_agent, 미검증 신뢰 등급). OPENAI_API_KEY 필요.

    clean=False(기본값): 기존 rag_documents/OpenSearch에 upsert만 한다 — 이번 build에서
    빠진 옛 row는 안 지워진다(스케줄러 등 기존 자동 호출과 동작 호환 유지 위해 기본값
    유지). clean=True: 적재 전 기존 rag_documents/OpenSearch를 전부 지우고 이번 build
    결과로 완전히 새로 채운다(재적재) — A360-Assistant-Backend와 같은 DB를 지우므로
    실행 중 RAG 검색이 잠깐 비거나 불완전할 수 있다.

    이미 실행 중이면 409 — 동시 실행은 ingest_jobs의 락으로 막는다(다중 워커/스케줄러와
    수동 버튼이 겹치는 경우 포함).
    """
    if option not in ingest_jobs.OPTION_SCRIPTS:
        raise HTTPException(status_code=400, detail="option은 1, 2, 3 중 하나여야 합니다")

    state = ingest_jobs.reserve_job(option, clean)
    if state is None:
        raise HTTPException(status_code=409, detail="이미 실행 중입니다 — /rag/ingest/status로 확인하세요")

    background_tasks.add_task(ingest_jobs.run_reserved_job, state)
    return {
        "status": "started",
        "run_id": state["run_id"],
        "option": option,
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
