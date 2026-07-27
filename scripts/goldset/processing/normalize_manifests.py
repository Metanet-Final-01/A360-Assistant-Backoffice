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
NORMALIZED_FILENAME = "manifest.normalized.json"

# Top-level manifest fields that are lists of dicts with a natural sort key.
# Sorting these makes diffs between bots/runs stable without changing what the
# manifest means (order within these lists is not semantically load-bearing —
# unlike, say, workflow step order, which this script never touches).
SORT_KEY_BY_FIELD = {
    "files": "path",
    "packages": "name",
}


@dataclass
class NormalizeResult:
    category: str
    bot_dir: str
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


def normalize_manifest(data: dict) -> dict:
    """Return a pretty/sorted copy of a manifest. Never drops or rewrites values —
    only key order (via sort_keys at dump time) and the order of specific
    list-of-dict fields (SORT_KEY_BY_FIELD) change, both purely cosmetic."""
    normalized = dict(data)
    for field, sort_key in SORT_KEY_BY_FIELD.items():
        items = normalized.get(field)
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            normalized[field] = sorted(items, key=lambda item: (item.get(sort_key) is None, item.get(sort_key)))
    return normalized


def collect_manifest_paths(root_dir: Path) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            manifest_path = bot_dir / MANIFEST_FILENAME
            if manifest_path.exists():
                paths.append((category, bot_dir))
    return paths


def process_bot(category: str, bot_dir: Path, root_dir: Path, output_root: Path | None = None) -> NormalizeResult:
    bot_dir_rel = str(bot_dir.relative_to(root_dir))
    manifest_path = bot_dir / MANIFEST_FILENAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return NormalizeResult(category=category, bot_dir=bot_dir_rel, status="failed", error=f"{type(exc).__name__}: {exc}")

    normalized = normalize_manifest(data)
    normalized_text = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True)
    # Default: write next to the original (in-place, backward compatible). When
    # output_root is given (curated dataset export), mirror category/bot_dir under it
    # instead — the raw Test/ tree stays untouched either way.
    destination_dir = (output_root / category / bot_dir.name) if output_root else bot_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    (destination_dir / NORMALIZED_FILENAME).write_text(normalized_text, encoding="utf-8")
    return NormalizeResult(category=category, bot_dir=bot_dir_rel, status="created")


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Manifest Normalization Report",
        "",
        f"For each bot with a `{MANIFEST_FILENAME}`, writes a pretty-printed, key-sorted "
        f"`{NORMALIZED_FILENAME}` next to it. The original `{MANIFEST_FILENAME}` is never modified.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Root: `{payload['root_dir']}`",
        f"- Total manifests: `{summary['total']}`",
        f"- Created: `{summary['created']}`",
        f"- Failed: `{summary['failed']}`",
    ]
    if summary["failed"]:
        lines.extend(["", "## Failed", ""])
        for row in payload["rows"]:
            if row["status"] == "failed":
                lines.append(f"- `{row['bot_dir']}`: {row['error']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Write a pretty-printed, key-sorted {NORMALIZED_FILENAME} next to each bot's {MANIFEST_FILENAME}."
    )
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument(
        "--output-root", type=Path,
        help="Write manifest.normalized.json under <output-root>/<category>/<bot_dir>/ "
        "instead of next to the original manifest.json (e.g. the curated goldset dataset/ folder).",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")
    output_root = args.output_root.resolve() if args.output_root else None

    results = [
        process_bot(category, bot_dir, root_dir, output_root)
        for category, bot_dir in collect_manifest_paths(root_dir)
    ]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "summary": {
            "total": len(results),
            "created": sum(1 for row in results if row.status == "created"),
            "failed": sum(1 for row in results if row.status == "failed"),
        },
        "rows": [asdict(row) for row in results],
    }
    json_output = args.json_output or (root_dir / "manifest_normalization_report.json")
    md_output = args.md_output or (root_dir / "manifest_normalization_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))
    if payload["summary"]["failed"]:
        raise SystemExit("Some manifests failed to normalize. Review the report.")


if __name__ == "__main__":
    main()
