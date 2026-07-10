import subprocess
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException

app = FastAPI(title="A360 Assistant Ops Backend")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPTION_SCRIPTS = {
    1: _REPO_ROOT / "app" / "rag" / "scripts" / "run_option1_jar_only.py",
    2: _REPO_ROOT / "app" / "rag" / "scripts" / "run_option2_with_naive_actions.py",
}

# 파이프라인은 실행에 몇 분~몇십 분이 걸릴 수 있어 백그라운드로 돌린다 — 프로세스 재시작하면
# 사라지는 인메모리 상태로 충분하다(가벼운 운영 도구 용도, 별도 job 큐 불필요).
_run_state: dict = {"running": False, "option": None, "returncode": None, "log": ""}


def _run_pipeline(option: int) -> None:
    _run_state.update(running=True, option=option, returncode=None, log="")
    proc = subprocess.run(
        [sys.executable, str(_OPTION_SCRIPTS[option])],
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
    return {"message": "A360 Assistant Ops backend가 살아있습니다."}


@app.post("/rag/ingest")
def trigger_rag_ingest(option: int, background_tasks: BackgroundTasks) -> dict:
    """RAG 수집 파이프라인 실행 (A360-Assistant-Backend와 같은 DB에 적재).

    옵션 1: JAR 있는 패키지만 action_schema로 적재.
    옵션 2: 옵션 1 + JAR 없는 패키지 리프도 action_candidate로 참고용 적재.
    """
    if option not in _OPTION_SCRIPTS:
        raise HTTPException(400, "option은 1 또는 2여야 합니다")
    if _run_state["running"]:
        raise HTTPException(409, "이미 실행 중입니다 — /rag/ingest/status로 확인하세요")
    background_tasks.add_task(_run_pipeline, option)
    return {"status": "started", "option": option}


@app.get("/rag/ingest/status")
def rag_ingest_status() -> dict:
    return _run_state
