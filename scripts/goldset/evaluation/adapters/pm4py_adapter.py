from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

GOLDSET_ROOT = Path(__file__).resolve().parents[2]
if str(GOLDSET_ROOT) not in sys.path:
    sys.path.insert(0, str(GOLDSET_ROOT))

from action_filters import action_label, is_browser_session_lifecycle_action  # noqa: E402


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "a360-eval-sandbox").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_pm4py_src() -> Path:
    return default_workspace_root() / "a360-eval-sandbox" / "external" / "pm4py"


def _import_pm4py():
    pm4py_src = default_pm4py_src()
    if str(pm4py_src) not in sys.path:
        sys.path.insert(0, str(pm4py_src))
    import pm4py  # type: ignore
    from pm4py.objects.log.obj import Event, EventLog, Trace  # type: ignore

    return pm4py, EventLog, Trace, Event


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_tree_json(node: dict[str, Any], labels: list[str], operators: Counter[str]) -> None:
    label = node.get("label")
    operator = node.get("operator")
    if label is not None:
        labels.append(str(label))
    if operator is not None:
        operators[str(operator)] += 1
    for child in node.get("children", []) or []:
        _walk_tree_json(child, labels, operators)


def summarize_tree_json(path: Path) -> dict[str, Any]:
    tree = json.loads(path.read_text(encoding="utf-8"))
    labels: list[str] = []
    operators: Counter[str] = Counter()
    _walk_tree_json(tree, labels, operators)
    return {
        "path": str(path),
        "exists": True,
        "sha256": sha256_file(path),
        "leaf_count": len(labels),
        "labels": labels,
        "operator_counts": dict(sorted(operators.items())),
    }


def summarize_pnml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return result

    result["sha256"] = sha256_file(path)
    try:
        pm4py, _, _, _ = _import_pm4py()

        net, initial_marking, final_marking = pm4py.read_pnml(str(path))
        result.update(
            {
                "readable_by_pm4py": True,
                "places": len(net.places),
                "transitions": len(net.transitions),
                "visible_transitions": sum(1 for transition in net.transitions if transition.label),
                "arcs": len(net.arcs),
                "initial_marking_size": len(initial_marking),
                "final_marking_size": len(final_marking),
            }
        )
    except Exception as exc:  # pragma: no cover - external library boundary
        result.update({"readable_by_pm4py": False, "error": f"{type(exc).__name__}: {exc}"})
    return result


CONTROL_FLOW_MARKER_PACKAGES = {"if", "loop", "error handler", "errorhandler"}
CONTROL_FLOW_MARKER_ACTION_RE = ("if", "loop", "try", "catch", "finally", "errorhandler")


def _is_control_flow_marker_action(package: str | None, action: str | None) -> bool:
    package_norm = (package or "").strip().lower()
    action_norm = (action or "").strip().lower()
    if package_norm not in CONTROL_FLOW_MARKER_PACKAGES:
        return False
    return any(token in action_norm for token in CONTROL_FLOW_MARKER_ACTION_RE)


def load_action_equivalence_map(root: Path | None = None) -> dict[str, str]:
    path = (root or GOLDSET_ROOT) / "evaluation" / "action_equivalence_rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for group in payload.get("equivalence_groups", []) or []:
        canonical = group.get("canonical")
        if not canonical:
            continue
        mapping.setdefault(canonical, canonical)
        for member in group.get("members", []) or []:
            if member in mapping and mapping[member] != canonical:
                raise ValueError(f"Action equivalence member maps to multiple canonicals: {member}")
            mapping[member] = canonical
    return mapping


def _split_action_label(label: str) -> tuple[str, str]:
    if "." not in label:
        return label, ""
    return label.split(".", 1)


def _canonical_label(package: str | None, action: str | None, mapping: dict[str, str]) -> str:
    return mapping.get(action_label(package, action), action_label(package, action))


def _transform_step(step: dict[str, Any], mapping: dict[str, str], excluded: list[str]) -> dict[str, Any] | None:
    step_type = step.get("type")
    if step_type == "action":
        package = step.get("package")
        action = step.get("action")
        label = action_label(package, action)
        if is_browser_session_lifecycle_action(package, action) or _is_control_flow_marker_action(package, action):
            excluded.append(label)
            return None
        canonical_package, canonical_action = _split_action_label(_canonical_label(package, action, mapping))
        copied = dict(step)
        copied["package"] = canonical_package
        copied["action"] = canonical_action
        return copied

    copied = dict(step)
    if step_type in {"container", "if", "loop", "trigger_loop", "try"}:
        copied["steps"] = _transform_steps(step.get("steps", []) or [], mapping, excluded)
        if step.get("branches") is not None:
            branches = []
            for branch in step.get("branches", []) or []:
                branch_copy = dict(branch)
                branch_copy["steps"] = _transform_steps(branch.get("steps", []) or [], mapping, excluded)
                branches.append(branch_copy)
            copied["branches"] = branches
        return copied

    raise ValueError(f"Unknown normalized step type for PM4Py conformance: {step_type!r}")


