from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Plain numeric ranges only -- no qualitative "easy/hard" labeling. What counts as
# "hard" depends on the eval consuming this, not on the action count alone (see
# PROVENANCE.md: a 5-action non-entry login sub-task isn't "easy", it's out of scope).
BUCKET_EDGES = [(1, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, None)]


@dataclass
class ActionCountRow:
    bot_name: str
    source_file: str
    action_count: int
    canonical_action_count: int | None
    is_entry_workflow: bool


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def bucket_label(n: int) -> str:
    for lo, hi in BUCKET_EDGES:
        if hi is None:
            if n >= lo:
                return f"{lo}+"
        elif lo <= n <= hi:
            return f"{lo}-{hi}"
    raise ValueError(f"action_count {n} did not match any bucket")


def load_rows(dataset_dir: Path, rag_covered_only: bool) -> list[ActionCountRow]:
    coverage_path = dataset_dir / "rag_action_coverage_report.json"
    shortlist_path = dataset_dir / "eval_candidate_shortlist.json"
    if not coverage_path.exists() or not shortlist_path.exists():
        raise SystemExit(
            f"Missing input(s) -- run check_actions_in_rag.py and select_eval_candidates.py first: "
            f"{coverage_path}, {shortlist_path}"
        )

    coverage_rows = json.loads(coverage_path.read_text(encoding="utf-8"))["rows"]
    candidates = json.loads(shortlist_path.read_text(encoding="utf-8"))["all_candidates"]
    candidates_by_key = {(c["bot_name"], c["source_file"]): c for c in candidates}

    rows: list[ActionCountRow] = []
    for cov in coverage_rows:
        if rag_covered_only and not cov["fully_covered"]:
            continue
        key = (cov["bot_name"], cov["source_file"])
        candidate = candidates_by_key.get(key)
        if candidate is None:
            continue
        rows.append(ActionCountRow(
            bot_name=key[0], source_file=key[1],
            action_count=candidate["action_count"],
            canonical_action_count=candidate.get("canonical_action_count"),
            is_entry_workflow=candidate["is_entry_workflow"],
        ))
    return rows


def summarize(rows: list[ActionCountRow]) -> dict:
    counts = [r.action_count for r in rows]
    entry_counts = [r.action_count for r in rows if r.is_entry_workflow]

    by_bucket: dict[str, dict[str, int]] = {}
    for r in rows:
        b = bucket_label(r.action_count)
        entry = by_bucket.setdefault(b, {"total": 0, "entry": 0})
        entry["total"] += 1
        if r.is_entry_workflow:
            entry["entry"] += 1
    # Report buckets in ascending numeric order, not insertion order.
    ordered_buckets = {}
    for lo, hi in BUCKET_EDGES:
        label = f"{lo}+" if hi is None else f"{lo}-{hi}"
        if label in by_bucket:
            ordered_buckets[label] = by_bucket[label]

    return {
        "total_files": len(rows),
        "entry_files": len(entry_counts),
        "action_count": {
            "min": min(counts) if counts else None,
            "max": max(counts) if counts else None,
            "mean": round(statistics.mean(counts), 2) if counts else None,
            "median": statistics.median(counts) if counts else None,
        },
        "action_count_entry_only": {
            "min": min(entry_counts) if entry_counts else None,
            "max": max(entry_counts) if entry_counts else None,
            "mean": round(statistics.mean(entry_counts), 2) if entry_counts else None,
            "median": statistics.median(entry_counts) if entry_counts else None,
        },
        "by_bucket": ordered_buckets,
    }


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    ac = summary["action_count"]
    ac_entry = summary["action_count_entry_only"]
    lines = [
        "# Action Count Distribution",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Scope: `{payload['scope']}`",
        f"- Total files: `{summary['total_files']}` (entry-level: `{summary['entry_files']}`)",
        "",
        "## action_count (full recursive count, all branches)",
        "",
        f"- min / max: `{ac['min']}` / `{ac['max']}`",
        f"- mean / median: `{ac['mean']}` / `{ac['median']}`",
        "",
        "## action_count, entry-level files only",
        "",
        f"- min / max: `{ac_entry['min']}` / `{ac_entry['max']}`",
        f"- mean / median: `{ac_entry['mean']}` / `{ac_entry['median']}`",
        "",
        "## Distribution by action_count bucket",
        "",
        "| Bucket | Total | Entry-level |",
        "| --- | ---: | ---: |",
    ]
    for label, counts in summary["by_bucket"].items():
        lines.append(f"| {label} | {counts['total']} | {counts['entry']} |")
    lines.extend(["", "## Rows", "", "| Bot | File | action_count | canonical_action_count | Entry? |", "| --- | --- | ---: | ---: | --- |"])
    for row in sorted(payload["rows"], key=lambda r: r["action_count"]):
        lines.append(
            f"| `{row['bot_name']}` | `{row['source_file']}` | {row['action_count']} | "
            f"{row['canonical_action_count']} | {'yes' if row['is_entry_workflow'] else 'no'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate action_count distribution stats over the goldset corpus.")
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument(
        "--all-files", action="store_true",
        help="Include all 124 unique goldset files, not just the 42 RAG-fully-covered ones (default: covered only).",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    rag_covered_only = not args.all_files

    rows = load_rows(dataset_dir, rag_covered_only)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "RAG-fully-covered unique files only" if rag_covered_only else "all unique goldset files",
        "summary": summarize(rows),
        "rows": [asdict(r) for r in rows],
    }
    json_output = args.json_output or (dataset_dir / "action_count_stats.json")
    md_output = args.md_output or (dataset_dir / "action_count_stats.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
