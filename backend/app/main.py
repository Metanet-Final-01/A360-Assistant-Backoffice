import subprocess
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.eval.format_guide import build_format_guide
from app.eval.format_schemas import validate_format
from app.eval.dataset_schema import EvaluationDataset
from app.eval.dataset_store import load_datasets, save_dataset
from app.eval.log_schema import EvalRunRecord
from app.eval.log_store import append_run, get_run, load_runs
from app.eval.metrics import metrics_from_raw
from app.eval.workflow.adapters import MissingCatalogError, to_pm4py_predicted_actions, to_worfbench_pred_traj
from app.eval.workflow.recommendation import Recommendation
from app.eval.xlsx_report import build_comparison_xlsx
from app.observability import backend_client, collector, log_store as obs_log_store

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


@app.post("/eval/runs")
def record_eval_run(record: EvalRunRecord) -> EvalRunRecord:
    """평가 결과 한 건을 로그에 기록한다. 채점 방법(rule_check/pm4py/수작업 등)은
    가리지 않는다 — record.source에 어떤 방법인지만 남기면 된다.

    단, source가 pm4py/worfbench처럼 이미 알려진 채점 엔진이면 raw를 그 채점 엔진의
    출력 형식(format_schemas.py)으로 엄격 검증한다 — 잘못된 형태로 기록되어 나중에
    비교할 때 조용히 깨지는 걸 막기 위함."""
    errors = validate_format(record.source, record.raw)
    if errors:
        raise HTTPException(400, {"message": f"source={record.source} 형식 검증 실패", "errors": errors})
    if not record.metrics:
        derived = metrics_from_raw(record.source, record.raw)
        if derived:
            score = record.score
            if score is None:
                preferred = "pm4py_fitness" if record.source == "pm4py" else "worfbench_f1_score"
                score = next((metric.value for metric in derived if metric.name == preferred), None)
            record = record.model_copy(update={"metrics": derived, "score": score})
    return append_run(record)


@app.get("/eval/datasets")
def list_eval_datasets() -> list[EvaluationDataset]:
    return load_datasets()


@app.post("/eval/datasets")
def create_eval_dataset(dataset: EvaluationDataset) -> EvaluationDataset:
    try:
        return save_dataset(dataset)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/eval/runs")
def list_eval_runs(
    case_id: str | None = None, source: str | None = None, agent_label: str | None = None
) -> list[EvalRunRecord]:
    return load_runs(case_id=case_id, source=source, agent_label=agent_label)


@app.get("/eval/runs/{run_id}")
def get_eval_run(run_id: str) -> EvalRunRecord:
    record = get_run(run_id)
    if record is None:
        raise HTTPException(404, f"run_id={run_id} 없음")
    return record


@app.get("/eval/format-guide")
def eval_format_guide() -> dict:
    """pm4py/WorFBench가 요구하는 입력·출력 형식 안내 + 예시 데이터셋
    (backend/app/eval/format_examples/)을 그대로 보여준다."""
    return build_format_guide()


class ConvertRequest(BaseModel):
    recommendation: Recommendation
    source_bot: str
    task_description: str | None = None


@app.post("/eval/convert/pm4py")
def convert_to_pm4py(req: ConvertRequest) -> dict:
    """agent가 만든 추천안(Recommendation)을 pm4py 채점 입력 형식으로 변환한다."""
    return to_pm4py_predicted_actions(req.recommendation, req.source_bot)


@app.post("/eval/convert/worfbench")
def convert_to_worfbench(req: ConvertRequest) -> dict:
    """agent가 만든 추천안(Recommendation)을 WorFBench 채점 입력 형식으로 변환한다."""
    if not req.task_description:
        raise HTTPException(400, "worfbench 변환에는 task_description이 필요합니다")
    try:
        return to_worfbench_pred_traj(
            req.recommendation,
            source_bot=req.source_bot,
            task_description=req.task_description,
            source_id=f"a360:{req.source_bot}",
        )
    except (MissingCatalogError, FileNotFoundError) as e:
        raise HTTPException(409, str(e)) from e


def _run_collect(fn, *args, **kwargs) -> dict:
    """backend_client 예외를 사람이 읽을 수 있는 HTTPException으로 변환."""
    try:
        return fn(*args, **kwargs)
    except backend_client.BackendAuthError as e:
        raise HTTPException(403, str(e)) from e
    except backend_client.BackendUnavailableError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/observability/audit-logs/collect")
def collect_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/audit-logs를 호출해 가져온 뒤 로컬에 저장한다."""
    return _run_collect(collector.collect_audit_logs, limit=limit, method=method, status_code=status_code, user_id=user_id)


@app.get("/observability/audit-logs")
def get_audit_logs(limit: int = 200, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> list:
    return obs_log_store.load_audit_logs(limit=limit, method=method, status_code=status_code, user_id=user_id)


@app.post("/observability/llm-usage/collect")
def collect_llm_usage(days: int = 30, group_by: str = "component") -> dict:
    """A360-Assistant-Backend의 GET /api/admin/llm-usage/stats를 호출해 스냅샷으로 저장한다."""
    return _run_collect(collector.collect_llm_usage, days=days, group_by=group_by)


@app.get("/observability/llm-usage/snapshots")
def get_llm_usage_snapshots(group_by: str | None = None, limit: int = 50) -> list:
    return obs_log_store.load_llm_usage_snapshots(group_by=group_by, limit=limit)


@app.post("/observability/rag-logs/collect")
def collect_rag_logs(limit: int = 100) -> dict:
    """A360-Assistant-Backend의 GET /api/rag/logs/recent를 호출해 http_request 이벤트만
    가져와 저장한다(파이프라인 단계별 이벤트는 텍스트 미리보기가 섞여 있어 기본 제외)."""
    return _run_collect(collector.collect_rag_logs, limit=limit)


@app.get("/observability/rag-logs")
def get_rag_logs(event: str | None = None, path_contains: str | None = None, limit: int = 200) -> list:
    return obs_log_store.load_rag_logs(event=event, path_contains=path_contains, limit=limit)


@app.get("/observability/status")
def observability_status() -> dict:
    return collector.status()


@app.get("/eval/export/comparison-xlsx")
def export_comparison_xlsx(label_a: str, label_b: str) -> Response:
    """AB_comparison_report.xlsx(a360-eval-sandbox)와 같은 스타일로 두 버전(agent_label)
    비교를 엑셀로 내보낸다."""
    runs_a = load_runs(agent_label=label_a)
    runs_b = load_runs(agent_label=label_b)
    if not runs_a or not runs_b:
        raise HTTPException(404, f"agent_label={label_a!r} 또는 {label_b!r}에 해당하는 로그가 없습니다")
    content = build_comparison_xlsx(runs_a, runs_b, label_a, label_b)
    filename = f"comparison_{label_a}_vs_{label_b}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
