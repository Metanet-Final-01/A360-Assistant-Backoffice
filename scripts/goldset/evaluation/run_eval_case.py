from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from action_filters import action_label, is_browser_session_lifecycle_action, is_disabled_step
from adapters.pm4py_adapter import compare_pm4py_artifacts, score_pm4py_conformance
from adapters.worfbench_adapter import score_worfbench, score_worfbench_f1chain


@dataclass(frozen=True)
class Paths:
    root: Path
    case_id: str
    run_id: str
    gold_normalized: Path
    pred_normalized: Path
    gold_pm4py_dir: Path
    pred_pm4py_dir: Path
    gold_worfbench: Path
    pred_worfbench: Path
    report_dir: Path


def goldset_root() -> Path:
    return Path(__file__).resolve().parents[1]


def only_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def resolve_paths(case_id: str, run_id: str) -> Paths:
    root = goldset_root()
    gold_norm_dir = root / "eval_inputs" / "normalized_workflows_13" / case_id
    pred_norm_dir = root / "runner" / "logs" / run_id / "converted_recommendation" / "normalized"
    gold_worf_dir = root / "eval_inputs" / "worfbench_13" / case_id
    pred_worf_dir = root / "runner" / "logs" / run_id / "converted_recommendation" / "worfbench"

    paths = Paths(
        root=root,
        case_id=case_id,
        run_id=run_id,
        gold_normalized=only_file(gold_norm_dir, "*.goldset.json"),
        pred_normalized=only_file(pred_norm_dir, "*.goldset.json"),
        gold_pm4py_dir=root / "eval_inputs" / "pm4py_13" / case_id,
        pred_pm4py_dir=root / "runner" / "logs" / run_id / "converted_recommendation" / "pm4py",
        gold_worfbench=only_file(gold_worf_dir, "*.worfbench.json"),
        pred_worfbench=only_file(pred_worf_dir, "*.worfbench.json"),
        report_dir=root / "evaluation" / "reports" / run_id / case_id,
    )

    for path in [
        paths.gold_normalized,
        paths.pred_normalized,
        paths.gold_pm4py_dir,
        paths.pred_pm4py_dir,
        paths.gold_worfbench,
        paths.pred_worfbench,
    ]:
        if not path.exists():
            raise SystemExit(f"Missing required evaluation input: {path}")
    return paths


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_action_equivalence_map(root: Path) -> dict[str, str]:
    path = root / "evaluation" / "action_equivalence_rules.json"
    payload = load_json(path)
    mapping: dict[str, str] = {}
    for group in payload.get("equivalence_groups", []) or []:
        canonical = group.get("canonical")
        if not canonical:
            continue
        for member in group.get("members", []) or []:
            if member in mapping and mapping[member] != canonical:
                raise ValueError(f"Action equivalence member maps to multiple canonicals: {member}")
            mapping[member] = canonical
        mapping.setdefault(canonical, canonical)
    return mapping


def canonicalize_actions(actions: list[str], mapping: dict[str, str]) -> list[str]:
    return [mapping.get(action, action) for action in actions]


def flatten_actions(steps: list[dict[str, Any]], excluded: list[str] | None = None) -> list[str]:
    actions: list[str] = []
    for step in steps:
        if is_disabled_step(step):
            if excluded is not None:
                excluded.append(action_label(step.get("package"), step.get("action")) or step.get("type", "disabled"))
            continue
        step_type = step.get("type")
        if step_type == "action":
            package = step.get("package")
            action = step.get("action")
            label = action_label(package, action)
            if is_browser_session_lifecycle_action(package, action):
                if excluded is not None:
                    excluded.append(label)
                continue
            actions.append(label)
        elif step_type == "container":
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
        elif step_type in {"if", "loop", "trigger_loop"}:
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
            for branch in step.get("branches", []) or []:
                actions.extend(flatten_actions(branch.get("steps", []) or [], excluded))
        elif step_type == "try":
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
            for branch in step.get("branches", []) or []:
                if branch.get("branch") == "finally":
                    actions.extend(flatten_actions(branch.get("steps", []) or [], excluded))
        else:
            raise ValueError(f"Unknown step type: {step_type!r}")
    return actions


def packages(actions: list[str]) -> list[str]:
    return [action.split(".", 1)[0] for action in actions]


def package_family(package: str) -> str:
    normalized = package.lower().replace("_", " ").replace("-", " ").strip()
    if normalized in {"excel ms", "excel advanced"}:
        return "spreadsheet"
    if normalized in {"webautomation", "browser"}:
        return "browser"
    if normalized in {"errorhandler", "error handler"}:
        return "error_handler"
    if normalized in {"logtofile"}:
        return "logging"
    if normalized in {"taskbot", "messagebox", "screen"}:
        return "runtime_ui"
    return normalized.replace(" ", "_")


