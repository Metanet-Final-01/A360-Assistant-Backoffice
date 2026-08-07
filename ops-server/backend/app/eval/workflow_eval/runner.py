"""Workflow 평가 라이브 러너 — 확정 골드셋 9개를 실제 Backend Agent로 채점한다.

채점 로직은 여기에 두지 않고 `scripts/agent_flow_eval/evaluation/audit_final_goldset.py`
를 그대로 호출한다. 로컬 CLI 채점과 웹 채점이 **같은 코드 한 벌**을 쓰게 하려는
것으로, 채점 규칙을 고칠 때 두 곳을 맞추다 어긋나는 일을 막는다(실제로 같은 이름의
변환 함수가 두 곳에서 다르게 동작하던 문제를 한 번 겪었다).

2026-08-07에 옛 방식을 걷어냈다:
- 골드셋: `goldset_from_bots.json`(17개) -> `confirmed_goldset/gold`(교차검수 확정 9개)
- 예측: `{package, action}`만 뽑던 평탄화 -> `convert_backend_recommendation`을 거쳐
  **제어 구조와 파라미터를 보존**한다. 새 채점기는 파라미터를 봐야 로그성 작업을
  걸러낼 수 있고, if 분기 구조가 있어야 순서를 제대로 계산한다.
- 채점: pm4py + WorFBench 서브프로세스 -> `audit_final_goldset.score_case()` 인프로세스.
  **pm4py는 폐기했다**(실측 fitness 0.089/precision 0.0 — 정상적인 구현 차이를 전부
  일탈로 잡는 구조라 이 과제에 맞지 않았다).
"""

import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .reservation import finish_state, reserve_state

logger = logging.getLogger(__name__)

_MAX_LOG_LINES = 200
_RECOMMEND_TRIGGER = "이 업무를 분석해서 자동화 워크플로우로 추천해줘."

state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "saved": 0, "cases": 0, "error": None, "log": [],
}


def _agent_flow_eval_root() -> Path:
    """채점기가 있는 `scripts/agent_flow_eval`을 찾는다.

    executor.py가 a360-eval-sandbox를 찾는 방식과 같다 — 환경변수를 먼저 보고,
    없으면 저장소 구조에서 추론한다. 컨테이너에서는 Dockerfile이 이 폴더를
    `/app/scripts/agent_flow_eval`로 복사하므로 그 경로도 후보에 넣는다."""
    override = os.getenv("A360_AGENT_FLOW_EVAL")
    if override:
        return Path(override).resolve()
    backend_root = Path(__file__).resolve().parents[3]        # ops-server/backend
    candidates = [
        backend_root / "scripts" / "agent_flow_eval",          # 컨테이너(/app/scripts/...)
    ]
    if len(backend_root.parents) > 1:
        # 컨테이너에서는 backend_root가 /app이라 parents[1]이 없다.
        # 저장소 루트에서 직접 실행할 때만 존재하는 fallback이라 길이를 먼저 확인한다.
        candidates.append(backend_root.parents[1] / "scripts" / "agent_flow_eval")  # 저장소 루트에서 실행
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


AGENT_FLOW_EVAL = _agent_flow_eval_root()
GOLD_DIR = AGENT_FLOW_EVAL / "goldset_expansion" / "confirmed_goldset" / "gold"
BRIEF_DIR = AGENT_FLOW_EVAL / "goldset_expansion" / "confirmed_goldset" / "briefs"


class WorkflowGoldsetError(RuntimeError):
    """골드셋 파일이 없거나 비어 있음."""


def _import_scorer():
    """채점기 모듈을 import한다. 경로가 없으면 여기서 분명한 메시지로 실패시킨다 —
    조용히 빈 결과를 내면 "채점이 돌았는데 0점"인지 "안 돌았는지" 구분이 안 된다."""
    if not GOLD_DIR.is_dir():
        raise WorkflowGoldsetError(
            f"채점기 경로를 찾지 못했습니다: {AGENT_FLOW_EVAL}. "
            "A360_AGENT_FLOW_EVAL 환경변수로 지정하거나 이미지에 포함되었는지 확인하세요."
        )
    evaluation_dir = AGENT_FLOW_EVAL / "evaluation"
    for path in (str(evaluation_dir), str(AGENT_FLOW_EVAL), str(AGENT_FLOW_EVAL / "processing")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import audit_final_goldset  # type: ignore
    from convert_backend_recommendation import convert_recommendation  # type: ignore

    return audit_final_goldset, convert_recommendation


def reserve() -> bool:
    """RAGAS runner와 동일한 원자적 check-and-set."""
    return reserve_state(state, {
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "saved": 0, "cases": 0, "error": None, "log": [],
    })


def _append_log(message: str) -> None:
    state["log"].append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}")
    del state["log"][:-_MAX_LOG_LINES]


