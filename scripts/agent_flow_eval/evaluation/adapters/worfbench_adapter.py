from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

GOLDSET_ROOT = Path(__file__).resolve().parents[2]
if str(GOLDSET_ROOT) not in sys.path:
    sys.path.insert(0, str(GOLDSET_ROOT))
EVALUATION_ROOT = Path(__file__).resolve().parents[1]
if str(EVALUATION_ROOT) not in sys.path:
    sys.path.insert(0, str(EVALUATION_ROOT))

from action_filters import action_label, is_disabled_step, should_exclude_action  # noqa: E402
from action_matching import normalize_action_label  # noqa: E402

# core_task.py(core_only projection)는 재설계(2026-07-30)로 삭제됨. 이 파일의
# t_eval_nodes 경로는 이제 "외부 벤치마크 비교용"(WorFEval 원본 재현)으로만 쓰인다 -
# action_matching.py/action_chain.py가 액티브 지표.
#
# 아래 3개 함수는 pm4py_adapter.py에서 그대로 옮겨왔다(2026-08-03, PM4Py를 쓰지
# 않기로 확정하면서 그 파일과 convert_to_pm4py.py를 삭제함 - 원래도 이 유틸
# 함수들은 PM4Py 자체와 무관한 단순 라벨 정규화 로직이었다). action_matching.py의
# canonicalize_action()과는 일부러 분리해서 쓴다 - WorFEval은 원본 벤치마크
# 재현이 목적이라 action_equivalence_rules_conditional.json의 조건부 동치
# 규칙을 적용하지 않고 순수 plain map만 쓴다(§ score_worfbench_f1chain 문서
# 참고).


def load_action_equivalence_map(root: Path | None = None) -> dict[str, str]:
    path = (root or GOLDSET_ROOT) / "evaluation" / "action_equivalence_rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for group in payload.get("equivalence_groups", []) or []:
        canonical = group.get("canonical")
        if not canonical:
            continue
        mapping.setdefault(normalize_action_label(canonical), canonical)
        for member in group.get("members", []) or []:
            key = normalize_action_label(member)
            if key in mapping and mapping[key] != canonical:
                raise ValueError(f"Action equivalence member maps to multiple canonicals: {member}")
            mapping[key] = canonical
    return mapping


def _split_action_label(label: str) -> tuple[str, str]:
    if "." not in label:
        return label, ""
    return label.split(".", 1)


def _canonical_label(package: str | None, action: str | None, mapping: dict[str, str]) -> str:
    label = action_label(package, action)
    return mapping.get(normalize_action_label(label), label)


EDGE_RE = re.compile(r"\((START|END|\d+),(START|END|\d+)\)")
_SENTENCE_MODEL = None


def _assistant_content(record: dict[str, Any]) -> str:
    for turn in record.get("conversations", []) or []:
        if turn.get("role") == "assistant":
            return str(turn.get("content", ""))
    return ""


def parse_node_edges(record_path: Path) -> dict[str, Any]:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    content = _assistant_content(record)
    nodes: dict[str, str] = {}
    in_edges = False
    edges: list[tuple[str, str]] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("edges"):
            in_edges = True
            edges.extend(EDGE_RE.findall(line))
            continue
        if in_edges:
            edges.extend(EDGE_RE.findall(line))
            continue
        match = re.match(r"^(\d+)\s*[:.]\s*(.+)$", line)
        if match:
            nodes[match.group(1)] = match.group(2).strip()

    if not edges:
        edges = EDGE_RE.findall(content)

    return {
        "path": str(record_path),
        "exists": True,
        "record_id": record.get("id"),
        "fidelity": (record.get("meta") or {}).get("worfbench_fidelity"),
        "control_flow_types": (record.get("meta") or {}).get("control_flow_types", []),
        "nodes": nodes,
        "edges": edges,
    }


def _f1(gold_items: list[Any], pred_items: list[Any]) -> dict[str, Any]:
    gold = Counter(gold_items)
    pred = Counter(pred_items)
    overlap = sum((gold & pred).values())
    precision = overlap / sum(pred.values()) if pred else 0.0
    recall = overlap / sum(gold.values()) if gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "overlap": overlap,
        "gold_count": sum(gold.values()),
        "prediction_count": sum(pred.values()),
    }


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "a360-eval-sandbox").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_worfbench_src() -> Path:
    return default_workspace_root() / "a360-eval-sandbox" / "external" / "WorFBench"


