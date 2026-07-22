"""canonical 스텝 목록을 pm4py의 ProcessTree(워크플로우 실행 구조 모델)로 바꾼다.

pm4py는 프로세스를 SEQUENCE(순서대로)/XOR(둘 중 하나만)/LOOP(반복) 같은 연산자로
이루어진 트리로 표현한다. pm4py에는 "try/catch/finally" 연산자가 따로 없어서,
try는 SEQUENCE(XOR(try, catch), finally)로, loop는 3갈래(본문, 다시하기, 끝내기)
LOOP로 조합해서 표현한다. 이 조합 규칙은 실제 A360 워크플로우 특성과 pm4py
연산자 집합을 맞춰보고 정한 것이라 바꾸지 않았다.

scripts/agent_flow_eval/processing/convert_to_pm4py.py의 로직을 그대로 옮겼다.

pm4py 자체는 AGPL 라이선스라 이 프로젝트의 requirements.txt에 그냥 추가하지 않고,
기존 스크립트와 같은 방식으로 로컬에 미리 받아둔 pm4py 소스(a360-eval-sandbox/
external/pm4py)를 import 시점에 sys.path에 끼워 넣어서 쓴다.
"""

import sys
from pathlib import Path
from typing import Any

from .action_filters import is_browser_session_lifecycle_action, is_disabled_step

_pm4py_module: Any | None = None
_process_tree_operator: Any | None = None
_process_tree_class: Any | None = None


def _find_workspace_root() -> Path:
    """이 저장소(A360-Assistant-Ops)와 a360-eval-sandbox가 같이 들어있는
    상위 폴더를 찾는다. 두 저장소가 항상 같은 폴더 밑에 나란히 checkout되어
    있다는 전제(이 머신의 실제 구조와 동일)로 찾는다."""
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "a360-eval-sandbox").exists():
            return parent
    raise RuntimeError(
        "A360-Assistant-Ops와 a360-eval-sandbox가 같이 있는 상위 폴더를 찾지 못했습니다. "
        "pm4py 변환을 쓰려면 두 저장소가 나란히 checkout되어 있어야 합니다."
    )


def _import_pm4py() -> tuple[Any, Any, Any]:
    """pm4py 모듈을 최초 1회만 불러온다(불러오는 데 시간이 좀 걸린다)."""
    global _pm4py_module, _process_tree_operator, _process_tree_class
    if _pm4py_module is not None:
        return _pm4py_module, _process_tree_operator, _process_tree_class

    pm4py_source_dir = _find_workspace_root() / "a360-eval-sandbox" / "external" / "pm4py"
    if str(pm4py_source_dir) not in sys.path:
        sys.path.insert(0, str(pm4py_source_dir))

    import pm4py as pm4py_module
    from pm4py.objects.process_tree.obj import Operator as process_tree_operator
    from pm4py.objects.process_tree.obj import ProcessTree as process_tree_class

    _pm4py_module = pm4py_module
    _process_tree_operator = process_tree_operator
    _process_tree_class = process_tree_class
    return _pm4py_module, _process_tree_operator, _process_tree_class


def convert_canonical_steps_to_process_tree(canonical_steps: list[dict]) -> tuple[Any, dict]:
    """canonical 스텝 목록 전체를 pm4py ProcessTree 하나로 바꾼다.

    반환값은 (pm4py ProcessTree 객체, 사람이 읽을 수 있는 트리 dict) 둘 다다 —
    pnml/ptml로 내보낼 때는 앞의 것을, 화면에 보여주거나 JSON으로 저장할 때는
    뒤의 것을 쓰면 된다.
    """
    return _convert_step_list(canonical_steps, parent_tree_node=None)


def _make_silent_leaf(parent_tree_node) -> tuple[Any, dict]:
    """"여기서는 아무 일도 안 일어난다"는 뜻의 빈 노드(tau transition).

    스텝이 하나도 없는 분기(예: Comment만 있던 if-branch)나, RPA의 Loop 개념이
    구분하지 않는 pm4py LOOP의 "다시하기"/"끝내기" 자리를 채우는 데 쓴다.
    """
    _, _, process_tree_class = _import_pm4py()
    tree_node = process_tree_class(parent=parent_tree_node)
    tree_json = {"operator": None, "label": None, "children": []}
    return tree_node, tree_json


