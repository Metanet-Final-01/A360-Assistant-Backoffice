import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .log_schema import EvalRunRecord
from .log_store import append_run
from .metrics import metrics_from_raw

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
# a360-eval-sandbox는 로컬 개발 시 backoffice 레포의 형제 폴더(예: Desktop 아래)다. 백엔드가
# ops-server/backend로 한 단계 깊어졌으므로 parents[2](= backoffice의 부모)에서 형제를 찾는다.
# 단, 컨테이너는 backend만 /app으로 복사돼 상위 경로가 얕고 parents[2]가 없어 IndexError가 난다
# (부팅 자체가 막힌다). 그 경우 백엔드 루트 아래로 떨어뜨린다. 실제 위치는 A360_EVAL_SANDBOX로
# 명시 지정한다 — 이 기본값은 임포트 시 부팅을 막지 않기 위한 안전판일 뿐이다.
_sandbox_parents = _BACKEND_ROOT.parents
_DEFAULT_SANDBOX = (_sandbox_parents[2] if len(_sandbox_parents) > 2 else _BACKEND_ROOT) / "a360-eval-sandbox"
SANDBOX_ROOT = Path(os.getenv("A360_EVAL_SANDBOX", str(_DEFAULT_SANDBOX))).resolve()
METADATA_DIR = SANDBOX_ROOT / "Metadata"
PYTHON = SANDBOX_ROOT / ".venv-verify" / "Scripts" / "python.exe"
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")

state: dict = {
    "running": False,
    "stage": None,
    "evaluation_id": None,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "saved": 0,
    "log": "",
    "error": None,
}


def validate_prediction_label(label: str) -> Path:
    if not _SAFE_LABEL.fullmatch(label):
        raise ValueError("prediction_label은 영문·숫자·점·밑줄·하이픈만 사용할 수 있습니다")
    path = METADATA_DIR / f"predictions_from_agent_{label}.json"
    if not path.is_file():
        raise FileNotFoundError(f"예측 입력 파일이 없습니다: {path.name}")
    if not PYTHON.is_file():
        raise FileNotFoundError(f"평가용 Python 환경이 없습니다: {PYTHON}")
    return path


def available_prediction_labels() -> list[str]:
    if not METADATA_DIR.is_dir():
        return []
    prefix = "predictions_from_agent_"
    return sorted(
        path.stem[len(prefix):]
        for path in METADATA_DIR.glob(f"{prefix}*.json")
        if _SAFE_LABEL.fullmatch(path.stem[len(prefix):])
    )


def _run_script(script: str, label: str) -> str:
    process = subprocess.run(
        [str(PYTHON), str(METADATA_DIR / script), label],
        cwd=METADATA_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = process.stdout + process.stderr
    if process.returncode:
        raise RuntimeError(f"{script} 실패(exit={process.returncode})\n{output[-8000:]}")
    return output


def _save_results(
    prediction_label: str,
    evaluation_id: str,
    dataset_id: str,
    dataset_version: str,
    case_ids: set[str],
    agent_label: str,
    commit_sha: str | None,
) -> int:
    files = {
        "pm4py": METADATA_DIR / f"pm4py_agent_conformance_results_{prediction_label}.json",
        "worfbench": METADATA_DIR / f"worfbench_openai_results_{prediction_label}.json",
    }
    saved = 0
    for source, path in files.items():
        for raw in json.loads(path.read_text(encoding="utf-8")):
            case_id = raw.get("source_bot")
            if case_id not in case_ids:
                continue
            metrics = metrics_from_raw(source, raw)
            preferred = "pm4py_fitness" if source == "pm4py" else "worfbench_f1_score"
            score = next((metric.value for metric in metrics if metric.name == preferred), None)
            append_run(EvalRunRecord(
                evaluation_id=evaluation_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                case_id=case_id,
                source=source,
                agent_label=agent_label,
                commit_sha=commit_sha,
                config={"prediction_label": prediction_label, "executor": "a360-eval-sandbox"},
                score=score,
                metrics=metrics,
                raw=raw,
            ))
            saved += 1
    return saved


def execute(
    prediction_label: str,
    evaluation_id: str,
    dataset_id: str,
    dataset_version: str,
    case_ids: list[str],
    agent_label: str,
    commit_sha: str | None,
) -> None:
    state.update(
        running=True,
        stage="pm4py",
        evaluation_id=evaluation_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        returncode=None,
        saved=0,
        log="",
        error=None,
    )
    try:
        validate_prediction_label(prediction_label)
        logs = ["[pm4py]\n" + _run_script("run_pm4py_conformance.py", prediction_label)]
        state.update(stage="worfbench", log="\n".join(logs)[-12000:])
        logs.append("[WorFBench]\n" + _run_script("run_worfbench_conformance.py", prediction_label))
        state.update(stage="saving", log="\n".join(logs)[-12000:])
        saved = _save_results(
            prediction_label, evaluation_id, dataset_id, dataset_version,
            set(case_ids), agent_label, commit_sha,
        )
        state.update(returncode=0, saved=saved, log="\n".join(logs)[-12000:])
    except Exception as exc:  # 백그라운드 작업 실패를 상태 API로 전달
        state.update(returncode=1, error=f"{type(exc).__name__}: {exc}")
    finally:
        state.update(running=False, stage="completed", finished_at=datetime.now(timezone.utc).isoformat())
