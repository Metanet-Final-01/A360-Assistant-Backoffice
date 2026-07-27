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

# This file previously ranked candidates with a hand-picked score formula (system-
# diversity weights, fidelity bonus, control-flow bonus, size penalties, a
# Recorder/AISense-"capture ratio" penalty) and hard cutoffs (3-60 actions, capture
# ratio <=50%). On review, every one of those was a self-invented judgment call with
# no real evidence behind the specific numbers, and the capture-ratio premise itself
# turned out to be wrong: Recorder.capture/AISense.capture carry a real, structured
# `<uiType>Action` attribute (CLICK/SETTEXT/SELECTITEMBYTEXT/CHECK/...) alongside the
# opaque uiObject blob -- confirmed across all 598 capture nodes in the corpus, 14
# distinct UI-object action fields, closed value sets -- so "capture repeats are
# indistinguishable" was false; the goldset just wasn't labeling them by that field.
# Removed entirely rather than patched. What's left below are only filters grounded in
# a verifiable fact or an explicit requirement, not a preference:
#
# - is_main_workflow ("main" vs "sub-workflow", not "entry"/"non-entry" -- confirmed
#   during discussion that graph-theory naming wasn't legible on first read): is this
#   file NOT the target of any other file's actual TaskBot.runTask node in the same
#   bot -- a structural fact about who really calls what, computed by
#   processing/resolve_subtask_coverage.py from the *parsed workflow content*, not
#   from workflow_index.json's manifest-derived manual/scanned dependency fields.
#   Switched away from the manifest fields after finding they're unreliable: AA's own
#   manifest scannedDependencies listed 0167_outlook-email-notifier's single workflow
#   file as depending on ITSELF even though that file's actual content has zero
#   TaskBot.runTask nodes at all, and 0338_lettergenerationbot's manifest
#   manualDependencies formed a 2-file cycle (LetterGenerationBot <-> ProofOfFunds_
#   Process) even though neither file -- nor the bot's third file, SendPOF_Email --
#   contains a single real TaskBot.runTask node. Both are legitimate, independent
#   workflows the manifest metadata was simply wrong about.
# - rag_fully_covered (transitive): explicit user requirement ("every action must
#   exist in the RAG catalog"), checked against real ingested catalog data, resolved
#   recursively through TaskBot.runTask references
#   (processing/resolve_subtask_coverage.py).
# - canonical_action_count > 0: not a size preference -- a file whose WorFBench
#   canonical path collapses to zero actions has literally nothing left to compare
#   against once branches are flattened, which is a hard logical floor, not a chosen
#   cutoff.
#
# No scoring, no ranking, no capture-ratio, no upper/lower action-count band picked by
# feel. Candidates are reported in full; `dataset/action_count_stats.md` is where size
# gets looked at, as a report, not a filter.


@dataclass
class Candidate:
    bot_name: str
    source_file: str
    categories_seen_in: list[str]
    action_count: int
    canonical_action_count: int | None
    distinct_packages: list[str]
    is_main_workflow: bool
    rag_fully_covered: bool | None
    rag_missing_actions: list[str]


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def collect_action_packages(steps: list[dict], packages: set[str]) -> int:
    """Returns the leaf action count and fills `packages` with every package name
    seen on an action/container step, recursing through steps and every branch."""
    count = 0
    for step in steps:
        step_type = step.get("type")
        if step_type in ("action", "container"):
            packages.add(step.get("package"))
            if step_type == "action":
                count += 1
        count += collect_action_packages(step.get("steps", []) or [], packages)
        for branch in step.get("branches", []) or []:
            count += collect_action_packages(branch.get("steps", []) or [], packages)
    return count


