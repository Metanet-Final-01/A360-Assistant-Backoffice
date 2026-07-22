"""원본 A360 워크플로우 JSON을 "canonical" 형태(순서가 있는 steps 목록)로 바꾼다.

A360이 내보내는 워크플로우 JSON은 노드가 자식 노드를 품는 트리 구조다. pm4py나
WorFBench 같은 채점 도구에 넘기려면 그 트리를 "이 순서대로 이런 스텝들이 있다"는
평평한 형태로 정리해야 하는데, 그 정리 결과가 canonical 형태다.

scripts/agent_flow_eval/processing/normalize_extracted_workflows.py의
normalize_workflow() 로직을 그대로 옮겼다 — 어떤 package를 "그냥 통과시키는 껍데기"로
볼지(TRANSPARENT_PACKAGE_NAMES), 어떤 걸 완전히 버릴지(SKIPPED_PACKAGE_NAMES), if/try/
loop을 어떤 모양으로 정리할지는 실제 A360 워크플로우 파일들을 직접 읽고 정한 규칙이라
바꾸지 않았다.
"""

# 'Step'은 그냥 다른 스텝들을 묶어두는 껍데기라서, 이 안의 자식들을 그대로 펼친다.
TRANSPARENT_PACKAGE_NAMES = frozenset({"Step"})

# 'Comment'는 실행되는 게 아니라서 canonical 형태에 아예 남기지 않는다.
SKIPPED_PACKAGE_NAMES = frozenset({"Comment"})

IF_PACKAGE_NAMES = frozenset({"If"})
TRY_PACKAGE_NAMES = frozenset({"ErrorHandler"})
LOOP_PACKAGE_NAMES = frozenset({"Loop"})

# 'TriggerLoop'은 자기 스텝이 없고 branch(분기)만 있다.
BRANCH_ONLY_LOOP_PACKAGE_NAMES = frozenset({"TriggerLoop"})


def normalize_workflow(raw_workflow: dict, source_file_name: str) -> dict:
    """원본 워크플로우 JSON(raw_workflow) 하나를 canonical 형태로 바꾼다."""
    return {
        "source_file": source_file_name,
        "triggers": raw_workflow.get("triggers", []),
        "steps": convert_node_list(raw_workflow.get("nodes", []) or []),
    }


def convert_node_list(nodes: list[dict]) -> list[dict]:
    """원본 노드 목록을 canonical 스텝 목록으로 바꾼다. 노드 하나가 스텝 여러 개가
    되거나(예: 'Step' 껍데기 펼치기) 아예 사라질 수 있어서(예: Comment 제거),
    결과를 한 번에 이어 붙인다."""
    steps: list[dict] = []
    for node in nodes:
        steps.extend(convert_single_node(node))
    return steps


def convert_single_node(node: dict) -> list[dict]:
    """원본 노드 하나를 canonical 스텝 0개 이상으로 바꾼다."""
    package_name = node.get("packageName")

    if package_name in SKIPPED_PACKAGE_NAMES:
        return []

    if package_name in TRANSPARENT_PACKAGE_NAMES:
        child_nodes = node.get("children", []) or []
        return convert_node_list(child_nodes)

    common_fields = {
        "uid": node.get("uid"),
        "disabled": bool(node.get("disabled", False)),
        "attributes": node.get("attributes", []),
        **_extra_binding_fields(node),
    }

    if package_name in IF_PACKAGE_NAMES:
        return [{
            "type": "if",
            **common_fields,
            "steps": convert_node_list(node.get("children", []) or []),
            "branches": [_convert_branch(branch) for branch in node.get("branches", []) or []],
        }]

    if package_name in TRY_PACKAGE_NAMES:
        return [{
            "type": "try",
            **common_fields,
            "steps": convert_node_list(node.get("children", []) or []),
            "branches": [_convert_branch(branch) for branch in node.get("branches", []) or []],
        }]

    if package_name in LOOP_PACKAGE_NAMES:
        return [{
            "type": "loop",
            **common_fields,
            "steps": convert_node_list(node.get("children", []) or []),
        }]

    if package_name in BRANCH_ONLY_LOOP_PACKAGE_NAMES:
        return [{
            "type": "trigger_loop",
            **common_fields,
            "branches": [_convert_branch(branch) for branch in node.get("branches", []) or []],
        }]

    has_nested_content = bool(node.get("children") or node.get("branches"))
    if has_nested_content:
        return [{
            "type": "container",
            "package": package_name,
            "action": node.get("commandName"),
            **common_fields,
            "steps": convert_node_list(node.get("children", []) or []),
            "branches": [_convert_branch(branch) for branch in node.get("branches", []) or []],
        }]

    return [{
        "type": "action",
        "package": package_name,
        "action": node.get("commandName"),
        **common_fields,
    }]


def _convert_branch(branch: dict) -> dict:
    return {
        "branch": branch.get("commandName"),
        "attributes": branch.get("attributes", []),
        **_extra_binding_fields(branch),
        "steps": convert_node_list(branch.get("children", []) or []),
    }


def _extra_binding_fields(node: dict) -> dict:
    extra_fields: dict = {}
    if "returnTo" in node:
        extra_fields["return_to"] = node["returnTo"]
    if "returns" in node:
        extra_fields["returns"] = node["returns"]
    return extra_fields


def count_steps(steps: list[dict]) -> int:
    """canonical 스텝 목록 안에 있는 스텝 개수를 센다(branch 안까지 전부 포함)."""
    total = 0
    for step in steps:
        total += 1
        total += count_steps(step.get("steps", []) or [])
        for branch in step.get("branches", []) or []:
            total += count_steps(branch.get("steps", []) or [])
    return total
