"""모니터링 서버 백엔드 진입점 (FastAPI).

- /observability/*: A360-Assistant-Backend의 감사 로그·LLM 사용량·RAG 요청 로그 수집/조회.
- /assurance/*: Backend의 AI 출력 검증 판정 기록을 저장 없이 읽기 전용 중계.
- /eval/*: 평가 데이터셋·결과 로그·pm4py/WorFBench 변환·A/B 비교·xlsx 내보내기.

RAG 적재 트리거(/rag/ingest)는 여기 없다 — 별도 rag-server가 담당하고, 프론트의 '적재'
버튼과 (향후) app/scheduler가 rag-server로 직접 요청을 보낸다.
"""

import asyncio
import json
import os
import re
import uuid
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.eval.format_guide import build_format_guide
from app.eval.format_schemas import validate_format
from app.eval.dataset_schema import EvaluationDataset
from app.eval.dataset_store import load_datasets, save_dataset
from app.eval import executor
from app.eval import goldset_admin
from app.eval.workflow_eval import runner as workflow_runner
from app.eval.workflow_eval.schema import WorkflowCase
from app.eval.ragas_eval import runner as ragas_runner
from app.eval.ragas_eval import pass_k as ragas_pass_k
from app.eval.ragas_eval import source_documents as ragas_source_documents
from app.eval.ragas_eval import validation_log as ragas_validation_log
from app.eval.ragas_eval.schema import RagasCase
from app.eval.bfcl_eval import runner as bfcl_runner
from app.eval.bfcl_eval import pass_k as bfcl_pass_k
from app.eval.bfcl_eval.schema import BFCLCase
from app.eval.log_schema import EvalRunRecord
from app.eval.log_store import append_run, get_run, load_runs
from app.eval.metrics import metrics_from_raw
from app.eval.workflow.adapters import MissingCatalogError, to_pm4py_predicted_actions, to_worfbench_pred_traj
from app.eval.workflow.recommendation import Recommendation
from app.eval.xlsx_report import build_comparison_xlsx
from app.loadtest import executor as loadtest_executor
from app.loadtest.schema import LoadTestRunRecord
from app.loadtest.store import append_run as append_loadtest_run, load_runs as load_loadtest_runs
from app.observability import backend_client, collector, log_store as obs_log_store, obs_db
from app.settings import backend_settings
from app.scheduler import scheduler as rag_scheduler
from app.scheduler.schema import RagIngestScheduleRequest, ScheduleApplyResult

app = FastAPI(title="A360 Assistant Monitoring Server")

# 임의의 웹 페이지가 PATCH/POST 같은 변경 API를 호출하지 못하도록, 실제 Streamlit
# 프론트 origin만 허용한다(CodeRabbit #42 지적). 로컬 개발 기본값은 Streamlit
# 기본 포트(8501) — 배포 환경은 OPS_FRONTEND_ORIGINS(콤마 구분)로 재정의한다.
_frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "OPS_FRONTEND_ORIGINS", "http://127.0.0.1:8501,http://localhost:8501"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 Assistant Monitoring Server가 살아있습니다."}