def package_families(actions: list[str]) -> list[str]:
    return [package_family(action.split(".", 1)[0]) for action in actions]


def salient_families(actions: list[str]) -> list[str]:
    support = {"string", "folder", "datetime", "file", "delay", "number", "logging", "runtime_ui"}
    return [family for family in package_families(actions) if family not in support]


def adjacent_edges(actions: list[str]) -> list[tuple[str, str]]:
    if not actions:
        return []
    return [("START", actions[0])] + list(zip(actions, actions[1:])) + [(actions[-1], "END")]


def lcs_len(a: list[str], b: list[str]) -> int:
    previous = [0] * (len(b) + 1)
    for left in a:
        current = [0]
        for idx, right in enumerate(b, start=1):
            current.append(previous[idx - 1] + 1 if left == right else max(previous[idx], current[-1]))
        previous = current
    return previous[-1]


def multiset_score(gold_items: list[Any], pred_items: list[Any]) -> dict[str, Any]:
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


def sequence_score(gold_actions: list[str], pred_actions: list[str]) -> dict[str, Any]:
    common = lcs_len(gold_actions, pred_actions)
    precision = common / len(pred_actions) if pred_actions else 0.0
    recall = common / len(gold_actions) if gold_actions else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "lcs": common,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gold_count": len(gold_actions),
        "prediction_count": len(pred_actions),
    }