def _transform_steps(steps: list[dict[str, Any]], mapping: dict[str, str], excluded: list[str]) -> list[dict[str, Any]]:
    transformed: list[dict[str, Any]] = []
    for step in steps:
        converted = _transform_step(step, mapping, excluded)
        if converted is not None:
            transformed.append(converted)
    return transformed


def _flatten_action_labels(steps: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for step in steps:
        step_type = step.get("type")
        if step_type == "action":
            labels.append(action_label(step.get("package"), step.get("action")))
        elif step_type in {"container", "if", "loop", "trigger_loop", "try"}:
            labels.extend(_flatten_action_labels(step.get("steps", []) or []))
            for branch in step.get("branches", []) or []:
                labels.extend(_flatten_action_labels(branch.get("steps", []) or []))
        else:
            raise ValueError(f"Unknown normalized step type for flattening: {step_type!r}")
    return labels


def _convert_steps_to_pm4py_tree(steps: list[dict[str, Any]]):
    processing_root = GOLDSET_ROOT / "processing"
    if str(processing_root) not in sys.path:
        sys.path.insert(0, str(processing_root))
    from convert_to_pm4py import convert_steps  # type: ignore

    return convert_steps(steps, None)


def score_pm4py_conformance(
    gold_normalized_path: Path,
    prediction_normalized_path: Path,
    *,
    equivalence_root: Path | None = None,
) -> dict[str, Any]:
    """Run actual PM4Py alignment fitness/precision on canonicalized artifacts.

    The gold side is converted to a PM4Py process model. The prediction side is a
    single event-log trace. Both sides first apply the human-confirmed action
    equivalence rules plus scoring-only exclusions for browser sessions and visible
    control-flow marker actions.
    """
    pm4py, EventLog, Trace, Event = _import_pm4py()
    mapping = load_action_equivalence_map(equivalence_root)
    gold_payload = json.loads(gold_normalized_path.read_text(encoding="utf-8"))
    pred_payload = json.loads(prediction_normalized_path.read_text(encoding="utf-8"))

    excluded_gold: list[str] = []
    excluded_prediction: list[str] = []
    gold_steps = _transform_steps(gold_payload.get("steps", []) or [], mapping, excluded_gold)
    pred_steps = _transform_steps(pred_payload.get("steps", []) or [], mapping, excluded_prediction)
    prediction_labels = _flatten_action_labels(pred_steps)

    result: dict[str, Any] = {
        "mode": "actual_pm4py_alignment_conformance",
        "gold_normalized": str(gold_normalized_path),
        "prediction_normalized": str(prediction_normalized_path),
        "gold_action_count_after_preprocessing": len(_flatten_action_labels(gold_steps)),
        "prediction_action_count_after_preprocessing": len(prediction_labels),
        "excluded_gold_actions": excluded_gold,
        "excluded_prediction_actions": excluded_prediction,
        "action_equivalence_member_count": len(mapping),
    }

    if not prediction_labels:
        result.update({"status": "empty_prediction", "fitness": 0.0, "precision": 0.0})
        return result

    try:
        tree, _tree_json = _convert_steps_to_pm4py_tree(gold_steps)
        net, initial_marking, final_marking = pm4py.convert_to_petri_net(tree)

        log = EventLog()
        trace = Trace()
        for label in prediction_labels:
            trace.append(Event({"concept:name": label}))
        log.append(trace)

        fitness = pm4py.fitness_alignments(log, net, initial_marking, final_marking)
        precision = pm4py.precision_alignments(log, net, initial_marking, final_marking)
        result.update(
            {
                "status": "ok",
                "fitness": round(fitness.get("averageFitness", fitness.get("average_trace_fitness", 0.0)), 4),
                "precision": round(float(precision), 4),
                "raw_fitness": fitness,
            }
        )
    except Exception as exc:  # pragma: no cover - external library boundary
        result.update({"status": "conformance_check_error", "error": f"{type(exc).__name__}: {exc}"})
    return result


def compare_pm4py_artifacts(gold_dir: Path, pred_dir: Path) -> dict[str, Any]:
    gold_tree = next(gold_dir.glob("*.tree.json"), None)
    pred_tree = next(pred_dir.glob("*.tree.json"), None)
    gold_pnml = next(gold_dir.glob("*.pnml"), None)
    pred_pnml = next(pred_dir.glob("*.pnml"), None)

    result: dict[str, Any] = {
        "gold_dir": str(gold_dir),
        "prediction_dir": str(pred_dir),
        "gold_tree": summarize_tree_json(gold_tree) if gold_tree else {"exists": False},
        "prediction_tree": summarize_tree_json(pred_tree) if pred_tree else {"exists": False},
        "gold_pnml": summarize_pnml(gold_pnml) if gold_pnml else {"exists": False},
        "prediction_pnml": summarize_pnml(pred_pnml) if pred_pnml else {"exists": False},
    }

    gold_labels = result["gold_tree"].get("labels", [])
    pred_labels = result["prediction_tree"].get("labels", [])
    result["tree_leaf_count_delta"] = len(pred_labels) - len(gold_labels)
    result["tree_hash_equal"] = result["gold_tree"].get("sha256") == result["prediction_tree"].get("sha256")
    result["pnml_hash_equal"] = result["gold_pnml"].get("sha256") == result["prediction_pnml"].get("sha256")
    return result
