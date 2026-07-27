from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from run_eval_case import resolve_paths, score_normalized, write_markdown
from adapters.pm4py_adapter import compare_pm4py_artifacts, score_pm4py_conformance
from adapters.worfbench_adapter import score_worfbench, score_worfbench_f1chain


ROOT = Path(__file__).resolve().parents[1]


def source_case_from_input(path: str | None) -> str | None:
    if not path:
        return None
    name = Path(path).name
    return name.split("__", 1)[0] if "__" in name else None


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
        "normalized": score_normalized(paths.gold_normalized, paths.pred_normalized),
        "pm4py": score_pm4py_conformance(paths.gold_normalized, paths.pred_normalized),
        "pm4py_artifact_check": compare_pm4py_artifacts(paths.gold_pm4py_dir, paths.pred_pm4py_dir),
        "worfbench": score_worfbench_f1chain(paths.gold_normalized, paths.pred_normalized),
        "worfbench_diagnostic_artifact_f1": score_worfbench(paths.gold_worfbench, paths.pred_worfbench),
    }
    (paths.report_dir / "evaluation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(paths.report_dir / "evaluation.md", {**payload, "created_at": "", "case_id": case_id, "run_id": run_id})
    return payload


def row_from_payload(payload: dict[str, Any], status: str = "ok", error: str | None = None) -> dict[str, Any]:
    normalized = payload.get("normalized") or {}
    pm4py = payload.get("pm4py") or {}
    worfbench = payload.get("worfbench") or {}
    return {
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "status": status,
        "error": error,
        "gold_actions": normalized.get("gold_action_count"),
        "prediction_actions": normalized.get("prediction_action_count"),
        "canonical_action_f1": (normalized.get("canonical_action_multiset") or {}).get("f1"),
        "canonical_sequence_f1": (normalized.get("canonical_action_sequence") or {}).get("f1"),
        "package_family_f1": (normalized.get("package_family_multiset") or {}).get("f1"),
        "pm4py_status": pm4py.get("status"),
        "pm4py_fitness": pm4py.get("fitness"),
        "pm4py_precision": pm4py.get("precision"),
        "worfbench_status": worfbench.get("status"),
        "worfbench_precision": worfbench.get("precision"),
        "worfbench_recall": worfbench.get("recall"),
        "worfbench_f1": worfbench.get("f1_score"),
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
        "canonical_action_f1",
        "canonical_sequence_f1",
        "package_family_f1",
        "pm4py_status",
        "pm4py_fitness",
        "pm4py_precision",
        "worfbench_status",
        "worfbench_precision",
        "worfbench_recall",
        "worfbench_f1",
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
            "canonical_action_f1": average(rows, "canonical_action_f1"),
            "canonical_sequence_f1": average(rows, "canonical_sequence_f1"),
            "package_family_f1": average(rows, "package_family_f1"),
            "pm4py_fitness": average(rows, "pm4py_fitness"),
            "pm4py_precision": average(rows, "pm4py_precision"),
            "worfbench_precision": average(rows, "worfbench_precision"),
            "worfbench_recall": average(rows, "worfbench_recall"),
            "worfbench_f1": average(rows, "worfbench_f1"),
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
            "| case_id | status | pred actions | PM4Py fitness | PM4Py precision | WorFBench F1 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.get('case_id')} | {row.get('status')} | {row.get('prediction_actions')} | "
            f"{row.get('pm4py_fitness')} | {row.get('pm4py_precision')} | {row.get('worfbench_f1')} |"
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
    output_name = args.output_name or manifest.get("name") or args.combined_manifest.stem
    rows: list[dict[str, Any]] = []
    for run in manifest.get("runs") or []:
        source = source_case_from_input(run.get("input"))
        case_id = case_map.get(source or "")
        run_id = run.get("run_id")
        if not case_id or not run_id or run.get("status") != "ok":
            rows.append(
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "status": "skipped",
                    "error": f"source={source} runner_status={run.get('status')}",
                }
            )
            continue
        try:
            rows.append(row_from_payload(evaluate_case(case_id, run_id)))
            print(f"OK {case_id} {run_id}")
        except Exception as exc:  # noqa: BLE001 - keep batch going
            rows.append({"case_id": case_id, "run_id": run_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {case_id} {run_id}: {exc}")

    output_dir = ROOT / "evaluation" / "reports" / output_name
    write_summary(output_dir, rows)
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