def _import_worfbench():
    src = default_worfbench_src()
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from evaluator.graph_evaluator import t_eval_nodes  # type: ignore
    from sentence_transformers import SentenceTransformer  # type: ignore

    return t_eval_nodes, SentenceTransformer


def _sentence_model():
    global _SENTENCE_MODEL
    if _SENTENCE_MODEL is None:
        _, SentenceTransformer = _import_worfbench()
        _SENTENCE_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _SENTENCE_MODEL


def _canonical_action(package: str | None, action: str | None, mapping: dict[str, str]) -> dict[str, str] | None:
    if should_exclude_action(package, action):
        return None
    canonical = _canonical_label(package, action, mapping)
    canonical_package, canonical_action = _split_action_label(canonical)
    return {"package": canonical_package, "action": canonical_action}


def _flatten_all_branches(steps: list[dict[str, Any]], mapping: dict[str, str], found_types: set[str], excluded: list[str]) -> list[dict[str, str]]:
    """스텝 트리를 액션 한 줄로 펼친다. **분기는 하나도 버리지 않는다** -
    if의 본문과 elseIf/else를 전부, loop/container도 본문과 분기를 전부 넣는다
    (catch만 제외, try는 본문+finally). 정답 워크플로우가 3개 분기로 3가지
    경우를 처리한다면 봇도 3가지를 다 만들어야 하므로, 분기를 버리면 정답
    요구사항 자체가 사라진다.

    이 함수는 2026-08-04까지 `_canonical_path`라는 이름이었는데, "대표 경로
    하나로 접는다"는 뜻이라 실제 동작과 정반대였다(실측: 9개 케이스 모두
    메인 채점기의 전체 펼치기 결과와 액션 수가 정확히 일치). 같은 시점에
    `processing/convert_to_worfbench.py`의 쌍둥이 함수도 같은 규칙으로 맞췄다
    - 그쪽은 실제로 elseIf/else를 버려서 0376에서 20개 중 6개가 사라지고
    있었다(그 출력은 어떤 채점기도 쓰지 않아 점수 영향은 없었음).

    루프는 본문을 1회만 넣는다 - 반복 횟수는 실행 시점에 정해지므로 정적
    워크플로우에서 펼칠 수 없다. 이건 이 함수만의 규칙이 아니라 메인 채점기
    (`action_matching.flatten_scored_actions`)도 똑같이 하는 전체 공통 규칙이다."""
    actions: list[dict[str, str]] = []
    for step in steps:
        if is_disabled_step(step):
            excluded.append(action_label(step.get("package"), step.get("action")) or step.get("type", "disabled"))
            continue
        step_type = step.get("type")
        if step_type == "action":
            converted = _canonical_action(step.get("package"), step.get("action"), mapping)
            if converted is None:
                excluded.append(action_label(step.get("package"), step.get("action")))
                continue
            actions.append(converted)
            continue

        if step_type in {"if", "loop", "try", "trigger_loop"}:
            found_types.add(step_type)

        if step_type in {"container", "loop"}:
            actions.extend(_flatten_all_branches(step.get("steps", []) or [], mapping, found_types, excluded))
            for branch in step.get("branches", []) or []:
                actions.extend(_flatten_all_branches(branch.get("steps", []) or [], mapping, found_types, excluded))
        elif step_type == "if":
            actions.extend(_flatten_all_branches(step.get("steps", []) or [], mapping, found_types, excluded))
            for branch in step.get("branches", []) or []:
                actions.extend(_flatten_all_branches(branch.get("steps", []) or [], mapping, found_types, excluded))
        elif step_type == "trigger_loop":
            for branch in step.get("branches", []) or []:
                actions.extend(_flatten_all_branches(branch.get("steps", []) or [], mapping, found_types, excluded))
        elif step_type == "try":
            actions.extend(_flatten_all_branches(step.get("steps", []) or [], mapping, found_types, excluded))
            for branch in step.get("branches", []) or []:
                if branch.get("branch") == "finally":
                    actions.extend(_flatten_all_branches(branch.get("steps", []) or [], mapping, found_types, excluded))
        else:
            raise ValueError(f"Unknown normalized step type for WorFBench: {step_type!r}")
    return actions


