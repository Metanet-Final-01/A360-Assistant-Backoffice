"""RAG 적재 서버 진입점 (화면 없음, API만).

모니터링 서버(또는 사람이 프론트 버튼으로)가 POST /rag/ingest를 호출하면 수집→빌드→
pgvector/OpenSearch 적재 파이프라인을 백그라운드로 실행한다. 적재 대상 DB는
A360-Assistant-Backend와 동일 인스턴스라 여기서 적재한 게 실서비스에 그대로 반영된다.

실행 상태·락·로그 관리는 ingest_jobs가 담당한다(프로세스 재시작/다중 워커에도 살아남는
파일 기반 상태) — 이 모듈은 HTTP 표면만 정의한다.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import ingest_jobs
from .rag import source_documents as rag_source_documents
from .rag import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 기동 시 필수 설정 검증(fail-fast) — RAG_DATABASE_URL이 없으면 여기서 바로 실패시켜,
    # 잘못된 배포가 "정상 기동·헬스 통과"로 보이지 않게 한다(폴백 제거 RPA-262의 짝, Qodo 리뷰 반영).
    # database_dsn()은 URL 존재만 확인(접속하지 않음)하므로 DB 일시 다운으로는 크래시하지 않는다.
    config.database_dsn()
    yield


app = FastAPI(title="A360 RAG Ingest Server", lifespan=lifespan)


class CreateIngestJobRequest(BaseModel):
    mode: Literal["standard", "extended", "agent_parse"] = "standard"
    clean: bool = False
    requested_by: str = "ops"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 RAG Ingest Server가 살아있습니다."}


@app.get("/rag/ingest/capabilities")
def rag_ingest_capabilities() -> dict:
    return ingest_jobs.capabilities()


@app.post("/rag/ingest/jobs")
def create_rag_ingest_job(req: CreateIngestJobRequest) -> dict:
    try:
        job = ingest_jobs.create_job(req.mode, req.clean, requested_by=req.requested_by.strip() or "ops")
    except ingest_jobs.ConflictError as exc:
        raise HTTPException(409, {"message": "A RAG ingest job is already running.", "job_id": exc.job_id}) from exc
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job_id": job["job_id"], "status": job["status"]}


@app.get("/rag/ingest/jobs")
def list_rag_ingest_jobs(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    return ingest_jobs.list_jobs(limit=limit)


@app.get("/rag/ingest/jobs/{job_id}")
def get_rag_ingest_job(job_id: str) -> dict:
    job = ingest_jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, f"job_id={job_id} not found")
    return job


@app.get("/rag/ingest/jobs/{job_id}/events")
async def rag_ingest_job_events(request: Request, job_id: str) -> StreamingResponse:
    if not ingest_jobs.get_job(job_id):
        raise HTTPException(404, f"job_id={job_id} not found")
    last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id") or "0"
    try:
        cursor = int(last_event_id)
    except ValueError:
        cursor = 0

    async def event_stream():
        nonlocal cursor
        while True:
            if await request.is_disconnected():
                break
            events = ingest_jobs.list_events(job_id, after_id=cursor, limit=100)
            if events:
                for event in events:
                    cursor = event["id"]
                    data = json.dumps(event["data"], ensure_ascii=False)
                    yield f"id: {event['id']}\nevent: {event['event_type']}\ndata: {data}\n\n"
            else:
                yield ": heartbeat\n\n"
            job = ingest_jobs.get_job(job_id)
            if job and job["status"] in ingest_jobs.TERMINAL_STATUSES and not events:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/rag/ingest/jobs/{job_id}/events/poll")
def poll_rag_ingest_job_events(
    job_id: str, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)
) -> dict:
    if not ingest_jobs.get_job(job_id):
        raise HTTPException(404, f"job_id={job_id} not found")
    events = ingest_jobs.list_events(job_id, after_id=after_id, limit=limit)
    return {"events": events, "last_event_id": events[-1]["id"] if events else after_id}


@app.post("/rag/ingest/jobs/{job_id}/cancel")
def cancel_rag_ingest_job(job_id: str) -> dict:
    try:
        return ingest_jobs.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"job_id={job_id} not found") from exc


@app.get("/rag/ingest/jobs/{job_id}/logs")
def get_rag_ingest_job_log(job_id: str, tail: int = Query(400, ge=1, le=5000)) -> PlainTextResponse:
    if not ingest_jobs.get_job(job_id):
        raise HTTPException(404, f"job_id={job_id} not found")
    return PlainTextResponse(ingest_jobs.read_log(job_id, tail=tail))


@app.get("/rag/ingest/jobs/{job_id}/logs/download")
def download_rag_ingest_job_log(job_id: str):
    if not ingest_jobs.get_job(job_id):
        raise HTTPException(404, f"job_id={job_id} not found")
    path = ingest_jobs.log_file(job_id)
    if not path.exists():
        return PlainTextResponse("", headers={"Content-Disposition": f'attachment; filename="{job_id}.log"'})
    return FileResponse(path, media_type="text/plain", filename=f"{job_id}.log")


@app.post("/rag/ingest")
def trigger_rag_ingest(option: int, clean: bool = False) -> dict:
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
    if option not in ingest_jobs.OPTION_TO_MODE:
        raise HTTPException(status_code=400, detail="option은 1, 2, 3 중 하나여야 합니다")
    mode = ingest_jobs.OPTION_TO_MODE[option]
    try:
        job = ingest_jobs.create_job(mode, clean, requested_by="legacy-api")
    except ingest_jobs.ConflictError as exc:
        raise HTTPException(409, {"message": "이미 실행 중입니다.", "job_id": exc.job_id}) from exc
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "started", "option": option, "clean": clean, "job_id": job["job_id"]}


@app.get("/rag/ingest/status")
def rag_ingest_status() -> dict:
    jobs = ingest_jobs.list_jobs(limit=1)
    latest = ingest_jobs.active_job() or (jobs[0] if jobs else None)
    if not latest:
        return {"running": False, "option": None, "clean": None, "returncode": None, "log": ""}
    return {
        "running": latest["status"] in ingest_jobs.RUNNING_STATUSES,
        "option": ingest_jobs.MODE_TO_OPTION.get(latest["mode"]),
        "clean": latest["clean"],
        "returncode": latest["exit_code"],
        "log": ingest_jobs.read_log(latest["job_id"], tail=400),
        "job_id": latest["job_id"],
        "status": latest["status"],
        "current_stage": latest.get("current_stage"),
    }


@app.get("/rag/source-documents/capabilities")
def rag_source_documents_capabilities() -> dict:
    try:
        return rag_source_documents.capabilities()
    except Exception as exc:
        raise HTTPException(500, f"source document pool unavailable: {exc}") from exc


@app.get("/rag/source-documents")
def rag_source_documents_search(
    q: str = "",
    source_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    schema_source: str | None = None,
) -> list[dict]:
    try:
        return rag_source_documents.search(q, source_type=source_type, limit=limit, schema_source=schema_source)
    except Exception as exc:
        raise HTTPException(500, f"source document pool unavailable: {exc}") from exc


@app.get("/rag/source-documents/random")
def rag_source_documents_random(
    source_type: str | None = None,
    limit: int = Query(5, ge=1, le=50),
    exclude_ids: str | None = None,
    min_content_length: int = Query(0, ge=0),
    schema_source: str | None = None,
) -> list[dict]:
    exclude = [item for item in (exclude_ids or "").split(",") if item]
    try:
        return rag_source_documents.random_sample(
            source_type=source_type,
            limit=limit,
            exclude_ids=exclude,
            min_content_length=min_content_length,
            schema_source=schema_source,
        )
    except Exception as exc:
        raise HTTPException(500, f"source document pool unavailable: {exc}") from exc


@app.get("/rag/source-documents/{doc_id}")
def rag_source_document_detail(doc_id: str) -> dict:
    try:
        doc = rag_source_documents.get_by_id(doc_id)
    except Exception as exc:
        raise HTTPException(500, f"source document pool unavailable: {exc}") from exc
    if not doc:
        raise HTTPException(404, f"doc_id={doc_id} not found")
    return doc


@app.get("/rag/schema-sources")
def rag_schema_sources(parent_ids: str) -> dict:
    ids = [pid for pid in parent_ids.split(",") if pid]
    if not ids:
        return {"schema_sources": {}}
    from app.rag.config import database_dsn
    import psycopg

    with psycopg.connect(database_dsn(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select parent_id, metadata->>'schema_source'
                from rag_documents
                where parent_id = any(%s::text[])
                  and source_type in ('action_schema', 'package_overview')
                group by parent_id, metadata->>'schema_source'
                """,
                (ids,),
            )
            return {"schema_sources": {row[0]: row[1] for row in cur.fetchall() if row[1]}}


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