@app.post("/schedules/rag-ingest", response_model=ScheduleApplyResult)
def upsert_rag_ingest_schedule(
    req: RagIngestScheduleRequest,
    provider: str | None = None,
    dry_run: bool = False,
) -> ScheduleApplyResult:
    try:
        return rag_scheduler.upsert_schedule(req, provider_name=provider, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc


@app.post("/schedules/rag-ingest/{schedule_id}/pause", response_model=ScheduleApplyResult)
def pause_rag_ingest_schedule(schedule_id: str, provider: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    try:
        return rag_scheduler.pause_schedule(schedule_id, provider_name=provider, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc


@app.post("/schedules/rag-ingest/{schedule_id}/resume", response_model=ScheduleApplyResult)
def resume_rag_ingest_schedule(schedule_id: str, provider: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    try:
        return rag_scheduler.resume_schedule(schedule_id, provider_name=provider, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc


@app.delete("/schedules/rag-ingest/{schedule_id}", response_model=ScheduleApplyResult)
def delete_rag_ingest_schedule(schedule_id: str, provider: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    try:
        return rag_scheduler.delete_schedule(schedule_id, provider_name=provider, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc


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


class ExecuteWorkflowRequest(BaseModel):
    agent_label: str = "workflow-live"

    @field_validator("agent_label")
    @classmethod
    def validate_agent_label(cls, value: str) -> str:
        label = value.strip()
        executor.validate_prediction_label(label)
        return label


@app.get("/eval/workflow/cases")
def workflow_cases() -> list:
    """골드셋 케이스 목록(채점 실행 전 미리보기용) — 실제 커뮤니티 봇 17개."""
    try:
        return workflow_runner.load_cases()
    except workflow_runner.WorkflowGoldsetError as e:
        raise HTTPException(500, str(e)) from e


@app.post("/eval/workflow/cases")
def add_workflow_case(case: dict) -> dict:
    try:
        return goldset_admin.append_case(workflow_runner._GOLDSET_PATH, WorkflowCase, case, "id").model_dump()
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/eval/workflow/cases/upload")
async def upload_workflow_cases(file: UploadFile = File(...)) -> dict:
    try:
        count = goldset_admin.replace_from_upload(workflow_runner._GOLDSET_PATH, WorkflowCase, await file.read())
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e
    return {"saved": count}


@app.delete("/eval/workflow/cases/{case_id}")
def delete_workflow_case(case_id: str) -> dict:
    deleted = goldset_admin.delete_case(workflow_runner._GOLDSET_PATH, "id", case_id)
    if not deleted:
        raise HTTPException(404, f"id={case_id!r} 케이스를 찾을 수 없습니다")
    return {"deleted": True}


@app.get("/eval/workflow/input-dataset")
def workflow_input_dataset() -> dict:
    """Workflow 입력 데이터셋 — source_bot별 상세 업무정의서 원문
    (detailed_task_descriptions.json, RPA-135에서 라이브 러너가 우선 사용하도록
    맞춘 바로 그 파일)."""
    return goldset_admin.read_text_map(workflow_runner._DETAILED_TASKS_PATH)


class WorkflowInputCase(BaseModel):
    source_bot: str = Field(min_length=1)
    text: str = Field(min_length=1)


@app.post("/eval/workflow/input-dataset")
def upsert_workflow_input(item: WorkflowInputCase) -> dict:
    goldset_admin.upsert_text(workflow_runner._DETAILED_TASKS_PATH, item.source_bot.strip(), item.text)
    return {"saved": 1}


@app.post("/eval/workflow/input-dataset/upload")
async def upload_workflow_input_dataset(file: UploadFile = File(...)) -> dict:
    try:
        count = goldset_admin.replace_text_map_from_upload(workflow_runner._DETAILED_TASKS_PATH, await file.read())
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e
    return {"saved": count}


@app.delete("/eval/workflow/input-dataset/{source_bot}")
def delete_workflow_input(source_bot: str) -> dict:
    deleted = goldset_admin.delete_text_key(workflow_runner._DETAILED_TASKS_PATH, source_bot)
    if not deleted:
        raise HTTPException(404, f"source_bot={source_bot!r}을 찾을 수 없습니다")
    return {"deleted": True}


@app.post("/eval/workflow/execution")
def start_workflow_evaluation(req: ExecuteWorkflowRequest, background_tasks: BackgroundTasks) -> dict:
    if not workflow_runner.reserve():
        raise HTTPException(409, "이미 Workflow 평가가 실행 중입니다")
    background_tasks.add_task(workflow_runner.execute_and_save, req.agent_label.strip())
    return {"status": "started"}


@app.get("/eval/workflow/execution/status")
def workflow_evaluation_status() -> dict:
    return workflow_runner.state


class ExecuteRagasRequest(BaseModel):
    agent_label: str = "rag-default"
    judge_model: str = "gpt-4o-mini"


@app.get("/eval/ragas/cases")
def ragas_cases() -> list:
    """골드셋 케이스 목록(채점 실행 전 미리보기용)."""
    try:
        return [case.model_dump() for case in ragas_runner.load_all_cases()]
    except ragas_runner.RagasGoldsetError as e:
        raise HTTPException(500, str(e)) from e


@app.post("/eval/ragas/cases")
def add_ragas_case(case: dict) -> dict:
    try:
        return goldset_admin.append_case(ragas_runner._CASES_PATH, RagasCase, case, "case_id").model_dump()
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/eval/ragas/cases/{case_id}")
def patch_ragas_case(case_id: str, patch: dict) -> dict:
    """부분 수정 — 승인/반려(status) 처리용. 예: {"status": "approved"} 또는
    {"status": "rejected", "review_note": "..."}."""
    try:
        return goldset_admin.update_case(
            ragas_runner._CASES_PATH, RagasCase, "case_id", case_id, patch
        ).model_dump()
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/eval/ragas/cases/{case_id}")
def delete_ragas_case(case_id: str) -> dict:
    deleted = goldset_admin.delete_case(ragas_runner._CASES_PATH, "case_id", case_id)
    if not deleted:
        raise HTTPException(404, f"case_id={case_id!r} 케이스를 찾을 수 없습니다")
    return {"deleted": True}


@app.post("/eval/ragas/cases/upload")
async def upload_ragas_cases(file: UploadFile = File(...)) -> dict:
    try:
        count = goldset_admin.replace_from_upload(ragas_runner._CASES_PATH, RagasCase, await file.read())
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e
    return {"saved": count}


class RagasValidationLogRequest(BaseModel):
    """doc_id는 필수(빈 문자열 거부), outcome은 success/failure만 허용한다 — 예전엔
    임의 dict를 그대로 받아 빈 doc_id·임의 outcome도 성공 응답과 함께 기록됐음
    (CodeRabbit #42 지적)."""

    doc_id: str = Field(min_length=1)
    doc_title: str | None = None
    question: str | None = None
    outcome: Literal["success", "failure"] = "failure"
    failed_snippets: str | None = None


@app.post("/eval/ragas/validation-log")
def post_ragas_validation_log(req: RagasValidationLogRequest) -> dict:
    """근거 검증 시도 1건 기록(성공/실패 둘 다) — 통계용, 실패해도 골드셋 저장을 막지 않는다."""
    ragas_validation_log.record_attempt(
        doc_id=req.doc_id,
        doc_title=req.doc_title,
        question=req.question,
        outcome=req.outcome,
        failed_snippets=req.failed_snippets,
    )
    return {"ok": True}


@app.get("/eval/ragas/source-documents")
def ragas_source_documents_search(q: str = "", source_type: str | None = None) -> list[dict]:
    """골드셋 작성 화면의 문서 브라우저 — 로컬 source_documents 테이블 조회
    (scripts/ragas_eval/datasets/build_source_documents.py로 미리 채워둬야 함)."""
    try:
        return ragas_source_documents.search(query=q, source_type=source_type)
    except ragas_source_documents.SourceDocumentsUnavailableError as e:
        raise HTTPException(503, str(e)) from e


@app.get("/eval/ragas/source-documents/random")
def ragas_source_documents_random(
    source_type: str | None = None,
    limit: int = Query(default=5, ge=1, le=100),
    exclude_used: bool = True,
    min_content_length: int = Query(default=0, ge=0),
) -> list[dict]:
    """골드셋 작성용 랜덤 문서 추출. exclude_used=True면 이미 골드셋에 근거로 쓰인
    문서(reference_doc_ids + reference_contexts의 source_document_id)는 제외한다 —
    다만 한 문서에서 여러 질문을 뽑을 수도 있으니 완전 배제가 항상 맞는 건 아니다,
    필요하면 exclude_used=false로 재추출."""
    exclude_ids: list[str] | None = None
    if exclude_used:
        try:
            cases = ragas_runner.load_all_cases()
        except ragas_runner.RagasGoldsetError:
            cases = []
        used: set[str] = set()
        for c in cases:
            used.update(c.reference_doc_ids)
            used.update(rc.source_document_id for rc in c.reference_contexts)
        exclude_ids = sorted(used) or None
    try:
        return ragas_source_documents.random_sample(
            source_type=source_type, limit=limit, exclude_ids=exclude_ids,
            min_content_length=min_content_length,
        )
    except ragas_source_documents.SourceDocumentsUnavailableError as e:
        raise HTTPException(503, str(e)) from e


@app.get("/eval/ragas/source-documents/{doc_id}")
def ragas_source_document_detail(doc_id: str) -> dict:
    try:
        doc = ragas_source_documents.get_by_id(doc_id)
    except ragas_source_documents.SourceDocumentsUnavailableError as e:
        raise HTTPException(503, str(e)) from e
    if doc is None:
        raise HTTPException(404, f"id={doc_id!r} 문서를 찾을 수 없습니다")
    return doc


@app.post("/eval/ragas/execution")
def start_ragas_evaluation(req: ExecuteRagasRequest, background_tasks: BackgroundTasks) -> dict:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(400, "OPENAI_API_KEY가 설정되지 않았습니다 (ops-server/backend/.env)")
    # reserve()가 "실행 중 아님 확인 + running=True로 표시"를 한 호출로 원자적으로
    # 처리한다 — 이전처럼 여기서 상태만 확인하고 실제 표시는 백그라운드 태스크 시작
    # 후에 하면, 그 사이(add_task 큐잉~태스크 실제 시작) 동시 요청이 둘 다 통과해
    # 중복 실행될 수 있었다(CodeRabbit 지적).
    if not ragas_runner.reserve():
        raise HTTPException(409, "이미 RAGAS 평가가 실행 중입니다")
    background_tasks.add_task(ragas_runner.execute_and_save, req.agent_label.strip(), req.judge_model)
    return {"status": "started"}


@app.get("/eval/ragas/execution/status")
def ragas_evaluation_status() -> dict:
    return ragas_runner.state


@app.get("/eval/ragas/execution/events")
async def ragas_evaluation_events(request: Request) -> StreamingResponse:
    async def event_stream():
        previous_payload = ""
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(ragas_runner.state, ensure_ascii=False)
            if payload != previous_payload:
                yield f"event: status\ndata: {payload}\n\n"
                previous_payload = payload
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ExecuteRagasPassKRequest(BaseModel):
    agent_label: str = "rag-passk"
    n_repeats: int = Field(default=5, ge=2, le=20)
    judge_model: str = "gpt-4o-mini"


@app.post("/eval/ragas/pass-k/execution")
def start_ragas_pass_k(req: ExecuteRagasPassKRequest, background_tasks: BackgroundTasks) -> dict:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(400, "OPENAI_API_KEY가 설정되지 않았습니다 (ops-server/backend/.env)")
    if not ragas_pass_k.reserve():
        raise HTTPException(409, "이미 RAGAS pass@k 평가가 실행 중입니다")
    background_tasks.add_task(
        ragas_pass_k.execute_pass_k_and_save, req.agent_label.strip(), req.n_repeats, req.judge_model,
    )
    return {"status": "started"}


@app.get("/eval/ragas/pass-k/execution/status")
def ragas_pass_k_status() -> dict:
    return ragas_pass_k.state


class UploadLoadTestRequest(BaseModel):
    summary: dict = Field(description="k6 --summary-export(또는 handleSummary)가 만든 요약 JSON 그대로")
    label: str = Field(default="loadtest", min_length=1, max_length=80)
    target_url: str
    peak_vus: int = Field(default=1, ge=1, le=100000)
    method: str = "GET"


@app.post("/loadtest/upload")
def upload_loadtest_result(req: UploadLoadTestRequest) -> dict:
    """scripts/loadtest.js의 handleSummary()가 테스트 종료 시 자동으로 호출한다 —
    Ops가 k6를 대신 실행하지 않고, CLI로 직접 돌린 결과만 여기로 전달받아 저장한다."""
    extracted = loadtest_executor.extract_summary(req.summary)
    record = LoadTestRunRecord(
        run_id=uuid.uuid4().hex[:12], label=req.label.strip(), target_url=req.target_url,
        method=req.method, peak_vus=req.peak_vus, **extracted,
    )
    return append_loadtest_run(record).model_dump()


@app.get("/loadtest/runs")
def loadtest_runs(label: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list:
    return load_loadtest_runs(label=label, limit=limit)


class ExecuteBfclRequest(BaseModel):
    agent_label: str = "bfcl-default"


@app.get("/eval/bfcl/cases")
def bfcl_cases() -> list:
    """골드셋 케이스 목록(채점 실행 전 미리보기용)."""
    try:
        return [c.model_dump() for c in bfcl_runner.load_cases()]
    except bfcl_runner.BFCLGoldsetError as e:
        raise HTTPException(500, str(e)) from e


@app.post("/eval/bfcl/cases")
def add_bfcl_case(case: dict) -> dict:
    try:
        return goldset_admin.append_case(bfcl_runner._CASES_PATH, BFCLCase, case, "case_id").model_dump()
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/eval/bfcl/cases/{case_id}")
def delete_bfcl_case(case_id: str) -> dict:
    deleted = goldset_admin.delete_case(bfcl_runner._CASES_PATH, "case_id", case_id)
    if not deleted:
        raise HTTPException(404, f"case_id={case_id!r} 케이스를 찾을 수 없습니다")
    return {"deleted": True}


@app.post("/eval/bfcl/cases/upload")
async def upload_bfcl_cases(file: UploadFile = File(...)) -> dict:
    try:
        count = goldset_admin.replace_from_upload(bfcl_runner._CASES_PATH, BFCLCase, await file.read())
    except goldset_admin.GoldsetWriteError as e:
        raise HTTPException(400, str(e)) from e
    return {"saved": count}


@app.post("/eval/bfcl/execution")
def start_bfcl_evaluation(req: ExecuteBfclRequest, background_tasks: BackgroundTasks) -> dict:
    if not bfcl_runner.reserve():
        raise HTTPException(409, "이미 BFCL 평가가 실행 중입니다")
    background_tasks.add_task(bfcl_runner.execute_and_save, req.agent_label.strip())
    return {"status": "started"}


@app.get("/eval/bfcl/execution/status")
def bfcl_evaluation_status() -> dict:
    return bfcl_runner.state


class ExecutePassKRequest(BaseModel):
    agent_label: str = "bfcl-default"
    n_repeats: int = Field(default=5, ge=2, le=20)


@app.post("/eval/bfcl/pass-k/execution")
def start_bfcl_pass_k(req: ExecutePassKRequest, background_tasks: BackgroundTasks) -> dict:
    if not bfcl_pass_k.reserve():
        raise HTTPException(409, "이미 pass@k 평가가 실행 중입니다")
    background_tasks.add_task(bfcl_pass_k.execute_pass_k_and_save, req.agent_label.strip(), req.n_repeats)
    return {"status": "started"}


@app.get("/eval/bfcl/pass-k/execution/status")
def bfcl_pass_k_status() -> dict:
    return bfcl_pass_k.state


def _direct_read(fn, *args, **kwargs):
    """관측 DB 직접 조회 예외를 HTTP로 옮긴다.

    **미구성/연결 실패를 503으로 드러낸다** — 여기서 사본 조회로 조용히 되돌아가면
    "직접 읽는 줄 알았는데 실은 화면이 옛 사본을 보고 있는" 상태를 아무도 모른다.
    백엔드에서 조용한 폴백이 장애를 숨긴 사례가 이미 둘 있었다(OPENSEARCH_HOST 빈 값,
    RAG_DATABASE_URL 미주입). 화면은 503을 받아 "직접 조회 미구성"을 띄워야 한다.
    """
    try:
        return fn(*args, **kwargs)
    except obs_db.ObservabilityDBUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:  # session_id 형식 오류 등 — 백엔드의 400 INVALID_ID에 대응
        raise HTTPException(400, str(e)) from e


def _run_collect(fn, *args, **kwargs) -> dict:
    """backend_client 예외를 사람이 읽을 수 있는 HTTPException으로 변환."""
    try:
        return fn(*args, **kwargs)
    except backend_client.BackendAuthError as e:
        raise HTTPException(403, str(e)) from e
    except backend_client.BackendUnavailableError as e:
        raise HTTPException(502, str(e)) from e
    except backend_client.BackendResponseError as e:
        status_code = e.status_code if 400 <= e.status_code < 500 else 502
        raise HTTPException(status_code, str(e)) from e


@app.get("/assurance/records")
def get_assurance_records(
    limit: int = Query(100, ge=1, le=500),
    harness: str | None = Query(None, pattern="^(change|output)$"),
    decision: str | None = Query(None, pattern="^(allow_candidate|deny|unassured)$"),
    assurance_verdict: str | None = Query(None, pattern="^(observed|deny|refused)$"),
    request_id: str | None = Query(None, max_length=32),
    session_id: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
) -> dict:
    """권위 원본인 Backend의 검증 판정 기록을 저장하지 않고 read-only로 중계한다."""
    return _run_collect(
        backend_client.fetch_assurance_records,
        limit=limit,
        harness=harness,
        decision=decision,
        assurance_verdict=assurance_verdict,
        request_id=request_id,
        session_id=session_id,
        since=since,
        cursor=cursor,
    )


@app.get("/assurance/records/{receipt_digest}")
def get_assurance_record_detail(receipt_digest: str) -> dict:
    """검증 판정 기록의 마스킹된 상세를 read-only로 중계한다."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_digest):
        raise HTTPException(400, "receipt_digest 형식이 올바르지 않습니다.")
    return _run_collect(backend_client.fetch_assurance_record_detail, receipt_digest)


@app.post("/observability/audit-logs/collect")
def collect_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/audit-logs를 호출해 가져온 뒤 로컬에 저장한다."""
    return _run_collect(collector.collect_audit_logs, limit=limit, method=method, status_code=status_code, user_id=user_id)


@app.get("/observability/audit-logs")
def get_audit_logs(limit: int = 200, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> list:
    """관측 DB를 **직접** 읽는다 — 수집 사본(JSONL)이 아니다.

    사본은 컨테이너 파일시스템에 있어 배포에서 재시작마다 사라진다. 화면이 사본을 읽던
    구조에서는 배포 후 "수집 버튼을 누르기 전까지 빈 화면"이었고, 정작 백엔드가 죽었을 때
    원인을 보려던 과거 데이터도 없었다(관측 시스템이 관측 대상에 의존하는 안티패턴).
    """
    return _direct_read(
        obs_db.fetch_audit_logs, limit=limit, method=method, status_code=status_code, user_id=user_id
    )["logs"]


@app.delete("/observability/audit-logs")
def delete_audit_logs(method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> dict:
    """조건에 맞는 로컬 감사 로그 사본만 삭제한다(Backend 원본 관측 DB는 그대로) —
    조건을 하나도 안 주면 로컬 사본 전체를 지운다."""
    return {"deleted": obs_log_store.delete_audit_logs(method=method, status_code=status_code, user_id=user_id)}


@app.post("/observability/llm-usage/collect")
def collect_llm_usage(days: int = 30, group_by: str = "component") -> dict:
    """A360-Assistant-Backend의 GET /api/admin/llm-usage/stats를 호출해 스냅샷으로 저장한다."""
    return _run_collect(collector.collect_llm_usage, days=days, group_by=group_by)


@app.get("/observability/llm-usage/stats")
def get_llm_usage_stats(
    days: int = Query(30, ge=1, le=365), group_by: str = Query("component"),
) -> dict:
    """LLM 사용량 집계를 관측 DB에서 직접 계산한다 — 비용 리포트 화면의 소스.

    예전엔 수집(POST .../collect)이 집계를 스냅샷으로 사본에 쌓고 화면이 그중 **최신 1건**만
    읽었다. 이력이 목적이 아니라 "지금 집계"를 보려던 것이라, 사본은 중간 저장소일 뿐이었다.
    직접 조회 한 번으로 대체한다(사본은 배포에서 재시작마다 사라진다).
    """
    return _direct_read(obs_db.fetch_llm_usage_stats, days=days, group_by=group_by)


@app.get("/observability/llm-usage/snapshots")
def get_llm_usage_snapshots(group_by: str | None = None, limit: int = 50) -> list:
    """수집 사본에 쌓인 집계 스냅샷 이력 — 화면은 더 이상 쓰지 않는다(위 stats 사용).

    사본을 남겨둔 로컬 분석용 경로다. 배포에서는 컨테이너 재시작 시 사라진다.
    """
    return obs_log_store.load_llm_usage_snapshots(group_by=group_by, limit=limit)


@app.post("/observability/rag-logs/collect")
def collect_rag_logs(limit: int = 100) -> dict:
    """A360-Assistant-Backend의 GET /api/rag/logs/recent를 호출해 http_request 이벤트만
    가져와 저장한다(파이프라인 단계별 이벤트는 텍스트 미리보기가 섞여 있어 기본 제외)."""
    return _run_collect(collector.collect_rag_logs, limit=limit)


@app.get("/observability/rag-logs")
def get_rag_logs(event: str | None = None, path_contains: str | None = None, limit: int = 200) -> list:
    """RAG 요청 로그 — 관측 DB의 `rag_events` 중 `event='http_request'`를 읽는다.

    예전엔 RAG 서버의 파일 로그를 수집해 `{"raw": {...}}`로 담았는데, **같은 내용이 이미
    관측 DB에 중앙화돼 있었다**(RPA-128). 사본을 따로 쌓을 이유가 없어 그쪽을 읽는다.
    반환 필드는 rag-events와 같은 정형 컬럼이다 — 소비 화면(홈·로그 EDA)도 함께 고쳤다.

    path_contains는 더 이상 지원하지 않는다: 경로는 파일 로그의 raw 필드였고 `rag_events`에는
    해당 컬럼이 없다. **조용히 무시하지 않고 400으로 거부한다** — 필터를 줬는데 무시하면
    전량이 돌아오고, 화면은 멀쩡해 보이는데 결과가 틀린다(에러조차 나지 않는다).
    """
    if path_contains:
        raise HTTPException(
            400,
            "path_contains는 지원하지 않습니다 — rag_events에는 경로 컬럼이 없습니다. "
            "필터 없이 조회한 뒤 화면에서 거르거나 request_id로 조회하세요.",
        )
    return _direct_read(
        obs_db.fetch_rag_events, event=event or "http_request", limit=limit
    )["events"]


@app.delete("/observability/rag-logs")
def delete_rag_logs(event: str | None = None, path_contains: str | None = None) -> dict:
    return {"deleted": obs_log_store.delete_rag_logs(event=event, path_contains=path_contains)}


@app.post("/observability/metrics-daily/collect")
def collect_metrics_daily(
    days: int = Query(7, ge=1, le=90), method: str | None = None, path: str | None = None,
) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/metrics-daily(RPA-104 롤업)를 가져와 저장한다."""
    return _run_collect(collector.collect_metrics_daily, days=days, method=method, path=path)


@app.get("/observability/metrics-daily")
def get_metrics_daily(
    method: str | None = None,
    path_contains: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    days: int = Query(90, ge=1, le=90),
) -> list:
    """관측 DB 직접 조회. days는 새로 추가한 선택 인자다 — 사본 조회 시절엔 기간 개념이
    없어(수집된 것 전부) 프론트가 보내지 않는다. 기본값을 상한(90일)으로 둬 기존 화면이
    보던 범위를 좁히지 않는다."""
    return _direct_read(
        obs_db.fetch_metrics_daily, days=days, method=method, path=path_contains, limit=limit
    )["rows"]


@app.post("/observability/usage-daily/collect")
def collect_usage_daily(
    days: int = Query(30, ge=1, le=365), component: str | None = None, model: str | None = None,
) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/usage-daily(RPA-104 롤업)를 가져와 저장한다."""
    return _run_collect(collector.collect_usage_daily, days=days, component=component, model=model)


@app.get("/observability/usage-daily")
def get_usage_daily(
    component: str | None = None,
    model: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    days: int = Query(365, ge=1, le=365),
) -> list:
    """관측 DB 직접 조회. days·model은 새로 추가한 선택 인자이고, 기본값은
    get_metrics_daily와 같은 이유로 상한이다(기존 화면 범위를 좁히지 않기 위해)."""
    return _direct_read(
        obs_db.fetch_usage_daily, days=days, component=component, model=model, limit=limit
    )["rows"]


@app.post("/observability/turn-events/collect")
def collect_turn_events(session_id: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/turn-events(RPA-105)를 가져와 저장한다."""
    return _run_collect(collector.collect_turn_events, session_id=session_id, limit=limit)


@app.get("/observability/turn-events")
def get_turn_events(session_id: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list:
    """관측 DB 직접 조회 — get_audit_logs와 같은 이유."""
    return _direct_read(obs_db.fetch_turn_events, session_id=session_id, limit=limit)["events"]


@app.delete("/observability/turn-events")
def delete_turn_events(session_id: str | None = None) -> dict:
    return {"deleted": obs_log_store.delete_turn_events(session_id=session_id)}


@app.post("/observability/rag-events/collect")
def collect_rag_events(request_id: str | None = None, limit: int = Query(500, ge=1, le=2000)) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/rag-events(RPA-128)를 가져와 저장한다 —
    embed/search/rerank 등 RAG 파이프라인 단계별 소요·설정."""
    return _run_collect(collector.collect_rag_events, request_id=request_id, limit=limit)


@app.get("/observability/rag-events")
def get_rag_events(request_id: str | None = None, event: str | None = None, limit: int = Query(500, ge=1, le=2000)) -> list:
    """관측 DB 직접 조회 — get_audit_logs와 같은 이유."""
    return _direct_read(
        obs_db.fetch_rag_events, request_id=request_id, event=event, limit=limit
    )["events"]


@app.delete("/observability/rag-events")
def delete_rag_events(request_id: str | None = None, event: str | None = None) -> dict:
    return {"deleted": obs_log_store.delete_rag_events(request_id=request_id, event=event)}


@app.post("/observability/request-metrics/collect")
def collect_request_metrics(
    limit: int = Query(500, ge=1, le=2000), method: str | None = None, path: str | None = None,
) -> dict:
    """A360-Assistant-Backend의 GET /api/admin/request-metrics(RPA-103 raw)를 증분 수집한다
    ('오늘 실시간' 패널 — 롤업 60분 지연 보완)."""
    return _run_collect(collector.collect_request_metrics, limit=limit, method=method, path=path)


@app.get("/observability/request-metrics")
def get_request_metrics(
    method: str | None = None, path_contains: str | None = None, limit: int = Query(500, ge=1, le=2000),
) -> list:
    """관측 DB 직접 조회 — get_audit_logs와 같은 이유."""
    return _direct_read(
        obs_db.fetch_request_metrics, method=method, path=path_contains, limit=limit
    )["rows"]


@app.get("/observability/backend-health")
def backend_health(probe: bool = True) -> dict:
    """A360-Assistant-Backend 생존 감시. probe=true면 지금 /health를 찔러 갱신,
    false면 마지막으로 관측된 상태를 반환한다(무인증 경량 경로 — 데이터 조회와 분리)."""
    return collector.probe_backend_health() if probe else collector.backend_health()


@app.get("/observability/trace")
def observability_trace(request_id: str | None = None, session_id: str | None = None, user_id: str | None = None) -> dict:
    """한 사건(request_id/session_id/user_id)에 연결된 관측 레코드를 모아 반환한다 (대시보드 #5).
    감사·성능·턴·RAG를 한 화면에서 추적하기 위한 상관관계 조회 — 저장된 수집분에서 필터.
    user_id는 request_id/session_id처럼 opaque id를 몰라도 조회할 수 있게 하는 축 —
    audit_logs/request_metrics에서 해당 user_id의 request_id들을 먼저 찾아 확장한다.

    관측 DB를 직접 읽는다. 사본 기반일 때는 **장애 원인을 좇는 이 화면이 정작 배포에서
    비어 있었다** — 사본이 컨테이너 재시작마다 사라지기 때문이다."""
    if not (request_id or session_id or user_id):
        raise HTTPException(400, "request_id, session_id, user_id 중 하나는 필요합니다")
    return _direct_read(
        obs_db.trace_by, request_id=request_id, session_id=session_id, user_id=user_id
    )


@app.get("/observability/status")
def observability_status() -> dict:
    return collector.status()


# ─────────────────────────────────────────────────────────────────────────────
# 런타임 설정 (RPA-174) — 백엔드의 무중단 튜닝 API를 ops 화면에 연결한다.
#
# 관측 데이터와 달리 **로컬 사본을 두지 않고 통과(pass-through)한다**: 설정은 "지금 실제로
# 무엇이 적용 중인가"가 전부라, 수집 시점의 사본을 보여주면 관리자가 옛 값을 보고 조작하게 된다.
# ─────────────────────────────────────────────────────────────────────────────


def _settings_call(fn, *args, **kwargs) -> dict:
    """backend_settings 호출을 HTTP 상태로 옮긴다 — 세 가지 실패를 구분해서.

    화면이 "내 입력이 틀렸다"(422)와 "권한 문제"(403)와 "백엔드가 죽었다"(502)를 다르게
    보여줄 수 있어야 한다 — 다 500으로 뭉치면 관리자가 뭘 해야 할지 알 수 없다.
    """
    try:
        return fn(*args, **kwargs)
    except backend_settings.BackendValidationError as e:
        raise HTTPException(422, str(e)) from e
    except backend_client.BackendAuthError as e:
        raise HTTPException(403, str(e)) from e
    except backend_client.BackendUnavailableError as e:
        raise HTTPException(502, str(e)) from e


class BudgetLimitsBody(BaseModel):
    """4개 전체 스냅샷 — 부분 갱신이 아니다(백엔드 append-only 이력에 완전한 설정이 남아야 함).
    null = 그 상한 비활성. 값 검증(0·음수 거부, 월<일 거부)은 백엔드가 단일 진실로 한다.

    ⚠️ 기본값을 주지 않는다(`... `). 기본값이 있으면 생략 필드가 조용히 null이 돼 백엔드의
    월<일 검증까지 우회된다(백엔드 #243 리뷰에서 잡힌 버그) — 여기서 막아야 그 요청이 아예 안 간다.
    """

    subject_daily_usd: float | None = Field(...)
    subject_monthly_usd: float | None = Field(...)
    global_daily_usd: float | None = Field(...)
    global_monthly_usd: float | None = Field(...)


class RetrievalParamsBody(BaseModel):
    """5개 전체 스냅샷. 값 검증(범위·nan/inf)은 백엔드가 한다."""

    candidate_pool_size: int
    rerank_candidates: int
    rrf_k: int
    vector_weight: float
    bm25_weight: float


@app.get("/settings/budget-limits")
def get_budget_limits() -> dict:
    """현재 활성 LLM 예산 상한 (백엔드 RPA-173). source=db면 누가 바꾼 값, config면 백엔드 .env."""
    return _settings_call(backend_settings.fetch_budget_limits)


@app.put("/settings/budget-limits")
def put_budget_limits(body: BudgetLimitsBody) -> dict:
    """LLM 예산 상한 갱신 — 재배포 없이 다음 턴부터 반영.

    ⚠️ 서비스를 막는 값이다. 근거는 백엔드 scripts/budget_calibration_report.py로 뽑는다.
    """
    return _settings_call(backend_settings.update_budget_limits, **body.model_dump())


@app.get("/settings/retrieval-params")
def get_retrieval_params() -> dict:
    """현재 활성 RAG 검색 파라미터 (백엔드 RPA-149)."""
    return _settings_call(backend_settings.fetch_retrieval_params)


@app.put("/settings/retrieval-params")
def put_retrieval_params(body: RetrievalParamsBody) -> dict:
    """RAG 검색 파라미터 갱신 — 재시작 없이 다음 검색부터 반영."""
    return _settings_call(backend_settings.update_retrieval_params, **body.model_dump())


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
