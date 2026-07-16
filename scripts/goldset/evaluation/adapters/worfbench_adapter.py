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

from action_filters import action_label, is_browser_session_lifecycle_action  # noqa: E402
from adapters.pm4py_adapter import (  # noqa: E402
    _is_control_flow_marker_action,
    _split_action_label,
    load_action_equivalence_map,
)


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
    if is_browser_session_lifecycle_action(package, action) or _is_control_flow_marker_action(package, action):
        return None
    canonical = mapping.get(action_label(package, action), action_label(package, action))
    canonical_package, canonical_action = _split_action_label(canonical)
    return {"package": canonical_package, "action": canonical_action}


def _canonical_path(steps: list[dict[str, Any]], mapping: dict[str, str], found_types: set[str], excluded: list[str]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for step in steps:
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

        if step_type in {"container", "if", "loop"}:
            actions.extend(_canonical_path(step.get("steps", []) or [], mapping, found_types, excluded))
        elif step_type == "trigger_loop":
            branches = step.get("branches", []) or []
            if branches:
                actions.extend(_canonical_path(branches[0].get("steps", []) or [], mapping, found_types, excluded))
        elif step_type == "try":
            actions.extend(_canonical_path(step.get("steps", []) or [], mapping, found_types, excluded))
            for branch in step.get("branches", []) or []:
                if branch.get("branch") == "finally":
                    actions.extend(_canonical_path(branch.get("steps", []) or [], mapping, found_types, excluded))
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
    """Run WorFBench's actual `t_eval_nodes` over canonicalized Node/Edges graphs."""
    t_eval_nodes, _ = _import_worfbench()
    mapping = load_action_equivalence_map(equivalence_root)
    gold_payload = json.loads(gold_normalized_path.read_text(encoding="utf-8"))
    pred_payload = json.loads(prediction_normalized_path.read_text(encoding="utf-8"))

    gold_types: set[str] = set()
    pred_types: set[str] = set()
    excluded_gold: list[str] = []
    excluded_prediction: list[str] = []
    gold_actions = _canonical_path(gold_payload.get("steps", []) or [], mapping, gold_types, excluded_gold)
    pred_actions = _canonical_path(pred_payload.get("steps", []) or [], mapping, pred_types, excluded_prediction)
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
