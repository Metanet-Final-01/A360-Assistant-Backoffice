"""RAG 적재 서버 진입점 (화면 없음, API만).

모니터링 서버(또는 사람이 프론트 버튼으로)가 POST /rag/ingest를 호출하면 수집→빌드→
pgvector/OpenSearch 적재 파이프라인을 백그라운드로 실행한다. 적재 대상 DB는
A360-Assistant-Backend와 동일 인스턴스라 여기서 적재한 게 실서비스에 그대로 반영된다.
"""

import subprocess
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException

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
