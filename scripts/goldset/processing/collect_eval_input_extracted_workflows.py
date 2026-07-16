from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_goldset_root() -> Path:
    return Path(__file__).resolve().parents[1]


def source_files(candidate: dict) -> list[str]:
    files = candidate.get("source_files")
    if files:
        return list(files)
    return [candidate["source_file"]]


def raw_workflow_name(source_file: str) -> str:
    if source_file.endswith(".goldset.json"):
        return source_file[: -len(".goldset.json")] + ".json"
    return source_file


def find_raw_workflow(dataset_dir: Path, bot_name: str, source_file: str) -> tuple[Path, list[dict]]:
    target = raw_workflow_name(source_file)
    matches = sorted(dataset_dir.glob(f"*/{bot_name}/workflows/{target}"))
    if not matches:
        raise FileNotFoundError(f"No extracted workflow found for {bot_name}/{target}")

    variants = [{"path": str(path.relative_to(dataset_dir)), "sha256": sha256(path)} for path in matches]
    if len({variant["sha256"] for variant in variants}) > 1:
        raise ValueError(f"Multiple non-identical extracted workflows found for {bot_name}/{target}")
    return matches[0], variants


def case_dir_name(index: int, bot_name: str) -> str:
    return f"{index:02d}_{bot_name}"


def collect(root_dir: Path, output_dir: Path, *, clean: bool) -> dict:
    root_dir = root_dir.resolve()
    dataset_dir = root_dir / "dataset"
    task_briefs_path = root_dir / "eval_inputs" / "task_briefs.json"
    output_dir = output_dir.resolve()

    expected_parent = (root_dir / "eval_inputs").resolve()
    if expected_parent not in output_dir.parents and output_dir != expected_parent:
        raise ValueError(f"Refusing to write outside eval_inputs: {output_dir}")

    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    task_briefs = json.loads(task_briefs_path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    raw_file_count = 0

    for index, candidate in enumerate(task_briefs["candidates"], start=1):
        bot_name = candidate["bot_name"]
        case_dir = output_dir / case_dir_name(index, bot_name)
        workflows_dir = case_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        sources = []
        copied_index = False
        for source_file in source_files(candidate):
            raw_path, variants = find_raw_workflow(dataset_dir, bot_name, source_file)
            destination = workflows_dir / raw_path.name
            shutil.copy2(raw_path, destination)
            raw_file_count += 1

            source_index = raw_path.parents[1] / "workflow_index.json"
            if source_index.exists() and not copied_index:
                shutil.copy2(source_index, case_dir / "workflow_index.json")
                copied_index = True

            sources.append(
                {
                    "source_file_from_task_brief": source_file,
                    "extracted_workflow_file": destination.relative_to(case_dir).as_posix(),
                    "selected_source_path": str(raw_path.relative_to(root_dir)),
                    "sha256": sha256(destination),
                    "equivalent_source_variants": variants,
                }
            )

        entries.append(
            {
                "index": index,
                "bot_name": bot_name,
                "title": candidate.get("title"),
                "case_dir": case_dir.name,
                "source_count": len(sources),
                "workflow_index_included": copied_index,
                "sources": sources,
            }
        )

    payload = {
        "description": "Raw workflow JSONs created by processing/extract_workflows.py for eval_inputs/task_briefs.json.",
        "source_manifest": str(task_briefs_path.relative_to(root_dir)),
        "case_count": len(entries),
        "raw_workflow_json_file_count": raw_file_count,
        "entries": entries,
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect only extract_workflows.py outputs for eval input cases.")
    parser.add_argument("--root-dir", type=Path, default=repo_goldset_root())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-clean", action="store_true", help="Do not replace the existing output directory.")
    args = parser.parse_args()

    root_dir = args.root_dir.resolve()
    output_dir = args.output_dir or (root_dir / "eval_inputs" / "extracted_workflows_13")
    payload = collect(root_dir, output_dir, clean=not args.no_clean)
    print(json.dumps({"output_dir": str(output_dir.resolve()), **{k: payload[k] for k in ("case_count", "raw_workflow_json_file_count")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
