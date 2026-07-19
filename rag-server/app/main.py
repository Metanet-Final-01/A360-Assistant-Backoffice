"""RAG 적재 서버 진입점 (화면 없음, API만).

모니터링 서버(또는 사람이 프론트 버튼으로)가 POST /rag/ingest를 호출하면 수집→빌드→
pgvector/OpenSearch 적재 파이프라인을 백그라운드로 실행한다. 적재 대상 DB는
A360-Assistant-Backend와 동일 인스턴스라 여기서 적재한 게 실서비스에 그대로 반영된다.
"""

import subprocess
import sys
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="A360 RAG Ingest Server")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPTION_SCRIPTS = {
    1: _REPO_ROOT / "app" / "rag" / "scripts" / "run_option1_jar_only.py",
    2: _REPO_ROOT / "app" / "rag" / "scripts" / "run_option2_with_naive_actions.py",
    3: _REPO_ROOT / "app" / "rag" / "scripts" / "run_option3_with_doc_agent.py",
}

# 파이프라인은 실행에 몇 분~몇십 분이 걸릴 수 있어 백그라운드로 돌린다 — 프로세스 재시작하면
# 사라지는 인메모리 상태로 충분하다(가벼운 운영 도구 용도, 별도 job 큐 불필요).
_run_state: dict = {"running": False, "option": None, "clean": None, "returncode": None, "log": ""}


def _run_pipeline(option: int, clean: bool) -> None:
    _run_state.update(running=True, option=option, clean=clean, returncode=None, log="")
    args = [sys.executable, str(_OPTION_SCRIPTS[option])]
    if clean:
        args.append("--clean")
    proc = subprocess.run(
        args,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _run_state.update(running=False, returncode=proc.returncode, log=proc.stdout + proc.stderr)


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
    """
    if option not in _OPTION_SCRIPTS:
        raise HTTPException(400, "option은 1, 2, 3 중 하나여야 합니다")
    if _run_state["running"]:
        raise HTTPException(409, "이미 실행 중입니다 — /rag/ingest/status로 확인하세요")
    background_tasks.add_task(_run_pipeline, option, clean)
    return {"status": "started", "option": option, "clean": clean}


@app.get("/rag/ingest/status")
def rag_ingest_status() -> dict:
    return _run_state


class RagasValidationAttemptRequest(BaseModel):
    """근거 검증 시도 1건. doc_id는 필수(빈 문자열 거부) — outcome은 success/failure만
    받는다(CodeRabbit #42 지적: 전에는 dict를 그대로 받아 빈 doc_id·임의 outcome도
    기록됐음)."""

    doc_id: str = Field(min_length=1)
    doc_title: str | None = None
    question: str | None = None
    outcome: Literal["success", "failure"] = "failure"
    failed_snippets: str | None = None


@app.get("/rag/schema-sources")
def rag_schema_sources(parent_ids: str) -> dict:
    """parent_id 목록(콤마 구분)에 대응하는 schema_source(jar/llm_agent)를 조회한다.

    ops-server의 골드셋 작성 화면이 로컬 source_documents 테이블(schema_source 컬럼
    없음)에 없는 이 구분을 문서 브라우저에 표시/필터링하기 위해 호출한다 — ops-server는
    RAG DB에 직접 연결하지 않고 이 API를 거친다(관측/RAG DB 직접 접근은 rag-server
    전용이라는 정책, validation_log.py와 동일).

    반드시 parent_id로 조회한다 — rag_documents는 청크 단위로 저장되어 있어서(운영
    chunk_size=1200) id는 청크마다 다르고, 원본 문서를 가리키는 건 parent_id뿐이다."""
    ids = [pid for pid in parent_ids.split(",") if pid]
    if not ids:
        return {"schema_sources": {}}
    from app.rag import config
    import psycopg

    with psycopg.connect(config.database_dsn(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select distinct parent_id, metadata->>'schema_source' "
                "from rag_documents where parent_id = any(%s::text[])",
                (ids,),
            )
            return {"schema_sources": {row[0]: (row[1] or "unknown") for row in cur.fetchall()}}


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