def _graph_from_actions(actions: list[dict[str, str]]) -> dict[str, Any]:
    nodes = ["START"] + [f"{action['package']}.{action['action']}" for action in actions] + ["END"]
    if actions:
        edges = [(0, 1)] + [(idx, idx + 1) for idx in range(1, len(actions))] + [(len(actions), len(actions) + 1)]
    else:
        edges = [(0, 1)]
    return {"nodes": nodes, "edges": edges}


def score_worfbench_f1chain(
    gold_normalized_path: Path,
    prediction_normalized_path: Path,
    *,
    equivalence_root: Path | None = None,
) -> dict[str, Any]:
    """Run WorFBench's actual `t_eval_nodes` over canonicalized Node/Edges graphs.
    "외부 벤치마크 비교용"(원본 WorFEval 재현) - 액티브 지표는 action_matching.py/
    action_chain.py를 쓴다.

    _import_worfbench()는 외부 sibling 워크스페이스(a360-eval-sandbox/external/
    WorFBench)와 sentence_transformers 설치 여부에 의존한다 - 없는 환경에서는
    RuntimeError/ImportError를 던진다(Qodo 리뷰로 실측 확인: 2026-08-03, 이
    호출이 try/except 밖에 있어서 호출부 전체가 죽는 문제였음). WorFBench는
    어디까지나 "외부 참고" 경로라 이게 없다고 Rule-only/Judge-assisted 같은
    주 지표 산출까지 막으면 안 되므로, 이 함수 안의 try/except로 감싼다."""
    mapping = load_action_equivalence_map(equivalence_root)
    gold_payload = json.loads(gold_normalized_path.read_text(encoding="utf-8"))
    pred_payload = json.loads(prediction_normalized_path.read_text(encoding="utf-8"))

    gold_types: set[str] = set()
    pred_types: set[str] = set()
    excluded_gold: list[str] = []
    excluded_prediction: list[str] = []
    gold_actions = _flatten_all_branches(gold_payload.get("steps", []) or [], mapping, gold_types, excluded_gold)
    pred_actions = _flatten_all_branches(pred_payload.get("steps", []) or [], mapping, pred_types, excluded_prediction)

    result: dict[str, Any] = {
        "mode": "actual_worfbench_t_eval_nodes",
        "gold_normalized": str(gold_normalized_path),
        "prediction_normalized": str(prediction_normalized_path),
        "gold_action_count_after_preprocessing": len(gold_actions),
        "prediction_action_count_after_preprocessing": len(pred_actions),
        "gold_worfbench_fidelity": "exact" if not gold_types else "approximated",
        "prediction_worfbench_fidelity": "exact" if not pred_types else "approximated",
        "gold_control_flow_types": sorted(gold_types),
        "prediction_control_flow_types": sorted(pred_types),
        "excluded_gold_actions": excluded_gold,
        "excluded_prediction_actions": excluded_prediction,
        "action_equivalence_member_count": len(mapping),
    }
    if not pred_actions:
        result.update({"status": "empty_prediction", "precision": 0.0, "recall": 0.0, "f1_score": 0.0})
        return result

    try:
        t_eval_nodes, _ = _import_worfbench()
        scores = t_eval_nodes(_graph_from_actions(pred_actions), _graph_from_actions(gold_actions), _sentence_model())
        result.update({"status": "ok", **{key: round(float(value), 4) for key, value in scores.items()}})
    except Exception as exc:  # pragma: no cover - external library boundary
        result.update({"status": "worfbench_check_error", "error": f"{type(exc).__name__}: {exc}"})
    return result


def score_worfbench(gold_path: Path, pred_path: Path) -> dict[str, Any]:
    gold = parse_node_edges(gold_path)
    pred = parse_node_edges(pred_path)
    return {
        "gold": {k: v for k, v in gold.items() if k not in {"nodes", "edges"}},
        "prediction": {k: v for k, v in pred.items() if k not in {"nodes", "edges"}},
        "node_label_f1": _f1(list(gold["nodes"].values()), list(pred["nodes"].values())),
        "edge_f1": _f1(gold["edges"], pred["edges"]),
        "gold_node_count": len(gold["nodes"]),
        "prediction_node_count": len(pred["nodes"]),
        "gold_edge_count": len(gold["edges"]),
        "prediction_edge_count": len(pred["edges"]),
    }