def load_cases() -> list[dict]:
    """확정 골드셋 9개. 각 케이스는 정답 워크플로우 하나와 업무정의서 하나를 갖는다."""
    if not GOLD_DIR.is_dir():
        raise WorkflowGoldsetError(f"골드셋 폴더가 없습니다: {GOLD_DIR}")
    cases = []
    for gold_path in sorted(GOLD_DIR.glob("*.goldset.json")):
        case_id = gold_path.name[:4]
        briefs = sorted(BRIEF_DIR.glob(f"{case_id}_*.md"))
        cases.append({
            "case_id": case_id,
            "gold_file": gold_path.name,
            "brief_file": briefs[0].name if briefs else None,
            "title": _brief_title(briefs[0]) if briefs else None,
        })
    if not cases:
        raise WorkflowGoldsetError(f"골드셋이 비어 있습니다: {GOLD_DIR}")
    return cases


def load_input_dataset() -> dict[str, str]:
    """case_id -> 업무정의서 원문. 에이전트에게 실제로 입력되는 텍스트 그대로다.

    옛 파이프라인은 이걸 detailed_task_descriptions.json 하나로 관리했는데, 지금은
    confirmed_goldset/briefs/의 .md가 정본이다 - 골드셋과 짝을 이뤄 저장소에서
    교차검수로 관리되므로 화면에서는 조회만 한다."""
    if not BRIEF_DIR.is_dir():
        raise WorkflowGoldsetError(f"업무정의서 폴더가 없습니다: {BRIEF_DIR}")
    return {
        path.name[:4]: path.read_text(encoding="utf-8")
        for path in sorted(BRIEF_DIR.glob("*.md"))
    }


def _brief_title(brief_path: Path) -> str | None:
    for line in brief_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("과제명:"):
            return line.split(":", 1)[1].strip()
    return None


