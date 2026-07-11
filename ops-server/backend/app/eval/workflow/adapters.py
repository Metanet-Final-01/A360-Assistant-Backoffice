"""agent가 만든 추천안(Recommendation)을 pm4py/WorFBench 채점이 요구하는 입력
형식으로 변환한다.

- pm4py: package/action 순서 목록 하나만 있으면 된다 (run_pm4py_conformance.py 참고).
- WorFBench: "Node: ... Edges: ..." 그래프 형식의 대화 + package.action 메타데이터가
  필요하다 (format_examples/worfbench/pred_traj_example.json 참고). 시스템 프롬프트와
  응답 문법은 그 실제 예시에서 그대로 옮겨왔다 — 임의로 새로 지어내지 않았다.

두 변환 함수 모두 결과를 format_schemas.py로 검증한 뒤 돌려준다: 변환기가 스스로
깨진 형식을 만들어내면 여기서 바로 걸린다.

Recommendation은 Loop/If/Step 같은 컨테이너 액션을 children으로 감싸는 트리 구조다.
pm4py 정답 Petri net은 이 컨테이너들을 제어 구조로만 보고 리프로 취급하지 않지만
(run_pm4py_conformance.py가 채점 직전에 걸러낸다), WorFBench 골드셋과 여기 변환기는
컨테이너 액션 자체도 하나의 노드/스텝으로 센다 — goldset_from_bots.json도 동일하게
컨테이너를 리스트에 포함한다(run_pm4py_conformance.py 모듈 docstring 참고). 그래서
flatten은 트리를 order 순으로 그냥 죽 펴기만 하고, 어느 쪽이 컨테이너인지는 신경 쓰지
않는다 — 필터링은 pm4py 채점 스크립트 쪽 책임이다.
"""

import json
from pathlib import Path

from app.eval.format_schemas import PM4pyPredictedActions, WorfbenchPredTrajEntry
from app.eval.workflow.recommendation import Recommendation, RecommendedAction
from app.eval.workflow.validate_catalog_refs import load_catalog

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PACKAGES_JSON = _BACKEND_ROOT / "data" / "ingest" / "packages.json"

# format_examples/worfbench/pred_traj_example.json에서 그대로 옮긴 시스템 프롬프트 —
# WorFBench 채점기가 assistant 응답을 Node/Edges 문법으로 파싱하므로 문구를 바꾸면 안 된다.
_WORFBENCH_SYSTEM_PROMPT = (
    "You are a helpful and intelligent task planner, and your target is to decompose "
    "the assigned task into multiple subtasks for task completion and analyze the "
    "precedence relationships among subtasks.\nAt the beginning of your interactions, "
    "you will be given the task description and actions list you can take to finish "
    "the task, and you should decompose the given task into subtasks that can be "
    "accomplished using the provided actions or APIs. And then, you should analyze the "
    "precedence relationships among these subtasks, ensuring that each subtask is "
    "sequenced correctly relative to others. Based on the analysis, you should construct "
    'a workflow consisting of the identified subtasks to complete the task. You should '
    'use "Node: \\n1. <subtask 1>\\n2. <subtask 2>" to denote subtasks, and use (x,y) to '
    "denote that <subtask x> is a predecessor of <subtask y>, (START,x) to indicate the "
    "beginning with <subtask x>, and (x,END) to signify the conclusion with <subtask x>. "
    "Remember that x, y are numbers.\nYour response should use the following format:\n\n"
    "Node:\n1.<subtask 1>\n2.<subtask 2>\n...\nEdges:(START,1) ... (n,END)"
)


class MissingCatalogError(RuntimeError):
    """data/ingest/packages.json이 없을 때 — RAG 적재를 먼저 돌려야 한다."""


def flatten_recommendation(rec: Recommendation) -> list[RecommendedAction]:
    """트리를 order 순 실행 순서로 편다. 컨테이너 액션(Loop/If/Step 등) 자체도
    그대로 목록에 포함된다 — 걸러내지 않는다(모듈 docstring 참고)."""

    def walk(actions: list[RecommendedAction]) -> list[RecommendedAction]:
        flat: list[RecommendedAction] = []
        for a in sorted(actions, key=lambda x: x.order):
            flat.append(a)
            flat.extend(walk(a.children))
        return flat

    flat: list[RecommendedAction] = []
    for step in rec.steps:
        flat.extend(walk(step.actions))
    return flat


def to_pm4py_predicted_actions(rec: Recommendation, source_bot: str) -> dict:
    """pm4py 채점 입력(predictions_from_agent_*.json의 레코드 한 건과 같은 형식)."""
    actions = flatten_recommendation(rec)
    payload = {
        "source_bot": source_bot,
        "predicted_actions": [{"package": a.package, "action": a.action} for a in actions],
        "predicted_action_count": len(actions),
    }
    PM4pyPredictedActions.model_validate(payload)
    return payload