def load_subtask_report(dataset_dir: Path) -> dict[tuple[str, str], dict]:
    """Uses processing/resolve_subtask_coverage.py's output for two things: the
    *transitive* RAG coverage (not analysis/check_actions_in_rag.py's own-actions-only
    coverage -- a file whose own actions are all covered can still hide uncovered work
    behind an opaque TaskBot.runTask call to an unresolved or uncovered sub-task,
    confirmed for real on 0188_invoicely-assistant-bot---main and 5 others), and
    is_real_call_graph_root (main vs sub-workflow, computed from actually-parsed
    TaskBot.runTask nodes rather than the manifest's dependency fields -- see the
    module docstring for why those fields can't be trusted)."""
    path = dataset_dir / "subtask_transitive_coverage_report.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {(row["bot_name"], row["source_file"]): row for row in payload["rows"]}


def load_worfbench_report(dataset_dir: Path) -> dict[tuple[str, str], dict]:
    path = dataset_dir / "worfbench_conversion_report.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[tuple[str, str], dict] = {}
    for row in payload["rows"]:
        bot_name = Path(row["bot_dir"]).name
        by_key[(bot_name, row["goldset_file"])] = row
    return by_key


def collect_candidates(dataset_dir: Path) -> list[Candidate]:
    worfbench_by_key = load_worfbench_report(dataset_dir)
    subtask_by_key = load_subtask_report(dataset_dir)

    # Dedup: the same physical bot can be shortlisted under more than one category
    # folder (see PROVENANCE.md) -- collapse to one candidate per (bot_name,
    # source_file), remembering every category it was seen under.
    merged: dict[tuple[str, str], Candidate] = {}

    for category in CATEGORY_DIRS:
        category_dir = dataset_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            workflows_dir = bot_dir / "workflows"
            if not workflows_dir.exists():
                continue
            for goldset_path in sorted(workflows_dir.glob("*.goldset.json")):
                key = (bot_dir.name, goldset_path.name)
                if key in merged:
                    merged[key].categories_seen_in.append(category)
                    continue

                goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
                packages: set[str] = set()
                action_count = collect_action_packages(goldset.get("steps", []) or [], packages)

                wf_row = worfbench_by_key.get(key)
                # Canonical single-path count (after if->then/loop-once/try+finally-only
                # flattening) -- can be much smaller than the full recursive
                # action_count above when most of a file's content lives in a branch
                # the canonical rule drops (found via 0313_sendemailwithimagehtml: 6
                # actions recursively, but only 1 -- Email.sendMail -- survives
                # flattening).
                canonical_action_count = wf_row["action_count"] if wf_row else None

                subtask_row = subtask_by_key.get(key)
                rag_fully_covered = subtask_row["transitive_fully_covered"] if subtask_row else None
                rag_missing_actions = subtask_row["transitive_missing_pairs"] if subtask_row else []
                is_main_workflow = subtask_row["is_real_call_graph_root"] if subtask_row else False

                merged[key] = Candidate(
                    bot_name=bot_dir.name, source_file=goldset_path.name, categories_seen_in=[category],
                    action_count=action_count, canonical_action_count=canonical_action_count,
                    distinct_packages=sorted(p for p in packages if p),
                    is_main_workflow=is_main_workflow, rag_fully_covered=rag_fully_covered,
                    rag_missing_actions=rag_missing_actions,
                )

    return list(merged.values())


MIN_ACTION_COUNT = 3  # user-specified floor, kept as-is (not one of the removed
                       # self-invented cutoffs -- see PROVENANCE.md audit)

# Manual, qualitative exclusions -- not a score or numeric threshold, same category as
# dropping application/vnd.aa.aiagent files below: a specific, documented reason a
# human found by reading the actual content, not a rule a future file could trip
# automatically. Keep this list short and justify every entry.
MANUALLY_EXCLUDED: dict[tuple[str, str], str] = {
    ("0313_sendemailwithimagehtml", "SendEmailWithImageHTML.goldset.json"): (
        "Of its 6 actions, only 1 (Email.sendMail, a real, specifiable action -- send "
        "an HTML birthday email with an image to named recipients) is real business "
        "content; the other 5 (Boolean.assign/LogToFile/Screen.captureDesktop/"
        "String.assign x2) only run in the generic failure-path (try/catch) branch. A "
        "business-task-definition PDF (this project's actual eval input format) never "
        "specifies generic error handling -- so the golden answer for this file is "
        "~83% content no valid business brief could ever imply, unlike other thin "
        "candidates (0137/0224/0225/0302) whose thin action count IS the real "
        "specifiable business logic (an external program call), not error-handling "
        "padding. See PROVENANCE.md."
    ),
}