def _stream_turn(
    client: httpx.Client, backend_url: str, session_id: str, message: str,
    agent_version: str | None = None,
) -> dict:
    payload: dict = {"message": message, "operation": "chat"}
    if agent_version:
        payload["agent_version"] = agent_version
    done_data: dict = {}
    with client.stream(
        "POST", f"{backend_url}/api/sessions/{session_id}/turn",
        json=payload, timeout=180.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: "):])
            if event.get("event") == "done":
                done_data = event.get("data") or {}
    return done_data


def generate_predictions(
    agent_label: str, agent_version: str | None = None, backend_url: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Path]:
    """케이스마다 실제 Backend Agent를 호출해 예측을 만들고, 채점기가 읽는
    goldset.json 형식으로 변환해 저장한다. 반환값은 case_id -> 예측 파일 경로."""
    _, convert_recommendation = _import_scorer()
    backend_url = (backend_url or os.getenv("A360_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
    output_dir = AGENT_FLOW_EVAL / "runner" / "logs" / f"ops_live_{agent_label}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases()
    predictions: dict[str, Path] = {}

    with httpx.Client() as client:
        for index, case in enumerate(cases, 1):
            case_id = case["case_id"]
            try:
                brief = (BRIEF_DIR / case["brief_file"]).read_text(encoding="utf-8")
                session = client.post(f"{backend_url}/api/sessions", json={}, timeout=10.0)
                session.raise_for_status()
                session_id = session.json()["session_id"]

                document = client.post(
                    f"{backend_url}/api/documents/text",
                    json={"text": brief, "session_id": session_id}, timeout=10.0,
                )
                document.raise_for_status()

                done = _stream_turn(client, backend_url, session_id, _RECOMMEND_TRIGGER, agent_version)
                recommendation = done.get("recommendation")
                if not recommendation:
                    raise RuntimeError("백엔드 응답에 recommendation이 없습니다")

                converted = convert_recommendation(
                    run_manifest={"runner": "ops-server", "run_id": f"ops_live_{agent_label}",
                                  "session_id": session_id, "document_id": document.json().get("document_id")},
                    data=done,
                    recommendation=recommendation,
                    source_file=case["gold_file"],
                    step_name="turnRecommend",
                )
                path = output_dir / f"{case_id}.goldset.json"
                path.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8")
                predictions[case_id] = path
                if on_progress:
                    on_progress(f"[{index}/{len(cases)}] ✓ {case_id} 액션 {len(converted.get('steps') or [])}개")
            except Exception as exc:  # noqa: BLE001 — 케이스 하나 실패가 전체를 막지 않는다
                logger.warning("Workflow 케이스 실패: %s", case_id, exc_info=True)
                if on_progress:
                    on_progress(f"[{index}/{len(cases)}] ⚠ {case_id} 오류: {exc}")

    return predictions


def _metrics_from_scores(scored: dict) -> list[EvalMetric]:
    rule_only = scored.get("rule_only_action_prf1") or {}
    judge = scored.get("action_prf1") or {}
    chain = scored.get("action_chain") or {}
    branch = scored.get("branch_coverage") or {}
    worfbench = scored.get("worfbench") or {}
    pairs = [
        ("workflow_rule_only_precision", rule_only.get("precision")),
        ("workflow_rule_only_recall", rule_only.get("recall")),
        ("workflow_rule_only_f1", rule_only.get("f1")),
        ("workflow_judge_f1", judge.get("f1")),
        ("workflow_chain_f1", chain.get("f1")),
        ("workflow_branch_coverage", branch.get("branch_coverage")),
        ("workflow_branch_score", branch.get("branch_score")),
    ]
    # WorFBench는 외부 참고치라 라이브러리(sentence-transformers/torch, 모델 수백MB)를
    # 이미지에 넣지 않았다 - 없으면 status가 ok가 아니고 이 지표만 빠진다. 주 지표
    # (rule-only/chain/branch)는 그대로 산출된다(컨테이너 실측 확인, 2026-08-07).
    if worfbench.get("status") == "ok":
        pairs.append(("worfbench_f1_score", worfbench.get("f1")))
    return [EvalMetric(name=name, value=float(value)) for name, value in pairs if value is not None]


def execute_and_save(agent_label: str, agent_version: str | None = None) -> None:
    """reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다."""
    try:
        audit, _ = _import_scorer()
        cases = load_cases()
        state["cases"] = len(cases)
        _append_log(f"확정 골드셋 {len(cases)}개 — 라이브 예측 생성 시작")

        predictions = generate_predictions(agent_label, agent_version, on_progress=_append_log)
        if not predictions:
            raise RuntimeError("예측이 하나도 생성되지 않았습니다 — 백엔드 응답을 확인하세요")

        evaluation_id = uuid4().hex[:12]
        saved = 0
        for case_id, prediction_path in sorted(predictions.items()):
            try:
                scored = audit.score_case_files(
                    gold_file=audit.gold_path(case_id),
                    pred_file=prediction_path,
                    case_id=case_id,
                )
                append_run(EvalRunRecord(
                    evaluation_id=evaluation_id,
                    dataset_id="confirmed-goldset-9",
                    dataset_version="2026.08",
                    case_id=case_id,
                    source="workflow",
                    agent_label=agent_label,
                    commit_sha=None,
                    config={"scorer": "audit_final_goldset", "prediction": str(prediction_path),
                            "agent_version": agent_version},
                    score=(scored.get("rule_only_action_prf1") or {}).get("f1"),
                    metrics=_metrics_from_scores(scored),
                    raw=scored,
                ))
                saved += 1
                rule_f1 = (scored.get("rule_only_action_prf1") or {}).get("f1")
                _append_log(f"채점 {case_id}: Rule-only F1 {rule_f1:.4f}" if rule_f1 is not None else f"채점 {case_id} 완료")
            except Exception as exc:  # noqa: BLE001 — 케이스 하나 실패를 전체 실패로 만들지 않는다
                logger.warning("Workflow 채점 실패: %s", case_id, exc_info=True)
                _append_log(f"⚠ {case_id} 채점 실패: {exc}")

        _append_log(f"결과 저장 완료 — {saved}건")
        state.update({"saved": saved})
    except Exception as exc:  # noqa: BLE001 — 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("Workflow 라이브 평가 실행 실패")
        state["error"] = str(exc)
        _append_log(f"오류: {exc}")
    finally:
        finish_state(state)
