from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TEMP_CATEGORY = "03_broad_sequential_workflow"


def goldset_root() -> Path:
    return Path(__file__).resolve().parents[1]


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def find_step(manifest: dict[str, Any], step_name: str) -> dict[str, Any]:
    for step in manifest.get("steps", []) or []:
        if step.get("name") == step_name:
            return step
    raise ValueError(f"Step not found in run manifest: {step_name}")


def recommendation_from_step(step: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    final_event = ((step.get("response") or {}).get("final_event") or {})
    data = final_event.get("data") or {}
    recommendation = data.get("recommendation")
    if not isinstance(recommendation, dict):
        raise ValueError("Final event does not contain data.recommendation")
    return data, recommendation


def value_to_goldset_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "NULL", "value": None}
    if isinstance(value, bool):
        return {"type": "BOOLEAN", "boolean": value}
    if isinstance(value, int | float):
        return {"type": "NUMBER", "number": value}
    if isinstance(value, str):
        return {"type": "STRING", "string": value}
    return {"type": "JSON", "json": value}


def parameter_to_attribute(parameter: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": parameter.get("name"),
        "label": parameter.get("label"),
        "value": value_to_goldset_value(parameter.get("value")),
        "value_source": parameter.get("value_source"),
        "rationale": parameter.get("rationale"),
        "original_parameter": parameter,
    }


def metadata_attributes(kind: str, payload: dict[str, Any], skip: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": f"recommendation_{kind}",
            "value": {"type": "JSON", "json": {key: value for key, value in payload.items() if key not in skip}},
        }
    ]


def action_to_step(action: dict[str, Any]) -> dict[str, Any]:
    child_steps = [action_to_step(child) for child in action.get("children", []) or []]
    common = {
        "package": action.get("package"),
        "action": action.get("action"),
        "disabled": False,
        "attributes": [
            *[parameter_to_attribute(parameter) for parameter in action.get("parameters", []) or []],
            *metadata_attributes("action_meta", action, {"children", "parameters", "package", "action"}),
        ],
        "original_recommendation_action": action,
    }

    if child_steps:
        return {
            "type": "container",
            **common,
            "steps": child_steps,
        }
    return {
        "type": "action",
        **common,
    }


def recommendation_step_to_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "container",
        "package": "Recommendation",
        "action": "businessStep",
        "disabled": False,
        "attributes": metadata_attributes("business_step", step, {"actions"}),
        "steps": [action_to_step(action) for action in step.get("actions", []) or []],
        "original_recommendation_step": step,
    }


def convert_recommendation(
    *,
    run_manifest: dict[str, Any],
    data: dict[str, Any],
    recommendation: dict[str, Any],
    source_file: str,
    step_name: str,
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "source": "backend_recommendation",
        "runner": run_manifest.get("runner"),
        "run_id": run_manifest.get("run_id"),
        "session_id": run_manifest.get("session_id"),
        "document_id": run_manifest.get("document_id"),
        "backend_step": step_name,
        "backend_response_id": data.get("id"),
        "backend_response_version": data.get("version"),
        "parent_version": data.get("parent_version"),
        "answer": data.get("answer"),
        "sources": data.get("sources", []),
        "usage_gauge": data.get("usage_gauge"),
        "variables": recommendation.get("variables", []),
        "notes": recommendation.get("notes"),
        "original_recommendation": recommendation,
        "triggers": [],
        "steps": [recommendation_step_to_step(step) for step in recommendation.get("steps", []) or []],
    }


def run_processing_script(root: Path, script_name: str, dataset_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(root / "processing" / script_name), "--dataset-dir", str(dataset_dir)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def write_conversions(root: Path, normalized_path: Path, output_dir: Path, record_stem: str) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="a360_backend_rec_"))
    try:
        workflows_dir = temp_dir / TEMP_CATEGORY / record_stem / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(normalized_path, workflows_dir / normalized_path.name)

        run_processing_script(root, "convert_to_pm4py.py", temp_dir)
        run_processing_script(root, "convert_to_worfbench.py", temp_dir)

        pm4py_dir = output_dir / "pm4py"
        worfbench_dir = output_dir / "worfbench"
        clean_dir(pm4py_dir)
        clean_dir(worfbench_dir)
        for pattern in ("*.pnml", "*.ptml", "*.tree.json"):
            for path in workflows_dir.glob(pattern):
                shutil.copy2(path, pm4py_dir / path.name)
        for path in workflows_dir.glob("*.worfbench.json"):
            shutil.copy2(path, worfbench_dir / path.name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a backend runner recommendation into normalized goldset format and optional scorer artifacts."
    )
    parser.add_argument("run_manifest", type=Path)
    parser.add_argument("--step-name", default="turnRecommend")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--record-stem")
    parser.add_argument("--with-conversions", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = goldset_root()
    run_manifest_path = args.run_manifest.resolve()
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    step = find_step(run_manifest, args.step_name)
    data, recommendation = recommendation_from_step(step)

    record_stem = args.record_stem or f"{run_manifest.get('run_id', run_manifest_path.parent.name)}__{args.step_name}"
    output_dir = (args.output_dir or (run_manifest_path.parent / "converted_recommendation")).resolve()
    normalized_dir = output_dir / "normalized"
    clean_dir(normalized_dir)

    normalized = convert_recommendation(
        run_manifest=run_manifest,
        data=data,
        recommendation=recommendation,
        source_file=f"{record_stem}.json",
        step_name=args.step_name,
    )
    normalized_path = normalized_dir / f"{record_stem}.goldset.json"
    normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if args.with_conversions:
        write_conversions(root, normalized_path, output_dir, record_stem)

    print(json.dumps({
        "normalized": str(normalized_path),
        "steps": len(normalized.get("steps", [])),
        "actions": sum(len(step.get("steps", []) or []) for step in normalized.get("steps", [])),
        "pm4py": str(output_dir / "pm4py") if args.with_conversions else None,
        "worfbench": str(output_dir / "worfbench") if args.with_conversions else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