def _make_action_leaf(action_label: str, parent_tree_node) -> tuple[Any, dict]:
    _, _, process_tree_class = _import_pm4py()
    tree_node = process_tree_class(label=action_label, parent=parent_tree_node)
    tree_json = {"operator": None, "label": action_label, "children": []}
    return tree_node, tree_json


def _convert_step_list(steps: list[dict], parent_tree_node) -> tuple[Any, dict]:
    """스텝 목록 하나를 트리 노드 하나로 바꾼다. 스텝이 없으면 빈 노드, 하나면
    그 스텝 자체, 여러 개면 SEQUENCE(순서대로 실행)로 묶는다."""
    visible_steps = [
        step for step in steps
        if not is_disabled_step(step)
        and not (
            step.get("type") == "action"
            and is_browser_session_lifecycle_action(step.get("package"), step.get("action"))
        )
    ]

    if not visible_steps:
        return _make_silent_leaf(parent_tree_node)
    if len(visible_steps) == 1:
        return _convert_single_step(visible_steps[0], parent_tree_node)

    _, operator, process_tree_class = _import_pm4py()
    sequence_node = process_tree_class(operator=operator.SEQUENCE, parent=parent_tree_node)
    child_json_list = []
    for step in visible_steps:
        child_node, child_json = _convert_single_step(step, sequence_node)
        sequence_node.children.append(child_node)
        child_json_list.append(child_json)
    return sequence_node, {"operator": "sequence", "label": None, "children": child_json_list}


def _branches_by_name(step: dict) -> dict[str, dict]:
    return {branch.get("branch"): branch for branch in step.get("branches", []) or []}