def first_mismatches(gold_actions: list[str], pred_actions: list[str], limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(max(len(gold_actions), len(pred_actions))):
        gold = gold_actions[idx] if idx < len(gold_actions) else None
        pred = pred_actions[idx] if idx < len(pred_actions) else None
        if gold != pred:
            rows.append({"index": idx + 1, "gold": gold, "prediction": pred})
        if len(rows) >= limit:
            break
    return rows


def score_normalized(gold_path: Path, pred_path: Path) -> dict[str, Any]:
    gold = load_json(gold_path)
    pred = load_json(pred_path)
    equivalence_map = load_action_equivalence_map(goldset_root())
    excluded_gold_actions: list[str] = []
    excluded_prediction_actions: list[str] = []
    gold_actions = flatten_actions(gold.get("steps", []) or [], excluded_gold_actions)
    pred_actions = flatten_actions(pred.get("steps", []) or [], excluded_prediction_actions)
    gold_canonical_actions = canonicalize_actions(gold_actions, equivalence_map)
    pred_canonical_actions = canonicalize_actions(pred_actions, equivalence_map)

    return {
        "gold_path": str(gold_path),
        "prediction_path": str(pred_path),
        "gold_source_file": gold.get("source_file"),
        "prediction_source_file": pred.get("source_file"),
        "preprocessing": {
            "excluded_rule": "browser_session_lifecycle_action",
            "excluded_regex": {
                "package": "^(web\\s*automation|webautomation|browser|recorder)$",
                "action": "session",
            },
            "excluded_gold_actions": excluded_gold_actions,
            "excluded_prediction_actions": excluded_prediction_actions,
            "excluded_gold_count": len(excluded_gold_actions),
            "excluded_prediction_count": len(excluded_prediction_actions),
            "action_equivalence_rules_path": str(goldset_root() / "evaluation" / "action_equivalence_rules.json"),
            "action_equivalence_member_count": len(equivalence_map),
        },
        "gold_action_count": len(gold_actions),
        "prediction_action_count": len(pred_actions),
        "action_sequence": sequence_score(gold_actions, pred_actions),
        "action_multiset": multiset_score(gold_actions, pred_actions),
        "canonical_action_sequence": sequence_score(gold_canonical_actions, pred_canonical_actions),
        "canonical_action_multiset": multiset_score(gold_canonical_actions, pred_canonical_actions),
        "package_multiset": multiset_score(packages(gold_actions), packages(pred_actions)),
        "package_family_multiset": multiset_score(package_families(gold_actions), package_families(pred_actions)),
        "salient_family_multiset": multiset_score(salient_families(gold_actions), salient_families(pred_actions)),
        "adjacent_edge_multiset": multiset_score(adjacent_edges(gold_actions), adjacent_edges(pred_actions)),
        "canonical_adjacent_edge_multiset": multiset_score(adjacent_edges(gold_canonical_actions), adjacent_edges(pred_canonical_actions)),
        "first_mismatches": first_mismatches(gold_actions, pred_actions),
        "first_canonical_mismatches": first_mismatches(gold_canonical_actions, pred_canonical_actions),
        "gold_actions_preview": gold_actions[:20],
        "gold_canonical_actions_preview": gold_canonical_actions[:20],
        "prediction_actions": pred_actions,
        "prediction_canonical_actions": pred_canonical_actions,
        "gold_salient_families": salient_families(gold_actions),
        "prediction_salient_families": salient_families(pred_actions),
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    normalized = payload["normalized"]
    lines = [
        "# Evaluation Report",
        "",
        f"- Run: `{payload['run_id']}`",
        f"- Case: `{payload['case_id']}`",
        f"- Created at: `{payload['created_at']}`",
        f"- Gold actions: `{normalized['gold_action_count']}`",
        f"- Prediction actions: `{normalized['prediction_action_count']}`",
        "",
        "## Scores",
        "",
        f"- Action sequence LCS F1: `{normalized['action_sequence']['f1']:.4f}` "
        f"(precision `{normalized['action_sequence']['precision']:.4f}`, recall `{normalized['action_sequence']['recall']:.4f}`)",
        f"- Action multiset F1: `{normalized['action_multiset']['f1']:.4f}`",
        f"- Canonical action sequence LCS F1: `{normalized['canonical_action_sequence']['f1']:.4f}`",
        f"- Canonical action multiset F1: `{normalized['canonical_action_multiset']['f1']:.4f}`",
        f"- Package multiset F1: `{normalized['package_multiset']['f1']:.4f}`",
        f"- Package family F1: `{normalized['package_family_multiset']['f1']:.4f}`",
        f"- Salient family F1: `{normalized['salient_family_multiset']['f1']:.4f}`",
        f"- Adjacent edge F1: `{normalized['adjacent_edge_multiset']['f1']:.4f}`",
        f"- PM4Py fitness: `{payload['pm4py'].get('fitness')}`",
        f"- PM4Py precision: `{payload['pm4py'].get('precision')}`",
        f"- WorFBench precision: `{payload['worfbench'].get('precision')}`",
        f"- WorFBench recall: `{payload['worfbench'].get('recall')}`",
        f"- WorFBench F1: `{payload['worfbench'].get('f1_score')}`",
        "",
        "## Diagnostic Artifact Check",
        "",
        f"- Gold PNML readable: `{payload['pm4py_artifact_check']['gold_pnml'].get('readable_by_pm4py')}`",
        f"- Prediction PNML readable: `{payload['pm4py_artifact_check']['prediction_pnml'].get('readable_by_pm4py')}`",
        f"- Tree leaf delta: `{payload['pm4py_artifact_check']['tree_leaf_count_delta']}`",
        f"- PNML hash equal: `{payload['pm4py_artifact_check']['pnml_hash_equal']}`",
        f"- Diagnostic WorFBench node-label F1: `{payload['worfbench_diagnostic_artifact_f1']['node_label_f1']['f1']:.4f}`",
        f"- Diagnostic WorFBench edge F1: `{payload['worfbench_diagnostic_artifact_f1']['edge_f1']['f1']:.4f}`",
        "",
        "## First Mismatches",
        "",
    ]
    if normalized["first_mismatches"]:
        for row in normalized["first_mismatches"]:
            lines.append(f"- `{row['index']}` gold=`{row['gold']}` prediction=`{row['prediction']}`")
    else:
        lines.append("- No positional mismatches.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate one backend runner output against one 13-case goldset artifact.")
    parser.add_argument("--case-id", default="03_0131_currency-rate---oanda")
    parser.add_argument("--run-id", default="runner_v2_repeat_20260715_01")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = resolve_paths(args.case_id, args.run_id)
    paths.report_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_id": paths.case_id,
        "run_id": paths.run_id,
        "normalized": score_normalized(paths.gold_normalized, paths.pred_normalized),
        "pm4py": score_pm4py_conformance(paths.gold_normalized, paths.pred_normalized),
        "pm4py_artifact_check": compare_pm4py_artifacts(paths.gold_pm4py_dir, paths.pred_pm4py_dir),
        "worfbench": score_worfbench_f1chain(paths.gold_normalized, paths.pred_normalized),
        "worfbench_diagnostic_artifact_f1": score_worfbench(paths.gold_worfbench, paths.pred_worfbench),
    }

    json_path = paths.report_dir / "evaluation.json"
    md_path = paths.report_dir / "evaluation.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, payload)
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(md_path),
                "action_sequence_f1": payload["normalized"]["action_sequence"]["f1"],
                "action_multiset_f1": payload["normalized"]["action_multiset"]["f1"],
                "canonical_action_sequence_f1": payload["normalized"]["canonical_action_sequence"]["f1"],
                "canonical_action_multiset_f1": payload["normalized"]["canonical_action_multiset"]["f1"],
                "package_multiset_f1": payload["normalized"]["package_multiset"]["f1"],
                "package_family_f1": payload["normalized"]["package_family_multiset"]["f1"],
                "salient_family_f1": payload["normalized"]["salient_family_multiset"]["f1"],
                "pm4py_fitness": payload["pm4py"].get("fitness"),
                "pm4py_precision": payload["pm4py"].get("precision"),
                "worfbench_precision": payload["worfbench"].get("precision"),
                "worfbench_recall": payload["worfbench"].get("recall"),
                "worfbench_f1": payload["worfbench"].get("f1_score"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
