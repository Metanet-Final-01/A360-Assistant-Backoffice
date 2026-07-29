"""Workflow(pm4py/WorFBench) 평가 라이브 러너.

기존 실행기(app/eval/executor.py)는 사람이 미리 만들어둔
`predictions_from_agent_<label>.json`을 재채점만 했다 — 그 예측 파일 자체를
여기서 실제 Backend Agent를 호출해 라이브로 만든 뒤, 같은 채점 스크립트
(`executor._run_script`)로 넘긴다. 채점 로직(pm4py/WorFBench 스크립트, 결과 저장)은
그대로 재사용하고 "예측을 사람이 미리 만들어야 한다"는 부분만 라이브로 바꾼 것 —
BFCL/RAGAS 러너와 같은 발상.

골드셋: `a360-eval-sandbox/Metadata/goldset_from_bots_a360_13.json`(13개,
470개 원본 Bot Store 봇을 RAG 카탈로그 전체 커버리지·실제 TaskBot.runTask 기반
메인/서브워크플로우 판정·최소 액션 수 3개 필터로 걸러낸 엄격 검증 세트 —
`PROVENANCE.md` 참고). 예전 `goldset_from_bots.json`(17개, title-only 셀렉션,
RAG 커버리지 미검증)은 더 이상 쓰지 않는다.
`run_pm4py_conformance_a360_13.py`/`run_worfbench_conformance_a360_13.py` 둘 다
같은 `predictions_from_agent_<label>.json`(source_bot + predicted_actions만 있는
평평한 리스트) 하나를 입력으로 쓴다 — WorFBench용 노드/엣지 변환은 그 스크립트
내부에서 함(`to_worfbench_pred_traj` 같은 별도 변환을 여기서 안 해도 됨, 실제
스크립트 코드로 확인함).

트리거 문구/agent_version: 실제 Assistant-Frontend(`src/stores/pipeline.js`의
`startRecommend`)가 `/api/sessions/{id}/turn`에 보내는 문구와 요청 필드를 그대로
맞췄다 — 다른 문구를 쓰면 백엔드가 실제 사용자 흐름과 다른 경로를 탈 수 있다.
"""

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .. import executor
from .reservation import finish_state, reserve_state

logger = logging.getLogger(__name__)

_GOLDSET_PATH = executor.METADATA_DIR / "goldset_from_bots_a360_13.json"
_DETAILED_TASKS_PATH = executor.METADATA_DIR / "detailed_task_descriptions_a360_13.json"
_PM4PY_SCRIPT = "run_pm4py_conformance_a360_13.py"
_WORFBENCH_SCRIPT = "run_worfbench_conformance_a360_13.py"
_DATASET_ID = "workflow-live-a360-13"
_DATASET_VERSION = "a360_13"
_MAX_LOG_LINES = 200
_KNOWN_AGENT_VERSIONS = ("v1", "v2", "v3")

state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "saved": 0, "cases": 0, "error": None, "log": [],
}


def reserve() -> bool:
    """BFCL/RAGAS runner와 동일한 원자적 check-and-set."""
    return reserve_state(state, {
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "saved": 0, "cases": 0, "error": None, "log": [],
    })


def _append_log(message: str) -> None:
    state["log"].append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}")
    del state["log"][:-_MAX_LOG_LINES]


class WorkflowGoldsetError(RuntimeError):
    """골드셋 파일이 없거나 비어 있음."""


def load_cases() -> list[dict]:
    if not _GOLDSET_PATH.exists():
        raise WorkflowGoldsetError(f"골드셋 파일이 없습니다: {_GOLDSET_PATH}")
    cases = json.loads(_GOLDSET_PATH.read_text(encoding="utf-8"))
    if not cases:
        raise WorkflowGoldsetError(f"골드셋이 비어 있습니다: {_GOLDSET_PATH}")
    return cases


def _load_detailed_tasks() -> dict[str, str]:
    """source_bot -> 상세 업무정의서 원문. 과거 예측 생성 스크립트가 쓴 것과 동일한
    입력으로 맞춰야 현재-과거 비교가 공정하다(one-liner만 쓰면 부당하게 저평가됨)."""
    if not _DETAILED_TASKS_PATH.exists():
        return {}
    return json.loads(_DETAILED_TASKS_PATH.read_text(encoding="utf-8-sig"))


_RECOMMEND_TRIGGER = "이 업무정의서로 자동화 흐름도 만들어줘"


def _flatten_recommendation(recommendation: dict | None) -> list[dict]:
    """RecommendedAction 트리를 평평하게 순회해 {package, action}만 뽑는다 —
    predictions_from_agent 형식이 파라미터 없이 이 두 필드만 요구한다."""
    if not recommendation:
        return []
    out: list[dict] = []

    def walk(actions: list[dict]) -> None:
        for a in actions:
            out.append({"package": a.get("package"), "action": a.get("action")})
            walk(a.get("children") or [])

    for step in recommendation.get("steps") or []:
        walk(step.get("actions") or [])
    return out


