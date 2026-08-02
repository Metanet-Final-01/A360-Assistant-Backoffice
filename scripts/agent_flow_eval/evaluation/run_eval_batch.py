from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from run_eval_case import resolve_paths, score_normalized, write_markdown
from adapters.worfbench_adapter import score_worfbench, score_worfbench_f1chain
from path_utils import ensure_child_path, safe_path_component

# PM4Py artifact conformance remains diagnostic. Official normalized scoring uses
# the same rule-based conversion policy for Gold and prediction workflows.


ROOT = Path(__file__).resolve().parents[1]


def source_case_from_input(path: str | None) -> str | None:
    if not path:
        return None
    name = Path(path).name
    return name.split("__", 1)[0] if "__" in name else None


def source_case_from_run_id(run_id: str | None) -> str | None:
    if not run_id or "__" not in run_id:
        return None
    tail = run_id.rsplit("__", 1)[1]
    parts = tail.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1]
    return tail


def load_case_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for case_dir in sorted((ROOT / "eval_inputs" / "normalized_workflows_13").iterdir()):
        if not case_dir.is_dir():
            continue
        source = case_dir.name.split("_", 1)[1]
        mapping[source] = case_dir.name
    return mapping


def evaluate_case(case_id: str, run_id: str) -> dict[str, Any]:
    paths = resolve_paths(case_id, run_id)
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": case_id,
        "run_id": run_id,
        "normalized": score_normalized(paths.gold_normalized, paths.pred_normalized, case_id=case_id),
        "worfbench": score_worfbench_f1chain(paths.gold_normalized, paths.pred_normalized),
        "worfbench_diagnostic_artifact_f1": score_worfbench(paths.gold_worfbench, paths.pred_worfbench),
    }
    (paths.report_dir / "evaluation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(paths.report_dir / "evaluation.md", {**payload, "created_at": "", "case_id": case_id, "run_id": run_id})
    return payload


def row_from_payload(payload: dict[str, Any], status: str = "ok", error: str | None = None) -> dict[str, Any]:
    normalized = payload.get("normalized") or {}
    action_prf1 = normalized.get("action_prf1") or {}
    action_chain = normalized.get("action_chain") or {}
    worfbench = payload.get("worfbench") or {}
    return {
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "status": status,
        "error": error,
        "gold_actions": normalized.get("gold_action_count"),
        "prediction_actions": normalized.get("prediction_action_count"),
        "action_precision": action_prf1.get("precision"),
        "action_recall": action_prf1.get("recall"),
        "action_f1": action_prf1.get("f1"),
        "rule_match_count": normalized.get("rule_match_count"),
        "judge_match_count": normalized.get("judge_match_count"),
        "action_chain_precision": action_chain.get("precision"),
        "action_chain_recall": action_chain.get("recall"),
        "action_chain_f1": action_chain.get("f1"),
        "canonical_action_f1": (normalized.get("canonical_action_multiset") or {}).get("f1"),
        "canonical_sequence_f1": (normalized.get("canonical_action_sequence") or {}).get("f1"),
        "worfbench_status_external_reference": worfbench.get("status"),
        "worfbench_precision_external_reference": worfbench.get("precision"),
        "worfbench_recall_external_reference": worfbench.get("recall"),
        "worfbench_f1_external_reference": worfbench.get("f1_score"),
        "worfbench_gold_fidelity": worfbench.get("gold_worfbench_fidelity"),
        "worfbench_prediction_fidelity": worfbench.get("prediction_worfbench_fidelity"),
    }


def average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if isinstance(row.get(key), (int, float))]
    return round(mean(values), 4) if values else None


def write_summary(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    preferred_columns = [
        "case_id",
        "run_id",
        "status",
        "error",
        "gold_actions",
        "prediction_actions",
        "action_precision",
        "action_recall",
        "action_f1",
        "rule_match_count",
        "judge_match_count",
        "action_chain_precision",
        "action_chain_recall",
        "action_chain_f1",
        "canonical_action_f1",
        "canonical_sequence_f1",
        "worfbench_status_external_reference",
        "worfbench_precision_external_reference",
        "worfbench_recall_external_reference",
        "worfbench_f1_external_reference",
        "worfbench_gold_fidelity",
        "worfbench_prediction_fidelity",
    ]
    extra_columns = sorted({key for row in rows for key in row} - set(preferred_columns))
    columns = [column for column in preferred_columns if any(column in row for row in rows)] + extra_columns
    with (output_dir / "score_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "case_count": len(rows),
        "ok_count": sum(row.get("status") == "ok" for row in rows),
        "averages": {
            "action_precision": average(rows, "action_precision"),
            "action_recall": average(rows, "action_recall"),
            "action_f1": average(rows, "action_f1"),
            "action_chain_precision": average(rows, "action_chain_precision"),
            "action_chain_recall": average(rows, "action_chain_recall"),
            "action_chain_f1": average(rows, "action_chain_f1"),
            "canonical_action_f1": average(rows, "canonical_action_f1"),
            "canonical_sequence_f1": average(rows, "canonical_sequence_f1"),
            "worfbench_precision_external_reference": average(rows, "worfbench_precision_external_reference"),
            "worfbench_recall_external_reference": average(rows, "worfbench_recall_external_reference"),
            "worfbench_f1_external_reference": average(rows, "worfbench_f1_external_reference"),
        },
        "rows": rows,
    }
    (output_dir / "score_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Evaluation Batch Summary", "", "| metric | average |", "|---|---:|"]
    for key, value in summary["averages"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "| case_id | status | pred actions | Action F1 | Action Chain F1 | WorFEval F1(참고) |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.get('case_id')} | {row.get('status')} | {row.get('prediction_actions')} | "
            f"{row.get('action_f1')} | {row.get('action_chain_f1')} | {row.get('worfbench_f1_external_reference')} |"
        )
    (output_dir / "score_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate every run in a runner combined manifest.")
    parser.add_argument("combined_manifest", type=Path)
    parser.add_argument("--output-name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.combined_manifest.read_text(encoding="utf-8"))
    case_map = load_case_map()
    output_name = safe_path_component(args.output_name or manifest.get("name") or args.combined_manifest.stem, field="output_name")
    rows: list[dict[str, Any]] = []
    for run in manifest.get("runs") or []:
        run_id = run.get("run_id")
        source = source_case_from_input(run.get("input") or run.get("pdf")) or source_case_from_run_id(run_id)
        case_id = case_map.get(source or "")
        runner_status = run.get("runner_status", run.get("status"))
        if not case_id or not run_id or runner_status != "ok":
            rows.append(
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "status": "skipped",
                    "error": f"source={source} runner_status={runner_status}",
                }
            )
            continue
        try:
            rows.append(row_from_payload(evaluate_case(case_id, run_id)))
            print(f"OK {case_id} {run_id}")
        except Exception as exc:  # noqa: BLE001 - keep batch going
            rows.append({"case_id": case_id, "run_id": run_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {case_id} {run_id}: {exc}")

    output_dir = ensure_child_path(ROOT / "evaluation" / "reports", ROOT / "evaluation" / "reports" / output_name, field="output_name")
    write_summary(output_dir, rows)
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