def select_eligible(candidates: list[Candidate]) -> list[Candidate]:
    eligible = [
        c for c in candidates
        if c.is_main_workflow
        and c.rag_fully_covered is True
        and c.action_count >= MIN_ACTION_COUNT
        and (c.canonical_action_count is None or c.canonical_action_count > 0)
        and (c.bot_name, c.source_file) not in MANUALLY_EXCLUDED
    ]
    return sorted(eligible, key=lambda c: (c.bot_name, c.source_file))


def write_markdown(path: Path, payload: dict) -> None:
    lines = [
        "# LLM Eval Candidate Shortlist",
        "",
        "Every candidate below passes 4 filters, nothing else -- no scoring, no "
        "ranking, no hand-picked upper size band or capture-ratio penalty (an earlier "
        "version of this script had those; they were removed as self-invented judgment "
        "calls without real evidence, see PROVENANCE.md):",
        "",
        "1. **Main workflow, not a sub-workflow**: no other workflow file in the same "
        "bot contains an actual `TaskBot.runTask` node targeting this one -- computed "
        "from parsed workflow content (`processing/resolve_subtask_coverage.py`), not "
        "the manifest's manual/scanned dependency fields (those turned out to be "
        "unreliable: confirmed real self-reference and cycle cases where the manifest "
        "disagreed with the actual, real, zero-runTask workflow content -- see "
        "PROVENANCE.md). A structural fact, not a threshold.",
        "2. **RAG-transitive-covered**: every action this candidate depends on, "
        "including everything reachable through `TaskBot.runTask` references resolved "
        "recursively (`processing/resolve_subtask_coverage.py`), exists in the RAG "
        "action catalog -- the user's explicit requirement, checked against real "
        "ingested data.",
        f"3. **action_count >= {MIN_ACTION_COUNT}**: user-specified floor, kept as-is.",
        "4. **canonical_action_count > 0**: the WorFBench canonical path (after "
        "if->then/loop-once/try+finally-only flattening) isn't empty -- a logical "
        "floor (nothing to compare against otherwise), not a size preference.",
        "",
        "Candidates are listed in full, sorted by name -- not narrowed to a target "
        "count. Size (`action_count`) is reported here for context; see "
        "`action_count_stats.md` for the full distribution. Judge fitness for a "
        "specific eval run yourself from that number and the action list, rather than "
        "trusting a fabricated score.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Total unique candidates (bot_name, source_file) considered: `{payload['summary']['total_candidates']}`",
        f"- Eligible: `{payload['summary']['eligible']}`",
        "",
        "## Eligible candidates",
        "",
        "| Bot | File | action_count | canonical_action_count | Packages |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in payload["eligible"]:
        packages = ", ".join(row["distinct_packages"])
        lines.append(
            f"| `{row['bot_name']}` | `{row['source_file']}` | {row['action_count']} | "
            f"{row['canonical_action_count']} | {packages} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List goldset workflows that pass all fact-based eval-candidate filters.")
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()

    candidates = collect_candidates(dataset_dir)
    eligible = select_eligible(candidates)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total_candidates": len(candidates), "eligible": len(eligible)},
        "eligible": [asdict(c) for c in eligible],
        "all_candidates": [asdict(c) for c in sorted(candidates, key=lambda c: (c.bot_name, c.source_file))],
    }
    json_output = args.json_output or (dataset_dir / "eval_candidate_shortlist.json")
    md_output = args.md_output or (dataset_dir / "eval_candidate_shortlist.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
