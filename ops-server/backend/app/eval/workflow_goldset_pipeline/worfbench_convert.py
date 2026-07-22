"""canonical 스텝 목록을 WorFBench 채점 형식(Node/Edges 텍스트)으로 바꾼다.

WorFBench의 f1chain 채점 방식은 "분기 없는 한 줄 순서(DAG)"만 다룰 수 있다. 그런데
실제 워크플로우에는 if/loop/try 같은 분기가 흔하다. 그래서 분기를 하나의 대표
경로로 뭉개서(if는 then-branch만, loop는 몸통 한 번만, try는 try+finally만 사용)
억지로 한 줄 순서로 만든다 — 이건 "정확한 채점"이 아니라 "그렇게라도 안 하면 아예
채점을 못 하니까 쓰는 근사치"라는 뜻이고, 그래서 결과에 worfbench_fidelity를 같이
남겨서 "이 결과가 근사치였는지" 항상 알 수 있게 한다.

scripts/agent_flow_eval/processing/convert_to_worfbench.py의 로직을 그대로 옮겼다.
"""

from .action_filters import is_browser_session_lifecycle_action, is_disabled_step

# WorFBench 채점기가 정확히 이 문구를 기대하기 때문에 자유롭게 바꿀 수 없다.
# (ops-server/backend/app/eval/workflow/adapters.py의 to_worfbench_pred_traj와 동일한 문구)
WORFBENCH_SYSTEM_PROMPT = (
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

# 분기를 근사치로 뭉갤 때 실제로 뭉갠 스텝 타입들. 여기 하나라도 걸리면
# worfbench_fidelity가 "approximated"가 된다.
CONTROL_FLOW_STEP_TYPES = {"if", "loop", "try", "trigger_loop"}


def build_worfbench_record(canonical_steps: list[dict], source_file_name: str, record_id: str) -> dict:
    """canonical 스텝 목록 하나를 WorFBench 학습/채점 데이터 한 건(record)으로 바꾼다."""
    control_flow_types_seen: set[str] = set()
    actions = _flatten_to_one_representative_path(canonical_steps, control_flow_types_seen)

    fidelity = "exact" if not control_flow_types_seen else "approximated"
    node_edges_text = _build_node_edges_text(actions)

    return {
        "source": "a360_rpa_goldset",
        "id": record_id,
        "conversations": [
            {"role": "system", "content": WORFBENCH_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {source_file_name}"},
            {"role": "assistant", "content": node_edges_text},
        ],
        "meta": {
            "worfbench_fidelity": fidelity,
            "control_flow_types": sorted(control_flow_types_seen),
            "actions": actions,
        },
    }


def _flatten_to_one_representative_path(steps: list[dict], control_flow_types_seen: set[str]) -> list[dict]:
    """분기가 있는 canonical 스텝 목록을, 대표 경로 하나만 남긴 액션 목록으로 뭉갠다.

    이건 "진짜 정확한 변환"이 아니라 진단용 근사치다 — WorFBench가 아예 다루지
    못하는 구조를 억지로 한 줄로 펴는 것뿐이다. 뭉갠 스텝 타입은 전부
    control_flow_types_seen에 기록해서, 호출한 쪽이 이 결과가 근사치인지 알 수
    있게 한다.
    """
    actions: list[dict] = []
    for step in steps:
        if is_disabled_step(step):
            continue

        step_type = step["type"]

        if step_type == "action":
            if is_browser_session_lifecycle_action(step.get("package"), step.get("action")):
                continue
            actions.append({"package": step["package"], "action": step["action"]})
            continue

        if step_type in CONTROL_FLOW_STEP_TYPES:
            control_flow_types_seen.add(step_type)

        if step_type in ("if", "loop", "container"):
            actions.extend(_flatten_to_one_representative_path(step.get("steps", []) or [], control_flow_types_seen))
        elif step_type == "trigger_loop":
            branches = step.get("branches", []) or []
            if branches:
                first_branch_steps = branches[0].get("steps", []) or []
                actions.extend(_flatten_to_one_representative_path(first_branch_steps, control_flow_types_seen))
        elif step_type == "try":
            actions.extend(_flatten_to_one_representative_path(step.get("steps", []) or [], control_flow_types_seen))
            for branch in step.get("branches", []) or []:
                if branch.get("branch") == "finally":
                    finally_steps = branch.get("steps", []) or []
                    actions.extend(_flatten_to_one_representative_path(finally_steps, control_flow_types_seen))
        else:
            raise ValueError(f"알 수 없는 canonical 스텝 타입: {step_type!r}")

    return actions


def _build_node_edges_text(actions: list[dict]) -> str:
    node_lines = [f"{index}: {action['package']}.{action['action']}" for index, action in enumerate(actions, start=1)]

    if not actions:
        edge_pairs = ["(START,END)"]
    else:
        middle_edges = [f"({index},{index + 1})" for index in range(1, len(actions))]
        edge_pairs = ["(START,1)", *middle_edges, f"({len(actions)},END)"]

    return "Node:\n" + "\n".join(node_lines) + "\nEdges:\n" + " ".join(edge_pairs)