def _convert_single_step(step: dict, parent_tree_node) -> tuple[Any, dict]:
    _, operator, process_tree_class = _import_pm4py()
    step_type = step["type"]

    if step_type == "action":
        action_label = f"{step['package']}.{step['action']}"
        return _make_action_leaf(action_label, parent_tree_node)

    if step_type == "container":
        # 인식 못 하는 package를 담은 컨테이너(예: HBCWorkflow)는 그냥 그 안의
        # 스텝들을 순서대로 실행하는 것으로 취급한다. branch가 있으면 우리가
        # 모르는 분기 구조라는 뜻이라 에러로 알린다.
        if step.get("branches"):
            raise ValueError(f"branch가 있는 미지원 컨테이너 package입니다: {step.get('package')!r}")
        return _convert_step_list(step.get("steps", []) or [], parent_tree_node)

    if step_type == "if":
        # then-branch와 elseIf/else 분기들은 실행 시점에 하나만 골라진다 ->
        # XOR(둘 중 정확히 하나).
        xor_node = process_tree_class(operator=operator.XOR, parent=parent_tree_node)
        alternative_step_lists = [step.get("steps", []) or []]
        for branch in step.get("branches", []) or []:
            alternative_step_lists.append(branch.get("steps", []) or [])

        child_json_list = []
        for alternative_steps in alternative_step_lists:
            child_node, child_json = _convert_step_list(alternative_steps, xor_node)
            xor_node.children.append(child_node)
            child_json_list.append(child_json)
        return xor_node, {"operator": "xor", "label": None, "children": child_json_list}

    if step_type == "loop":
        # pm4py의 LOOP는 항상 3갈래(본문, 다시하기, 끝내기)다. RPA의 Loop 개념은
        # "다시하기"와 "끝내기"를 구분하는 별도 액션이 없어서 둘 다 빈 노드로
        # 채운다 — 반복 횟수 자체는 구조 비교에 영향을 주지 않기 때문이다.
        loop_node = process_tree_class(operator=operator.LOOP, parent=parent_tree_node)
        body_node, body_json = _convert_step_list(step.get("steps", []) or [], loop_node)
        redo_node, redo_json = _make_silent_leaf(loop_node)
        exit_node, exit_json = _make_silent_leaf(loop_node)
        loop_node.children.extend([body_node, redo_node, exit_node])
        return loop_node, {"operator": "loop", "label": None, "children": [body_json, redo_json, exit_json]}

    if step_type == "trigger_loop":
        xor_node = process_tree_class(operator=operator.XOR, parent=parent_tree_node)
        child_json_list = []
        for branch in step.get("branches", []) or []:
            child_node, child_json = _convert_step_list(branch.get("steps", []) or [], xor_node)
            xor_node.children.append(child_node)
            child_json_list.append(child_json)
        if not xor_node.children:
            return _make_silent_leaf(parent_tree_node)
        return xor_node, {"operator": "xor", "label": None, "children": child_json_list}

    if step_type == "try":
        # finally는 항상 실행된다(성공하든 실패하든). catch는 try 본문이 끝까지
        # 실행되는 것의 대안(예외 발생 시)이라 XOR로 묶는다. finally는 그
        # XOR과 대등한 셋째 선택지가 아니라, 그 뒤에 항상 이어지는 것이므로
        # SEQUENCE(XOR(try, catch), finally) 형태로 감싼다.
        branches = _branches_by_name(step)
        has_catch = "catch" in branches
        has_finally = "finally" in branches

        outer_sequence_node = process_tree_class(operator=operator.SEQUENCE, parent=parent_tree_node) if has_finally else None
        core_parent_node = outer_sequence_node if outer_sequence_node is not None else parent_tree_node

        if has_catch:
            core_node = process_tree_class(operator=operator.XOR, parent=core_parent_node)
            try_child_node, try_json = _convert_step_list(step.get("steps", []) or [], core_node)
            catch_child_node, catch_json = _convert_step_list(branches["catch"].get("steps", []) or [], core_node)
            core_node.children.extend([try_child_node, catch_child_node])
            core_json = {"operator": "xor", "label": None, "children": [try_json, catch_json]}
        else:
            core_node, core_json = _convert_step_list(step.get("steps", []) or [], core_parent_node)

        if outer_sequence_node is None:
            return core_node, core_json

        finally_child_node, finally_json = _convert_step_list(branches["finally"].get("steps", []) or [], outer_sequence_node)
        outer_sequence_node.children.extend([core_node, finally_child_node])
        return outer_sequence_node, {"operator": "sequence", "label": None, "children": [core_json, finally_json]}

    raise ValueError(f"알 수 없는 canonical 스텝 타입: {step_type!r}")


def count_leaf_actions(tree_json: dict) -> int:
    """트리 안에 실제 액션(빈 노드가 아닌 leaf)이 몇 개인지 센다."""
    if tree_json["label"] is not None:
        return 1
    return sum(count_leaf_actions(child) for child in tree_json["children"])


def export_process_tree_files(process_tree, tree_json: dict, output_path_without_extension: Path) -> dict[str, Path]:
    """ProcessTree를 파일 3종류로 저장한다.

    - .pnml: 실제 채점 스크립트가 읽는 Petri net 형식
    - .ptml: pm4py 자체 트리 저장 형식
    - .tree.json: pm4py 없이도 아무 텍스트 편집기로 열어볼 수 있는 사람이 읽기 쉬운 버전
    """
    pm4py_module, _, _ = _import_pm4py()

    pnml_path = output_path_without_extension.with_suffix(".pnml")
    ptml_path = output_path_without_extension.with_suffix(".ptml")
    tree_json_path = Path(str(output_path_without_extension) + ".tree.json")

    petri_net, initial_marking, final_marking = pm4py_module.convert_to_petri_net(process_tree)
    pm4py_module.write_pnml(petri_net, initial_marking, final_marking, str(pnml_path))
    pm4py_module.write_ptml(process_tree, str(ptml_path))
    tree_json_path.write_text(_to_pretty_json(tree_json), encoding="utf-8")

    return {"pnml": pnml_path, "ptml": ptml_path, "tree_json": tree_json_path}


def _to_pretty_json(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)
