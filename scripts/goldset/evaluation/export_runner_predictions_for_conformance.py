from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "a360-eval-sandbox").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def source_bot_from_run(run: dict[str, Any]) -> str:
    input_path = str(run.get("input") or "")
    name = Path(input_path).name
    if "__" in name:
        return name.split("__", 1)[0]

    run_id = str(run.get("run_id") or "")
    match = re.search(r"__(?:\d+_)?(.+)$", run_id)
    if match:
        return match.group(1)
    raise ValueError(f"Could not infer source_bot for run: {run}")


def flatten_recommendation(recommendation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not recommendation:
        return []
    out: list[dict[str, Any]] = []

    def walk(actions: list[dict[str, Any]]) -> None:
        for action in actions:
            out.append({"package": action.get("package"), "action": action.get("action")})
            walk(action.get("children") or [])

    for step in recommendation.get("steps") or []:
        walk(step.get("actions") or [])
    return out


def final_done_data(manifest: dict[str, Any], step_name: str) -> dict[str, Any]:
    for step in manifest.get("steps") or []:
        if step.get("name") != step_name:
            continue
        final_event = ((step.get("response") or {}).get("final_event") or {})
        data = final_event.get("data") or {}
        return data if isinstance(data, dict) else {}
    return {}


def prediction_from_run(run: dict[str, Any], manifest_path: Path, step_name: str) -> dict[str, Any]:
    source_bot = source_bot_from_run(run)
    result: dict[str, Any] = {"source_bot": source_bot}

    if run.get("status") != "ok" or not manifest_path.exists():
        result.update(
            {
                "predicted_actions": [],
                "predicted_action_count": 0,
                "error": f"runner_status={run.get('status')} manifest_exists={manifest_path.exists()}",
            }
        )
        return result

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = final_done_data(manifest, step_name)
    recommendation = data.get("recommendation") or data.get("updated_recommendation")
    if not isinstance(recommendation, dict):
        recommendation = None

    actions = flatten_recommendation(recommendation)
    result.update(
        {
            "input_mode": "pdf",
            "document_id": manifest.get("document_id"),
            "predicted_actions": actions,
            "predicted_action_count": len(actions),
            "full_recommendation": recommendation,
            "analysis_steps": ((data.get("analysis_result") or {}).get("steps") or []),
            "analysis_ambiguities": ((data.get("analysis_result") or {}).get("ambiguities") or []),
            "variables": (recommendation or {}).get("variables", []),
            "notes": (recommendation or {}).get("notes") if recommendation else None,
        }
    )
    if not actions:
        result["error"] = "empty_prediction"
    return result


def export_predictions(combined_manifest: Path, run_label: str, step_name: str) -> Path:
    root = workspace_root()
    manifest = json.loads(combined_manifest.read_text(encoding="utf-8"))
    predictions = []
    for run in manifest.get("runs") or []:
        manifest_path = root / str(run.get("manifest"))
        predictions.append(prediction_from_run(run, manifest_path, step_name))

    out_path = root / "a360-eval-sandbox" / "Metadata" / f"predictions_from_agent_{run_label}.json"
    out_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("combined_manifest", type=Path)
    parser.add_argument("run_label")
    parser.add_argument("--step-name", default="turnRecommend")
    args = parser.parse_args()

    out_path = export_predictions(args.combined_manifest, args.run_label, args.step_name)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