def _load_action_catalog() -> dict[str, dict]:
    """package -> {action_name: {description, parameters}}. packages.json(RAG 적재
    산출물, data/ingest/ 아래)이 파라미터 스키마까지 가진 원본이라 우선 쓴다 — 아직
    로컬에서 RAG 적재를 한 번도 안 돌렸으면 없을 수 있어 그때는 명확히 에러를 낸다
    (설명 없는 api_list를 만들어 조용히 품질을 낮추지 않는다)."""
    if not _PACKAGES_JSON.exists():
        raise MissingCatalogError(
            f"{_PACKAGES_JSON}가 없습니다 — WorFBench 입력을 만들려면 파라미터 설명이 "
            "포함된 패키지 카탈로그가 필요합니다. 먼저 RAG 적재(Ops 웹의 'RAG 적재' "
            "메뉴 또는 `python -m app.rag.pipeline`)를 한 번 실행하세요."
        )
    packages = json.loads(_PACKAGES_JSON.read_text(encoding="utf-8"))
    catalog: dict[str, dict] = {}
    for pkg in packages:
        catalog[pkg["package_name"]] = {
            action["name"]: {
                "description": action.get("description", ""),
                "parameters": [
                    {
                        "name": p["name"],
                        "type": p.get("type", ""),
                        "required": bool(p.get("required", False)),
                    }
                    for p in action.get("parameters", [])
                ],
            }
            for action in pkg.get("actions", [])
        }
    return catalog


def _build_api_list(actions: list[RecommendedAction], action_catalog: dict[str, dict]) -> list[dict]:
    """실제로 쓰인 액션만 api_list에 담는다(원본 예시처럼 카탈로그 전체를 다 넣지
    않음 — 프롬프트 크기를 실제 사용 범위로 제한)."""
    api_list: list[dict] = []
    seen: set[str] = set()
    for a in actions:
        key = f"{a.package}.{a.action}"
        if key in seen:
            continue
        seen.add(key)
        info = action_catalog.get(a.package, {}).get(a.action)
        api_list.append(
            {
                "name": key,
                "description": info["description"] if info else "",
                "parameters": info["parameters"] if info else [],
            }
        )
    return api_list


def to_worfbench_pred_traj(rec: Recommendation, source_bot: str, task_description: str, source_id: str) -> dict:
    """WorFBench 채점 입력(pred_traj_*.json의 레코드 한 건과 같은 형식).

    노드 라벨은 RecommendedAction.label(사람용 라벨)을 쓰고, 없으면 "package.action"으로
    대신한다. 실행 순서를 그대로 선형 체인(1→2→...→n)으로 표현한다 — 분기(If/Else)의
    병렬 브랜치까지 그래프로 표현하려면 A360 트리 구조(recommendation.py 참고)를 추가
    분석해야 하는데, WorFBench 골드셋 자체도 컨테이너 액션을 리스트에 그대로 펴서
    담아(run_pm4py_conformance.py docstring 참고) 이 프로젝트에서 별도 분기 그래프를
    만든 적이 없다 — 그 방식을 그대로 따른다.
    """
    actions = flatten_recommendation(rec)
    if not actions:
        raise ValueError("Recommendation에 액션이 하나도 없습니다")

    catalog = load_catalog()  # package -> set(action_name), catalog_actions.json 기반
    action_catalog = _load_action_catalog()  # package -> action -> {description, parameters}

    node_lines = [f"{i}: {a.label or f'{a.package}.{a.action}'}" for i, a in enumerate(actions, start=1)]
    edge_pairs = ["(START,1)"] + [f"({i},{i + 1})" for i in range(1, len(actions))] + [f"({len(actions)},END)"]
    assistant_content = "Node:\n" + "\n".join(node_lines) + "\nEdges:\n" + " ".join(edge_pairs)

    api_list = _build_api_list(actions, action_catalog)
    user_content = f"Task: {task_description}\napi_list:{json.dumps(api_list, ensure_ascii=False)}"

    packages = sorted({a.package for a in actions})
    payload = {
        "query": {
            "source": "a360_rpa",
            "id": source_id,
            "conversations": [
                {"role": "system", "content": _WORFBENCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": assistant_content},
            ],
            "meta": {
                "source_bot": source_bot,
                "packages": packages,
                "packages_in_catalog": [p for p in packages if p in catalog],
                "actions": [
                    {"package": a.package, "action": a.action, "in_catalog": a.action in catalog.get(a.package, set())}
                    for a in actions
                ],
            },
        }
    }
    WorfbenchPredTrajEntry.model_validate(payload)
    return payload
