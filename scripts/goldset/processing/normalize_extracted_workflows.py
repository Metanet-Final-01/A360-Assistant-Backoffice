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
OUTPUT_SUFFIX = ".goldset.json"

# Raw Automation Anywhere workflow JSON is tree-shaped: nodes can contain children
# and branches. This derived artifact keeps source metadata but normalizes that tree
# into ordered steps for scoring adapters such as pm4py and WorFBench.
TRANSPARENT_PACKAGES = frozenset({"Step"})
SKIPPED_PACKAGES = frozenset({"Comment"})
IF_PACKAGES = frozenset({"If"})
TRY_PACKAGES = frozenset({"ErrorHandler"})
LOOP_PACKAGES = frozenset({"Loop"})
BRANCH_ONLY_LOOP_PACKAGES = frozenset({"TriggerLoop"})


@dataclass
class NormalizeResult:
    category: str
    bot_dir: str
    source_file: str
    status: str
    output_file: str | None = None
    step_count: int = 0
    error: str | None = None


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def extra_binding_fields(node: dict) -> dict:
    extra: dict = {}
    if "returnTo" in node:
        extra["return_to"] = node["returnTo"]
    if "returns" in node:
        extra["returns"] = node["returns"]
    return extra


def convert_branch(branch: dict) -> dict:
    return {
        "branch": branch.get("commandName"),
        "attributes": branch.get("attributes", []),
        **extra_binding_fields(branch),
        "steps": convert_nodes(branch.get("children", []) or []),
    }


def convert_node(node: dict) -> list[dict]:
    package = node.get("packageName")

    if package in SKIPPED_PACKAGES:
        return []
    if package in TRANSPARENT_PACKAGES:
        return convert_nodes(node.get("children", []) or [])

    common = {
        "uid": node.get("uid"),
        "disabled": bool(node.get("disabled", False)),
        "attributes": node.get("attributes", []),
        **extra_binding_fields(node),
    }

    if package in IF_PACKAGES:
        return [{
            "type": "if",
            **common,
            "steps": convert_nodes(node.get("children", []) or []),
            "branches": [convert_branch(branch) for branch in node.get("branches", []) or []],
        }]
    if package in TRY_PACKAGES:
        return [{
            "type": "try",
            **common,
            "steps": convert_nodes(node.get("children", []) or []),
            "branches": [convert_branch(branch) for branch in node.get("branches", []) or []],
        }]
    if package in LOOP_PACKAGES:
        return [{
            "type": "loop",
            **common,
            "steps": convert_nodes(node.get("children", []) or []),
        }]
    if package in BRANCH_ONLY_LOOP_PACKAGES:
        return [{
            "type": "trigger_loop",
            **common,
            "branches": [convert_branch(branch) for branch in node.get("branches", []) or []],
        }]
    if node.get("children"):
        return [{
            "type": "container",
            "package": package,
            "action": node.get("commandName"),
            **common,
            "steps": convert_nodes(node.get("children", []) or []),
        }]

    return [{
        "type": "action",
        "package": package,
        "action": node.get("commandName"),
        **common,
    }]


def convert_nodes(nodes: list[dict]) -> list[dict]:
    steps: list[dict] = []
    for node in nodes:
        steps.extend(convert_node(node))
    return steps


def normalize_workflow(raw: dict, source_file: str) -> dict:
    return {
        "source_file": source_file,
        "triggers": raw.get("triggers", []),
        "steps": convert_nodes(raw.get("nodes", []) or []),
    }


def count_steps(steps: list[dict]) -> int:
    total = 0
    for step in steps:
        total += 1
        total += count_steps(step.get("steps", []) or [])
        for branch in step.get("branches", []) or []:
            total += count_steps(branch.get("steps", []) or [])
    return total


def collect_workflow_files(dataset_dir: Path) -> list[tuple[str, str, Path]]:
    entries: list[tuple[str, str, Path]] = []
    for category in CATEGORY_DIRS:
        category_dir = dataset_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            index_path = bot_dir / "workflow_index.json"
            if not index_path.exists():
                continue
            index = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in index.get("workflows", []):
                output_file = entry.get("output_file")
                if output_file:
                    entries.append((category, str(bot_dir.relative_to(dataset_dir)), bot_dir / output_file))
    return entries


def process_workflow_file(category: str, bot_dir_rel: str, workflow_path: Path) -> NormalizeResult:
    source_file = workflow_path.name
    try:
        raw = json.loads(workflow_path.read_text(encoding="utf-8"))
        normalized = normalize_workflow(raw, source_file)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return NormalizeResult(
            category=category,
            bot_dir=bot_dir_rel,
            source_file=source_file,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    output_path = workflow_path.with_name(workflow_path.stem + OUTPUT_SUFFIX)
    output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return NormalizeResult(
        category=category,
        bot_dir=bot_dir_rel,
        source_file=source_file,
        status="created",
        output_file=output_path.name,
        step_count=count_steps(normalized["steps"]),
    )


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Extracted Workflow Normalization Report",
        "",
        "For each raw workflow JSON produced by `extract_workflows.py`, writes a derived",
        "`*.goldset.json` next to it. This is not the raw source workflow; it is the",
        "ordered `steps` representation used by scoring adapters.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Dataset root: `{payload['dataset_dir']}`",
        f"- Total workflow files: `{summary['total']}`",
        f"- Created: `{summary['created']}`",
        f"- Failed: `{summary['failed']}`",
        f"- Total steps: `{summary['total_steps']}`",
    ]
    if summary["failed"]:
        lines.extend(["", "## Failed", ""])
        for row in payload["results"]:
            if row["status"] == "failed":
                lines.append(f"- `{row['bot_dir']}/{row['source_file']}`: {row['error']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize extract_workflows.py raw workflow JSONs into ordered steps for scoring."
    )
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    args = parser.parse_args()

    dataset_dir = args.dataset_dir.resolve()
    results = [
        process_workflow_file(category, bot_dir_rel, workflow_path)
        for category, bot_dir_rel, workflow_path in collect_workflow_files(dataset_dir)
    ]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "summary": {
            "total": len(results),
            "created": sum(1 for result in results if result.status == "created"),
            "failed": sum(1 for result in results if result.status == "failed"),
            "total_steps": sum(result.step_count for result in results),
        },
        "results": [asdict(result) for result in results],
    }

    json_output = args.json_output or (dataset_dir / "workflow_normalization_report.json")
    md_output = args.md_output or (dataset_dir / "workflow_normalization_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))
    if payload["summary"]["failed"]:
        raise SystemExit("Some workflow files failed to normalize. Review the report.")


if __name__ == "__main__":
    main()
