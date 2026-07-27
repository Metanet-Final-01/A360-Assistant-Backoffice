from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)
MANIFEST_FILENAME = "manifest.json"

# contentType values that manifest.json uses for actual action-sequence workflows.
# Confirmed by reading real files: taskbot/aiagent/headlessbot all share the same
# {"triggers": [...], "nodes": [...]} body; "workflow" is the AARI-style orchestration
# variant ({"nodes": [...]} only, nodes reference other taskbots by repository path).
# application/vnd.aa.form and application/vnd.aa.prompt are also extension-less but are
# NOT action sequences (form layout / LLM prompt template) — deliberately excluded.
#
# application/vnd.aa.aiagent is deliberately excluded too: confirmed empirically that
# 4/4 aiagent files across the corpus have zero top-level nodes (100%, vs. 0/155 for
# every other type) — they orchestrate other files via manualDependencies rather than
# embedding their own action sequence, and an AI Agent's actual step order is decided
# by an LLM at runtime, so there's no fixed reference sequence for pm4py/WorFBench to
# check against in the first place. Their referenced sub-workflows (taskbot/headlessbot)
# are extracted and scored normally on their own.
WORKFLOW_CONTENT_TYPES = frozenset({
    "application/vnd.aa.taskbot",
    "application/vnd.aa.headlessbot",
    "application/vnd.aa.workflow",
})


@dataclass
class WorkflowExtractResult:
    category: str
    bot_dir: str
    manifest_path: str
    content_type: str
    output_file: str | None
    status: str
    error: str | None = None


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_unpacked_dir() -> Path:
    return (
        default_workspace_root()
        / "Test"
        / "botstore_deep"
        / "selected_task1_candidates_unpacked"
        / "_by_original_3_categories"
    )


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def sanitize_filename(name: str) -> str:
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return name


def collect_bot_dirs(root_dir: Path) -> list[tuple[str, Path]]:
    bot_dirs: list[tuple[str, Path]] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            if (bot_dir / MANIFEST_FILENAME).exists():
                bot_dirs.append((category, bot_dir))
    return bot_dirs


def find_file_by_name(bot_dir: Path, filename: str) -> Path | None:
    # A taskbot file and its containing package folder often share the same name
    # (e.g. ".../Excel Operation/Excel Operation") — rglob matches the directory too,
    # and without is_file() the directory can sort first and get opened as a file.
    matches = sorted(path for path in bot_dir.rglob(filename) if path.is_file())
    return matches[0] if matches else None


def dependency_filenames(manifest_path_value: str | None) -> str | None:
    if not manifest_path_value:
        return None
    return manifest_path_value.split("\\")[-1]


def process_bot(category: str, bot_dir: Path, root_dir: Path, dataset_dir: Path) -> list[WorkflowExtractResult]:
    bot_dir_rel = str(bot_dir.relative_to(root_dir))
    manifest_path = bot_dir / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [WorkflowExtractResult(
            category=category, bot_dir=bot_dir_rel, manifest_path="", content_type="",
            output_file=None, status="failed", error=f"manifest unreadable: {type(exc).__name__}: {exc}",
        )]

    workflow_files = [f for f in manifest.get("files", []) if f.get("contentType") in WORKFLOW_CONTENT_TYPES]
    if not workflow_files:
        return []

    workflows_dir = dataset_dir / category / bot_dir.name / "workflows"
    results: list[WorkflowExtractResult] = []
    index_entries: list[dict] = []

    for file_entry in workflow_files:
        manifest_path_value = file_entry.get("path") or ""
        filename = manifest_path_value.split("\\")[-1] if manifest_path_value else None
        content_type = file_entry.get("contentType", "")
        if not filename:
            results.append(WorkflowExtractResult(
                category=category, bot_dir=bot_dir_rel, manifest_path=manifest_path_value,
                content_type=content_type, output_file=None, status="failed", error="empty path in manifest",
            ))
            continue

        source_path = find_file_by_name(bot_dir, filename)
        if source_path is None:
            results.append(WorkflowExtractResult(
                category=category, bot_dir=bot_dir_rel, manifest_path=manifest_path_value,
                content_type=content_type, output_file=None, status="missing", error=f"{filename} not found under {bot_dir_rel}",
            ))
            continue

        try:
            workflow_data = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append(WorkflowExtractResult(
                category=category, bot_dir=bot_dir_rel, manifest_path=manifest_path_value,
                content_type=content_type, output_file=None, status="failed", error=f"{type(exc).__name__}: {exc}",
            ))
            continue

        output_name = f"{sanitize_filename(filename)}.json"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / output_name).write_text(
            json.dumps(workflow_data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8",
        )
        index_entries.append({
            "filename": filename,
            "content_type": content_type,
            "output_file": f"workflows/{output_name}",
            "manual_dependencies": [dependency_filenames(p) for p in file_entry.get("manualDependencies", [])],
            "scanned_dependencies": [dependency_filenames(p) for p in file_entry.get("scannedDependencies", [])],
        })
        results.append(WorkflowExtractResult(
            category=category, bot_dir=bot_dir_rel, manifest_path=manifest_path_value,
            content_type=content_type, output_file=f"workflows/{output_name}", status="created",
        ))

    if index_entries:
        index_path = dataset_dir / category / bot_dir.name / "workflow_index.json"
        index_path.write_text(
            json.dumps({"bot_dir": bot_dir_rel, "workflows": index_entries}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return results


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Workflow Extraction Report",
        "",
        "For each bot, pulls files whose manifest.json contentType marks them as an actual "
        "action-sequence workflow (taskbot/aiagent/headlessbot/workflow — not form/prompt) and "
        "writes a pretty-printed copy under dataset/<category>/<bot_dir>/workflows/, plus a "
        "workflow_index.json recording each file's declared dependencies.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Source root: `{payload['root_dir']}`",
        f"- Dataset root: `{payload['dataset_dir']}`",
        f"- Total workflow files: `{summary['total']}`",
        f"- Created: `{summary['created']}`",
        f"- Missing: `{summary['missing']}`",
        f"- Failed: `{summary['failed']}`",
    ]
    if summary["missing"] or summary["failed"]:
        lines.extend(["", "## Issues", ""])
        for row in payload["rows"]:
            if row["status"] in ("missing", "failed"):
                lines.append(f"- `{row['bot_dir']}` ({row['content_type']}): {row['status']} — {row['error']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract workflow-type files (per manifest.json contentType) into the curated goldset dataset/ folder."
    )
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")
    dataset_dir = args.dataset_dir.resolve()

    results: list[WorkflowExtractResult] = []
    for category, bot_dir in collect_bot_dirs(root_dir):
        results.extend(process_bot(category, bot_dir, root_dir, dataset_dir))

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "dataset_dir": str(dataset_dir),
        "summary": {
            "total": len(results),
            "created": sum(1 for row in results if row.status == "created"),
            "missing": sum(1 for row in results if row.status == "missing"),
            "failed": sum(1 for row in results if row.status == "failed"),
        },
        "rows": [asdict(row) for row in results],
    }
    json_output = args.json_output or (dataset_dir / "workflow_extraction_report.json")
    md_output = args.md_output or (dataset_dir / "workflow_extraction_report.md")
    dataset_dir.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))
    if payload["summary"]["missing"] or payload["summary"]["failed"]:
        raise SystemExit("Some workflow files were missing or failed to extract. Review the report.")


if __name__ == "__main__":
    main()
