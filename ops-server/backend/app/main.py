"""모니터링 서버 백엔드 진입점 (FastAPI).

- /observability/*: A360-Assistant-Backend의 감사 로그·LLM 사용량·RAG 요청 로그 수집/조회.
- /eval/*: 평가 데이터셋·결과 로그·pm4py/WorFBench 변환·A/B 비교·xlsx 내보내기.

RAG 적재 트리거(/rag/ingest)는 여기 없다 — 별도 rag-server가 담당하고, 프론트의 '적재'
버튼과 (향후) app/scheduler가 rag-server로 직접 요청을 보낸다.
"""

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.eval.format_guide import build_format_guide
from app.eval.format_schemas import validate_format
from app.eval.dataset_schema import EvaluationDataset
from app.eval.dataset_store import load_datasets, save_dataset
from app.eval import executor
from app.eval.log_schema import EvalRunRecord
from app.eval.log_store import append_run, get_run, load_runs
from app.eval.metrics import metrics_from_raw
from app.eval.workflow.adapters import MissingCatalogError, to_pm4py_predicted_actions, to_worfbench_pred_traj
from app.eval.workflow.recommendation import Recommendation
from app.eval.xlsx_report import build_comparison_xlsx
from app.observability import backend_client, collector, log_store as obs_log_store

app = FastAPI(title="A360 Assistant Monitoring Server")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 Assistant Monitoring Server가 살아있습니다."}


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
    (app/eval/format_examples/)을 그대로 보여준다."""
    return build_format_guide()


class ConvertRequest(BaseModel):
    recommendation: Recommendation
    source_bot: str
    task_description: str | None = None


class ExecuteEvaluationRequest(BaseModel):
    prediction_label: str
    evaluation_id: str
    dataset_id: str
    dataset_version: str
    agent_label: str
    commit_sha: str | None = None


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


@app.get("/eval/execution/options")
def evaluation_execution_options() -> dict:
    return {"prediction_labels": executor.available_prediction_labels()}


@app.post("/eval/execution")
def start_evaluation(req: ExecuteEvaluationRequest, background_tasks: BackgroundTasks) -> dict:
    if executor.state["running"]:
        raise HTTPException(409, "이미 평가가 실행 중입니다")
    datasets = [
        item for item in load_datasets()
        if item.dataset_id == req.dataset_id and item.version == req.dataset_version
    ]
    if not datasets:
        raise HTTPException(404, "등록된 데이터셋 버전을 찾지 못했습니다")
    try:
        executor.validate_prediction_label(req.prediction_label)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not req.evaluation_id.strip() or not req.agent_label.strip():
        raise HTTPException(400, "evaluation_id와 agent_label은 필수입니다")
    dataset = datasets[0]
    background_tasks.add_task(
        executor.execute,
        req.prediction_label,
        req.evaluation_id.strip(),
        dataset.dataset_id,
        dataset.version,
        dataset.case_ids,
        req.agent_label.strip(),
        req.commit_sha.strip() if req.commit_sha else None,
    )
    return {"status": "started", "evaluation_id": req.evaluation_id.strip()}


@app.get("/eval/execution/status")
def evaluation_execution_status() -> dict:
    return executor.state


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
