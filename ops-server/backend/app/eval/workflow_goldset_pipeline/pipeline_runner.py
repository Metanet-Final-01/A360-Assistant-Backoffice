"""zip 업로드 -> 전처리 -> canonical 변환 -> pm4py/WorFBench 변환을 순서대로 실행하고,
진행 상황을 state 딕셔너리에 남긴다.

RAGAS 평가 실행기(ragas_eval/runner.py)와 청크 실험 실행기(ragas_eval/
chunk_experiment_runner.py)가 쓰는 것과 같은 모양이다: reserve로 중복 실행을 막고,
state["log"]에 진행 로그를 쌓고, main.py가 그 state를 그대로 상태 조회/SSE
엔드포인트에 내보낸다. 프론트는 그 state를 보고 "o-o-o-o-o" 같은 단계 표시와
텍스트 로그를 같이 그린다.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..workflow_eval.reservation import finish_state, reserve_state
from . import canonical_convert, case_store, pm4py_convert, worfbench_convert, workflow_extract, zip_extract

# 화면에 "o-o-o-o-o" 형태로 보여줄 단계 이름들. 순서 그대로 진행된다.
PIPELINE_STAGES = ["업로드", "전처리", "canonical 변환", "pm4py/WorFBench 변환"]

MAX_LOG_LINES_KEPT = 200

# 이 파이프라인이 실행될 때마다 만드는 산출물(압축 해제된 파일, canonical json,
# pm4py pnml/ptml, worfbench json)을 저장해두는 폴더. 나중에 시각화나 다운로드
# 기능에서 다시 읽을 수 있도록 실행이 끝난 뒤에도 지우지 않는다.
RUNS_DIR = Path(__file__).resolve().parents[3] / "data" / "workflow_goldset_pipeline_runs"

state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "run_id": None,
    "stage_index": -1,
    "stages": PIPELINE_STAGES,
    "error": None,
    "log": [],
    "results": [],
}


def _append_log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    state["log"].append(f"{timestamp} {message}")
    if len(state["log"]) > MAX_LOG_LINES_KEPT:
        state["log"] = state["log"][-MAX_LOG_LINES_KEPT:]


def _set_stage(stage_index: int) -> None:
    state["stage_index"] = stage_index
    _append_log(f"[{stage_index + 1}/{len(PIPELINE_STAGES)}] {PIPELINE_STAGES[stage_index]} 시작")


def reserve() -> bool:
    """이미 실행 중이면 False를 반환해서 중복 실행을 막는다."""
    return reserve_state(state, {
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "run_id": None,
        "stage_index": -1,
        "error": None,
        "log": [],
        "results": [],
    })


@dataclass
class WorkflowFileResult:
    """워크플로우 파일 하나(zip 안에 여러 개 있을 수 있다)를 끝까지 변환한 결과."""

    case_id: str
    manifest_path: str
    content_type: str
    canonical_step_count: int
    pm4py_leaf_count: int
    worfbench_fidelity: str
    worfbench_action_count: int
    output_dir: str


def run_pipeline_from_zip(zip_file_path: Path) -> None:
    """업로드된 zip 파일 경로 하나를 받아서 파이프라인 전체를 실행한다.

    reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다
    (main.py가 background task로 등록하기 전에 reserve()를 먼저 호출한다).
    """
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    state["run_id"] = run_id

    try:
        run_dir.mkdir(parents=True, exist_ok=True)

        _set_stage(0)  # 업로드
        _append_log(f"업로드된 zip: {zip_file_path.name}")

        _set_stage(1)  # 전처리
        unpacked_dir = run_dir / "unpacked"
        extracted_file_count = zip_extract.extract_zip_safely(zip_file_path, unpacked_dir)
        _append_log(f"압축 해제 완료 ({extracted_file_count}개 파일)")

        workflow_files = workflow_extract.extract_workflow_files(unpacked_dir)
        _append_log(f"워크플로우 파일 {len(workflow_files)}개 발견")

        results = _convert_all_workflow_files(workflow_files, run_dir)
        state["results"] = [result.__dict__ for result in results]

    except Exception as error:
        _append_log(f"실패: {type(error).__name__}: {error}")
        state["error"] = str(error)
    finally:
        finish_state(state)


def run_pipeline_from_pasted_workflow(raw_workflow_text: str, source_label: str) -> None:
    """zip 대신 워크플로우 JSON 원문을 텍스트로 붙여넣은 경우. 압축 해제 단계가
    필요 없으므로 "전처리" 단계는 JSON 파싱 검증만 하고 바로 다음 단계로 간다.
    """
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    state["run_id"] = run_id

    try:
        run_dir.mkdir(parents=True, exist_ok=True)

        _set_stage(0)  # 업로드
        _append_log(f"텍스트로 입력된 워크플로우: {source_label}")

        _set_stage(1)  # 전처리 (여기서는 JSON 파싱만)
        try:
            workflow_json = json.loads(raw_workflow_text)
        except json.JSONDecodeError as error:
            raise ValueError(f"워크플로우 JSON 형식이 아닙니다: {error}") from error
        _append_log("JSON 파싱 완료")

        workflow_file = workflow_extract.ExtractedWorkflowFile(
            manifest_path=source_label,
            content_type="pasted-text",
            workflow_json=workflow_json,
        )
        results = _convert_all_workflow_files([workflow_file], run_dir)
        state["results"] = [result.__dict__ for result in results]

    except Exception as error:
        _append_log(f"실패: {type(error).__name__}: {error}")
        state["error"] = str(error)
    finally:
        finish_state(state)


def _convert_all_workflow_files(workflow_files: list, run_dir: Path) -> list[WorkflowFileResult]:
    _set_stage(2)  # canonical 변환
    canonical_workflows = []
    for workflow_file in workflow_files:
        canonical = canonical_convert.normalize_workflow(workflow_file.workflow_json, workflow_file.manifest_path)
        canonical_workflows.append((workflow_file, canonical))
        step_count = canonical_convert.count_steps(canonical["steps"])
        _append_log(f"canonical 변환 완료: {workflow_file.manifest_path} ({step_count}개 스텝)")

    _set_stage(3)  # pm4py/WorFBench 변환
    results: list[WorkflowFileResult] = []
    for index, (workflow_file, canonical) in enumerate(canonical_workflows, start=1):
        result = _convert_one_workflow_to_pm4py_and_worfbench(canonical, run_dir, file_index=index)
        results.append(result)
        _append_log(
            f"[{index}/{len(canonical_workflows)}] pm4py/WorFBench 변환 완료: "
            f"leaf={result.pm4py_leaf_count}, worfbench_fidelity={result.worfbench_fidelity}"
        )

    return results


def _convert_one_workflow_to_pm4py_and_worfbench(
    canonical: dict, run_dir: Path, *, file_index: int,
) -> WorkflowFileResult:
    output_dir = run_dir / f"workflow_{file_index}"
    output_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = output_dir / "canonical.json"
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")

    process_tree, tree_json = pm4py_convert.convert_canonical_steps_to_process_tree(canonical["steps"])
    pm4py_convert.export_process_tree_files(process_tree, tree_json, output_dir / "pm4py")

    worfbench_record = worfbench_convert.build_worfbench_record(
        canonical["steps"], canonical["source_file"], record_id=f"run/{run_dir.name}/workflow_{file_index}",
    )
    worfbench_path = output_dir / "worfbench.json"
    worfbench_path.write_text(json.dumps(worfbench_record, ensure_ascii=False, indent=2), encoding="utf-8")

    canonical_step_count = canonical_convert.count_steps(canonical["steps"])
    pm4py_leaf_count = pm4py_convert.count_leaf_actions(tree_json)
    worfbench_fidelity = worfbench_record["meta"]["worfbench_fidelity"]
    worfbench_action_count = len(worfbench_record["meta"]["actions"])

    # 변환된 워크플로우를 draft 케이스로 저장한다 — 검수 화면에서 사람이 보고
    # 승인해야 실제 평가 세트에 쓸 수 있는 상태가 된다.
    saved_case = case_store.save_new_case(
        source_label=canonical["source_file"],
        canonical_steps=canonical["steps"],
        canonical_step_count=canonical_step_count,
        pm4py_leaf_count=pm4py_leaf_count,
        worfbench_fidelity=worfbench_fidelity,
        worfbench_action_count=worfbench_action_count,
        run_id=run_dir.name,
    )

    return WorkflowFileResult(
        case_id=saved_case.case_id,
        manifest_path=canonical["source_file"],
        content_type="",
        canonical_step_count=canonical_step_count,
        pm4py_leaf_count=pm4py_leaf_count,
        worfbench_fidelity=worfbench_fidelity,
        worfbench_action_count=worfbench_action_count,
        output_dir=str(output_dir.relative_to(RUNS_DIR.parent)),
    )


def delete_old_temp_upload(zip_file_path: Path) -> None:
    """업로드 처리 중 임시로 저장해둔 zip 파일을 정리한다. 실행 결과(run_dir)는
    건드리지 않는다 — 그건 나중에 다시 보거나 다운로드할 수 있어야 한다."""
    try:
        zip_file_path.unlink(missing_ok=True)
    except OSError:
        pass