def _stream_turn(
    client: httpx.Client, backend_url: str, session_id: str, message: str,
    agent_version: str | None = None,
) -> dict:
    body: dict = {"message": message, "operation": "chat"}
    if agent_version:
        body["agent_version"] = agent_version
    done_data: dict = {}
    with client.stream(
        "POST", f"{backend_url}/api/sessions/{session_id}/turn",
        json=body, timeout=180.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[len("data: "):])
            if evt.get("event") == "done":
                done_data = evt.get("data") or {}
    return done_data


def generate_predictions(
    backend_url: str | None = None, on_progress: Callable[[str], None] | None = None,
    agent_version: str | None = None,
) -> list[dict]:
    """골드셋 각 봇 업무에 대해 실제 Backend Agent를 호출해 predictions_from_agent
    형식(source_bot/predicted_actions/predicted_action_count)의 예측을 만든다.
    BFCL 러너와 같은 패턴: 문서로 업무를 등록한 뒤 고정 트리거 문구를 보내야
    라우터가 recommendation으로 판단하고 백엔드가 결과를 저장한다(실측 확인된 패턴)."""
    backend_url = (backend_url or os.getenv("A360_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
    cases = load_cases()
    detailed_tasks = _load_detailed_tasks()
    predictions: list[dict] = []

    with httpx.Client() as client:
        for i, case in enumerate(cases, 1):
            source_bot = str(case.get("source_bot") or f"case_{i}")
            try:
                task = detailed_tasks.get(source_bot) or case["input"]["task"]
                session = client.post(f"{backend_url}/api/sessions", json={}, timeout=10.0)
                session.raise_for_status()
                session_id = session.json()["session_id"]

                doc = client.post(
                    f"{backend_url}/api/documents/text",
                    json={"text": task, "session_id": session_id}, timeout=10.0,
                )
                doc.raise_for_status()

                done = _stream_turn(
                    client, backend_url, session_id, _RECOMMEND_TRIGGER, agent_version,
                )
                actions = _flatten_recommendation(done.get("recommendation"))
                predictions.append({
                    "source_bot": source_bot,
                    "predicted_actions": actions,
                    "predicted_action_count": len(actions),
                })
                if on_progress:
                    on_progress(f"[{i}/{len(cases)}] ✓ {source_bot}: 액션 {len(actions)}개 예측")
            except Exception as e:  # noqa: BLE001 — 케이스 하나 실패가 전체를 막지 않는다
                logger.warning("Workflow 케이스 실패: %s", source_bot, exc_info=True)
                predictions.append({
                    "source_bot": source_bot, "predicted_actions": [], "predicted_action_count": 0,
                })
                if on_progress:
                    on_progress(f"[{i}/{len(cases)}] ⚠ {source_bot} 오류: {e}")

    return predictions


def execute_and_save(agent_label: str, agent_version: str | None = None) -> None:
    """reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다. 라이브로 예측을
    만들어 predictions_from_agent_<agent_label>.json으로 저장한 뒤, 기존 pm4py/
    WorFBench 채점 스크립트를 그대로 돌린다(executor._run_script — 서브프로세스로
    a360-eval-sandbox/.venv-verify를 호출, 채점 로직 중복 없음).

    agent_version이 주어지면(v1/v2/v3) `/turn` 요청에 그대로 실어 보내 그 버전을
    강제한다 — 생략하면 Backend가 자기 기본 버전(env AGENT_VERSION, 없으면 v2)을
    쓴다. 이전에는 이 필드를 아예 안 보내서 항상 v2로만 평가되고 있었다."""
    try:
        _append_log(f"골드셋 로드 중... ({_GOLDSET_PATH.name})")
        cases = load_cases()
        state["cases"] = len(cases)
        version_note = f", agent_version={agent_version}" if agent_version else " (Backend 기본 버전)"
        _append_log(f"{len(cases)}개 케이스 — 라이브 예측 생성 시작{version_note}")

        predictions = generate_predictions(on_progress=_append_log, agent_version=agent_version)

        pred_path = executor.METADATA_DIR / f"predictions_from_agent_{agent_label}.json"
        pred_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
        _append_log(f"예측 파일 저장: {pred_path.name}")

        _append_log("pm4py 채점 실행 중...")
        executor._run_script(_PM4PY_SCRIPT, agent_label)
        _append_log("pm4py 채점 완료")

        _append_log("WorFBench 채점 실행 중...")
        executor._run_script(_WORFBENCH_SCRIPT, agent_label)
        _append_log("WorFBench 채점 완료")

        case_ids = {c["source_bot"] for c in cases}
        evaluation_id = uuid4().hex[:12]
        saved = executor._save_results(
            agent_label, evaluation_id, _DATASET_ID, _DATASET_VERSION, case_ids, agent_label, None,
        )
        _append_log(f"결과 저장 완료 — {saved}건")
        state.update({"saved": saved})
    except Exception as e:  # noqa: BLE001 — 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("Workflow 라이브 평가 실행 실패")
        state["error"] = str(e)
        _append_log(f"오류: {e}")
    finally:
        finish_state(state)
