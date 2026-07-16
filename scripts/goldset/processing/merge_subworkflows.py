from __future__ import annotations

import argparse
import json
from pathlib import Path

# Builds a single, self-contained goldset for a main workflow that calls sub-workflows,
# by replacing every TaskBot.runTask step with the referenced sub-workflow's own
# `steps` (recursively). This is a *processing* step: it produces a new artifact
# (`<name>.merged.goldset.json`), not a report.
#
# When to use this: only when processing/resolve_subtask_coverage.py has confirmed a
# main workflow's transitive_fully_covered is True AND it actually has resolved
# sub-workflows (resolved_subtask_files is non-empty) -- i.e. there is a real bundle to
# merge, not a single self-contained file. As of this script's creation, 0 of the
# corpus's currently-eligible candidates need this (none of them call any real
# TaskBot.runTask), so this has not been run against real data yet -- built ahead of
# need, per explicit instruction, for the next batch of goldset candidates where a main
# workflow's sub-workflows are genuinely RAG-covered.
#
# What "merge" means precisely: the TaskBot.runTask step itself is dropped and replaced
# in-place by the sub-workflow's own `steps` list (spliced into the same position in
# the parent's step sequence) -- the call becomes its callee's actual body, exactly
# once, at the point it was called from. A sub-workflow reached from two different call
# sites is inlined at each site separately (no shared/cached single copy), since each
# call site is a distinct point in the resulting linear/structured flow.

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def is_runtask(step: dict) -> bool:
    return step.get("type") == "action" and step.get("package") == "TaskBot" and step.get("action") == "runTask"


def taskbot_target_stem(step: dict) -> str | None:
    import urllib.parse

    for attr in step.get("attributes", []) or []:
        if attr.get("name") != "taskbot":
            continue
        file_str = (attr.get("value") or {}).get("taskbotFile", {}).get("string")
        if not file_str:
            continue
        return urllib.parse.unquote(file_str).rstrip("/").split("/")[-1]
    return None


def load_goldset(workflows_dir: Path, stem: str) -> dict | None:
    path = workflows_dir / f"{stem}.goldset.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def merge_steps(steps: list[dict], workflows_dir: Path, visited: set[str]) -> list[dict]:
    """Returns a new step list with every TaskBot.runTask step replaced by its target's
    own (recursively merged) steps. `visited` prevents infinite recursion on a cycle --
    if a cycle is hit, the runTask step is left as-is (not expanded) rather than
    silently dropped, so a merged output never silently loses a call it couldn't
    resolve."""
    merged: list[dict] = []
    for step in steps:
        if is_runtask(step):
            target_stem = taskbot_target_stem(step)
            if target_stem and target_stem not in visited:
                target_goldset = load_goldset(workflows_dir, target_stem)
                if target_goldset is not None:
                    merged.extend(
                        merge_steps(target_goldset.get("steps", []) or [], workflows_dir, visited | {target_stem})
                    )
                    continue
            # Unresolved reference or cycle guard tripped -- keep the call visible
            # rather than pretending it isn't there.
            merged.append(step)
            continue

        step = dict(step)
        if "steps" in step:
            step["steps"] = merge_steps(step.get("steps") or [], workflows_dir, visited)
        if "branches" in step:
            step["branches"] = [
                {**branch, "steps": merge_steps(branch.get("steps") or [], workflows_dir, visited)}
                for branch in step.get("branches") or []
            ]
        merged.append(step)
    return merged


def merge_workflow(workflows_dir: Path, main_stem: str) -> dict:
    main_goldset = load_goldset(workflows_dir, main_stem)
    if main_goldset is None:
        raise SystemExit(f"No goldset found for {main_stem!r} in {workflows_dir}")
    merged_steps = merge_steps(main_goldset.get("steps", []) or [], workflows_dir, visited={main_stem})
    return {
        "source_file": main_goldset.get("source_file"),
        "merged_from": main_stem,
        "triggers": main_goldset.get("triggers", []),
        "steps": merged_steps,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge a main workflow's TaskBot.runTask calls into one self-contained goldset."
    )
    parser.add_argument("bot_dir", type=Path, help="Path to dataset/<category>/<bot_dir>/")
    parser.add_argument("main_stem", type=str, help="Main workflow's filename, without .goldset.json")
    parser.add_argument("--output", type=Path, help="Output path (default: <bot_dir>/workflows/<main_stem>.merged.goldset.json)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workflows_dir = args.bot_dir / "workflows"
    merged = merge_workflow(workflows_dir, args.main_stem)
    output_path = args.output or (workflows_dir / f"{args.main_stem}.merged.goldset.json")
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "step_count": len(merged["steps"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
