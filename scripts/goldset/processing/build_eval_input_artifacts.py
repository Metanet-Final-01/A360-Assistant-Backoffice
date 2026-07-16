from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader


TEMP_CATEGORY = "03_broad_sequential_workflow"


def goldset_root() -> Path:
    return Path(__file__).resolve().parents[1]


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def source_files(candidate: dict[str, Any]) -> list[str]:
    return list(candidate.get("source_files") or [candidate["source_file"]])


def pdf_name(candidate: dict[str, Any]) -> str:
    stems = "+".join(source.removesuffix(".goldset.json") for source in source_files(candidate))
    return f"{candidate['bot_name']}__{stems}.pdf"


def case_stem(entry: dict[str, Any]) -> str:
    return "+".join(Path(source["extracted_workflow_file"]).stem for source in entry["sources"])


def create_temp_dataset(root: Path, manifest: dict[str, Any], temp_dir: Path) -> dict[str, dict[str, Any]]:
    case_by_bot: dict[str, dict[str, Any]] = {}
    category_dir = temp_dir / TEMP_CATEGORY
    category_dir.mkdir(parents=True, exist_ok=True)

    for entry in manifest["entries"]:
        case_dir = category_dir / entry["case_dir"]
        workflows_dir = case_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        index_entries = []
        for source in entry["sources"]:
            raw_rel = Path(source["extracted_workflow_file"])
            raw_source = root / "eval_inputs" / "extracted_workflows_13" / entry["case_dir"] / raw_rel
            raw_target = workflows_dir / raw_rel.name
            shutil.copy2(raw_source, raw_target)
            index_entries.append({
                "filename": raw_rel.stem,
                "content_type": "application/vnd.aa.taskbot",
                "output_file": f"workflows/{raw_rel.name}",
                "manual_dependencies": [],
                "scanned_dependencies": [],
            })
        (case_dir / "workflow_index.json").write_text(
            json.dumps({"bot_dir": f"{TEMP_CATEGORY}\\{entry['case_dir']}", "workflows": index_entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        case_by_bot[entry["bot_name"]] = entry
    return case_by_bot


def run_processing_script(root: Path, script_name: str, temp_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(root / "processing" / script_name), "--dataset-dir", str(temp_dir)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def collapse_multi_source_cases(temp_dir: Path, manifest: dict[str, Any]) -> None:
    for entry in manifest["entries"]:
        if entry["source_count"] <= 1:
            continue

        workflows_dir = temp_dir / TEMP_CATEGORY / entry["case_dir"] / "workflows"
        component_paths = [
            workflows_dir / f"{Path(source['extracted_workflow_file']).stem}.goldset.json"
            for source in entry["sources"]
        ]
        components = [json.loads(path.read_text(encoding="utf-8")) for path in component_paths]
        combined = {
            "source_file": f"{case_stem(entry)}.json",
            "source_files": [component.get("source_file") for component in components],
            "combined_from": [path.name for path in component_paths],
            "triggers": [trigger for component in components for trigger in component.get("triggers", [])],
            "steps": [step for component in components for step in component.get("steps", [])],
        }

        combined_path = workflows_dir / f"{case_stem(entry)}.goldset.json"
        combined_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for path in component_paths:
            path.unlink()


def copy_case_artifacts(root: Path, temp_dir: Path, manifest: dict[str, Any]) -> None:
    output_roots = {
        "normalized_workflows_13": ("*.goldset.json",),
        "pm4py_13": ("*.pnml", "*.ptml", "*.tree.json"),
        "worfbench_13": ("*.worfbench.json",),
    }
    for dirname in output_roots:
        clean_dir(root / "eval_inputs" / dirname)

    for entry in manifest["entries"]:
        temp_workflows = temp_dir / TEMP_CATEGORY / entry["case_dir"] / "workflows"
        for dirname, patterns in output_roots.items():
            case_output = root / "eval_inputs" / dirname / entry["case_dir"]
            case_output.mkdir(parents=True, exist_ok=True)
            for pattern in patterns:
                for artifact in sorted(temp_workflows.glob(pattern)):
                    shutil.copy2(artifact, case_output / artifact.name)

    for dirname in output_roots:
        payload = {
            "source": "eval_inputs/extracted_workflows_13",
            "description": f"Regenerated {dirname} artifacts for the 13 eval-input cases.",
            "case_count": len(manifest["entries"]),
            "entries": [
                {
                    "case_dir": entry["case_dir"],
                    "bot_name": entry["bot_name"],
                    "source_count": entry["source_count"],
                    "files": sorted(path.name for path in (root / "eval_inputs" / dirname / entry["case_dir"]).glob("*")),
                }
                for entry in manifest["entries"]
            ],
        }
        (root / "eval_inputs" / dirname / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def collect_actions_from_steps(steps: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    for step in steps:
        step_type = step.get("type")
        if step_type in {"action", "container"}:
            actions.append({
                "type": step_type,
                "package": step.get("package"),
                "action": step.get("action"),
            })
        collect_actions_from_steps(step.get("steps", []) or [], actions)
        for branch in step.get("branches", []) or []:
            collect_actions_from_steps(branch.get("steps", []) or [], actions)


def write_comparison_report(root: Path, manifest: dict[str, Any]) -> None:
    briefs = json.loads((root / "eval_inputs" / "task_briefs.json").read_text(encoding="utf-8"))
    candidates = {candidate["bot_name"]: candidate for candidate in briefs["candidates"]}
    output_dir = root / "eval_inputs" / "comparison_reports"
    clean_dir(output_dir)

    rows = []
    for entry in manifest["entries"]:
        candidate = candidates[entry["bot_name"]]
        pdf_path = root / "eval_inputs" / "pdfs" / pdf_name(candidate)
        pdf_text = extract_pdf_text(pdf_path)
        normalized_files = sorted((root / "eval_inputs" / "normalized_workflows_13" / entry["case_dir"]).glob("*.goldset.json"))

        actions: list[dict[str, Any]] = []
        for normalized_file in normalized_files:
            normalized = json.loads(normalized_file.read_text(encoding="utf-8"))
            collect_actions_from_steps(normalized.get("steps", []) or [], actions)

        task_names = [task["name"] for task in candidate.get("tasks", [])]
        systems = sorted({system for task in candidate.get("tasks", []) for system in task.get("systems", []) if system})
        rows.append({
            "case_dir": entry["case_dir"],
            "bot_name": entry["bot_name"],
            "pdf_file": pdf_path.name,
            "normalized_files": [path.name for path in normalized_files],
            "pdf_text_length": len(pdf_text),
            "title_in_pdf_text": candidate["title"] in pdf_text,
            "task_names_found_in_pdf_text": [name for name in task_names if name in pdf_text],
            "task_names_missing_from_pdf_text": [name for name in task_names if name not in pdf_text],
            "task_count": len(task_names),
            "system_terms": systems,
            "normalized_action_count": len(actions),
            "normalized_packages": sorted({action["package"] for action in actions if action.get("package")}),
            "normalized_actions_sample": actions[:20],
        })

    payload = {
        "description": "PDF text vs normalized workflow comparison for the 13 eval inputs.",
        "case_count": len(rows),
        "rows": rows,
    }
    (output_dir / "pdf_vs_normalized.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# PDF vs Normalized Workflow Comparison",
        "",
        "| Case | PDF title found | Tasks found | Normalized files | Actions | Packages |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for row in rows:
        packages = ", ".join(row["normalized_packages"][:8])
        if len(row["normalized_packages"]) > 8:
            packages += ", ..."
        lines.append(
            f"| `{row['case_dir']}` | {row['title_in_pdf_text']} | "
            f"{len(row['task_names_found_in_pdf_text'])}/{row['task_count']} | "
            f"{', '.join(f'`{name}`' for name in row['normalized_files'])} | "
            f"{row['normalized_action_count']} | {packages} |"
        )
    (output_dir / "pdf_vs_normalized.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate clean eval-input artifacts and comparison reports.")
    parser.add_argument("--root-dir", type=Path, default=goldset_root())
    args = parser.parse_args()

    root = args.root_dir.resolve()
    manifest = json.loads((root / "eval_inputs" / "extracted_workflows_13" / "manifest.json").read_text(encoding="utf-8"))
    temp_dir = Path(tempfile.mkdtemp(prefix="a360_eval_artifacts_"))
    try:
        create_temp_dataset(root, manifest, temp_dir)
        run_processing_script(root, "normalize_extracted_workflows.py", temp_dir)
        collapse_multi_source_cases(temp_dir, manifest)
        run_processing_script(root, "convert_to_pm4py.py", temp_dir)
        run_processing_script(root, "convert_to_worfbench.py", temp_dir)
        copy_case_artifacts(root, temp_dir, manifest)
        write_comparison_report(root, manifest)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(json.dumps({
        "normalized": str(root / "eval_inputs" / "normalized_workflows_13"),
        "pm4py": str(root / "eval_inputs" / "pm4py_13"),
        "worfbench": str(root / "eval_inputs" / "worfbench_13"),
        "comparison": str(root / "eval_inputs" / "comparison_reports"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
