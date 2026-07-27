from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)


@dataclass
class JarOccurrence:
    category: str
    bot_dir: str
    jar_name: str
    path: str
    size_bytes: int
    class_count: int | None
    has_a360_command_classes: bool
    top_entries: list[str]
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


def classify_jar_name(jar_name: str) -> str:
    name = jar_name.lower()
    if name.startswith("bot-command-"):
        return "standard_command_package"
    if any(token in name for token in ("excel", "csv", "htmltable")):
        return "custom_command_package_data_table_excel"
    if any(token in name for token in ("browser", "webautomation")):
        return "custom_command_package_web"
    if any(token in name for token in ("pdf", "word")):
        return "custom_command_package_document"
    if any(token in name for token in ("teams", "twilio", "servicenow", "credential")):
        return "custom_command_package_external_service"
    if any(token in name for token in ("json", "system", "variables", "information")):
        return "custom_command_package_utility"
    return "custom_command_package_unknown"


def inspect_jar(jar_path: Path, bot_dir: Path, category_dir: Path, root_dir: Path) -> JarOccurrence:
    top_entries: list[str] = []
    class_count: int | None = None
    has_a360_command_classes = False
    error: str | None = None
    try:
        with zipfile.ZipFile(jar_path) as archive:
            names = archive.namelist()
            class_count = sum(1 for name in names if name.endswith(".class"))
            has_a360_command_classes = any(name.startswith("com/automationanywhere/botcommand/") for name in names)
            grouped_entries: set[str] = set()
            for name in names:
                if name.endswith("/") or name.upper() == "META-INF/MANIFEST.MF":
                    continue
                parts = name.split("/")
                grouped_entries.add("/".join(parts[:3]) if len(parts) >= 3 else name)
            top_entries = sorted(grouped_entries)[:20]
    except Exception as exc:  # noqa: BLE001 - keep reporting even when one archive is malformed.
        error = f"{type(exc).__name__}: {exc}"

    return JarOccurrence(
        category=category_dir.name,
        bot_dir=str(bot_dir.relative_to(root_dir)),
        jar_name=jar_path.name,
        path=str(jar_path.relative_to(root_dir)),
        size_bytes=jar_path.stat().st_size,
        class_count=class_count,
        has_a360_command_classes=has_a360_command_classes,
        top_entries=top_entries,
        error=error,
    )


def collect_occurrences(root_dir: Path) -> list[JarOccurrence]:
    occurrences: list[JarOccurrence] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            for jar_path in sorted(bot_dir.rglob("*.jar")):
                if jar_path.name.startswith("bot-command-"):
                    continue
                occurrences.append(inspect_jar(jar_path, bot_dir, category_dir, root_dir))
    return occurrences


def summarize(occurrences: list[JarOccurrence]) -> dict:
    by_name: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "total_size_bytes": 0,
            "categories": set(),
            "bots": set(),
            "classification": "",
            "has_a360_command_classes": False,
            "class_count_total": 0,
            "errors": [],
        }
    )
    for item in occurrences:
        row = by_name[item.jar_name]
        row["count"] += 1
        row["total_size_bytes"] += item.size_bytes
        row["categories"].add(item.category)
        row["bots"].add(item.bot_dir)
        row["classification"] = classify_jar_name(item.jar_name)
        row["has_a360_command_classes"] = row["has_a360_command_classes"] or item.has_a360_command_classes
        row["class_count_total"] += item.class_count or 0
        if item.error:
            row["errors"].append({"path": item.path, "error": item.error})

    unique_jars = []
    for jar_name, row in sorted(by_name.items(), key=lambda kv: (-kv[1]["count"], kv[0].lower())):
        unique_jars.append(
            {
                "jar_name": jar_name,
                "classification": row["classification"],
                "count": row["count"],
                "total_size_mb": round(row["total_size_bytes"] / 1024 / 1024, 2),
                "categories": sorted(row["categories"]),
                "bots": sorted(row["bots"]),
                "has_a360_command_classes": row["has_a360_command_classes"],
                "class_count_total": row["class_count_total"],
                "errors": row["errors"],
                "recommendation": "drop_binary_keep_metadata",
            }
        )

    return {
        "total_occurrences": len(occurrences),
        "unique_jars": len(unique_jars),
        "total_size_mb": round(sum(item.size_bytes for item in occurrences) / 1024 / 1024, 2),
        "bots_with_remaining_jars": len({item.bot_dir for item in occurrences}),
        "unique_jar_summary": unique_jars,
    }


def write_markdown_report(report_path: Path, payload: dict) -> None:
    lines = [
        "# Remaining Non-Command JAR Analysis",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Root: `{payload['root_dir']}`",
        f"- Total occurrences: `{payload['summary']['total_occurrences']}`",
        f"- Unique JARs: `{payload['summary']['unique_jars']}`",
        f"- Total size: `{payload['summary']['total_size_mb']} MB`",
        f"- Bots with remaining JARs: `{payload['summary']['bots_with_remaining_jars']}`",
        "",
        "## Recommendation",
        "",
        "Keep JAR names, versions, occurrence paths, and classifications as metadata. Drop binary JAR files from the cleaned goldset unless a later manual review explicitly needs bytecode-level evidence.",
        "",
        "## Unique JARs",
        "",
        "| JAR | Count | Size MB | Classification | Recommendation | Sample Bots |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in payload["summary"]["unique_jar_summary"]:
        sample_bots = "<br>".join(item["bots"][:4])
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item['jar_name']}`",
                    str(item["count"]),
                    str(item["total_size_mb"]),
                    f"`{item['classification']}`",
                    f"`{item['recommendation']}`",
                    sample_bots,
                ]
            )
            + " |"
        )
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze non-bot-command JAR files in selected Bot Store goldset candidates.")
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    occurrences = collect_occurrences(root_dir)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "summary": summarize(occurrences),
        "occurrences": [asdict(item) for item in occurrences],
    }

    json_output = args.json_output or (root_dir / "remaining_jar_analysis.json")
    md_output = args.md_output or (root_dir / "remaining_jar_analysis.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(md_output, payload)
    print(
        json.dumps(
            {
                "json": str(json_output),
                "markdown": str(md_output),
                **payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
