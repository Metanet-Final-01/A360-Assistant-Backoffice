from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from action_filters import action_label, is_browser_session_lifecycle_action


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parents[2]
DEFAULT_AGENT_NORMALIZED = (
    ROOT
    / "runner"
    / "logs"
    / "runner_v2_repeat_20260715_01"
    / "converted_recommendation"
    / "normalized"
    / "runner_v2_repeat_20260715_01__turnRecommend.goldset.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "evaluation" / "action_equivalence_candidates"
RULES_PATH = ROOT / "evaluation" / "action_equivalence_rules.json"
REVIEWED_PATH = ROOT / "evaluation" / "action_equivalence_reviewed_pairs.json"


def norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def words(text: str | None) -> str:
    return re.sub(r"[_\-./]+", " ", text or "").lower()


def package_part(label: str) -> str:
    return label.split(".", 1)[0] if "." in label else label


def action_part(label: str) -> str:
    return label.split(".", 1)[1] if "." in label else label


def package_family(label: str) -> str:
    package = words(package_part(label)).strip()
    if package in {"excel", "excel ms", "excel advanced"}:
        return "excel"
    if package in {"browser", "webautomation", "web automation", "recorder"}:
        return "browser"
    if package in {"error handler", "errorhandler"}:
        return "error_handler"
    if package in {"mswordpackage", "word", "microsoft word"}:
        return "word"
    return norm(package)


def action_semantics(label: str) -> set[str]:
    action = norm(action_part(label))
    groups: set[str] = set()
    if any(key in action for key in ["open", "launch", "browserpackageopenaction", "cloudexcelopen"]):
        groups.add("open")
    if any(key in action for key in ["close", "browserpackagecloseaction", "exceladvancedpackagecloseaction"]):
        groups.add("close")
    if any(
        key in action
        for key in [
            "getmultiplecells",
            "readexcelrow",
            "readrow",
            "packagereadrowaction",
            "query",
            "extract",
            "capture",
        ]
    ):
        groups.add("read")
    if action.startswith("get") and "getcurrent" not in action:
        groups.add("read")
    if any(
        key in action
        for key in [
            "setcell",
            "packagesetcellaction",
            "assign",
            "put",
            "replace",
            "create",
        ]
    ):
        groups.add("write")
    if any(key in action for key in ["delete", "deleting", "remove"]):
        groups.add("delete")
    if any(key in action for key in ["runjavascript", "runfunction", "runcsharpdll", "usingrunfunctionaction"]):
        groups.add("run")
    if any(key in action for key in ["try", "catch", "errorhandlertry", "errorhandlercatch"]):
        groups.add("error_flow")
    if any(key in action for key in ["sendmail", "cloudusingsendaction"]):
        groups.add("send")
    return groups


def is_control_flow_marker_action(package: str | None, action: str | None) -> bool:
    package_norm = (package or "").strip().lower()
    action_norm = (action or "").strip().lower()
    if package_norm not in {"if", "loop", "error handler", "errorhandler"}:
        return False
    return any(token in action_norm for token in ["if", "loop", "try", "catch", "finally", "errorhandler"])


def conflicting_semantics(left: set[str], right: set[str]) -> bool:
    return (
        bool({"open", "close"} <= (left | right) and left != right)
        or bool({"read", "write"} <= (left | right) and left != right)
        or bool({"write", "delete"} <= (left | right) and left != right)
    )


def walk_steps(steps: list[dict[str, Any]] | None):
    for step in steps or []:
        yield step
        yield from walk_steps(step.get("steps"))
        for branch in step.get("branches") or []:
            yield from walk_steps(branch.get("steps"))


def collect_actions(path: Path, case_id: str | None = None) -> tuple[Counter[str], dict[str, set[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    cases: dict[str, set[str]] = defaultdict(set)
    for step in walk_steps(payload.get("steps")):
        if step.get("type") not in {"action", "container"}:
            continue
        package = step.get("package")
        action = step.get("action")
        if not package or not action:
            continue
        label = action_label(package, action)
        if label == "Recommendation.businessStep":
            continue
        if is_browser_session_lifecycle_action(package, action):
            continue
        if is_control_flow_marker_action(package, action):
            continue
        counts[label] += 1
        if case_id:
            cases[label].add(case_id)
    return counts, cases


def load_gold_actions() -> tuple[Counter[str], dict[str, set[str]]]:
    counts: Counter[str] = Counter()
    cases: dict[str, set[str]] = defaultdict(set)
    for path in sorted((ROOT / "eval_inputs" / "normalized_workflows_13").glob("*/*.goldset.json")):
        path_counts, path_cases = collect_actions(path, path.parent.name)
        counts.update(path_counts)
        for label, case_ids in path_cases.items():
            cases[label] |= case_ids
    return counts, cases


def load_equivalence_rules() -> tuple[set[tuple[str, str]], dict[str, str]]:
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str]] = set()
    member_to_canonical: dict[str, str] = {}
    for group in payload.get("equivalence_groups", []) or []:
        canonical = group["canonical"]
        members = set(group.get("members", [])) | {canonical}
        for member in members:
            member_to_canonical[member] = canonical
        for left in members:
            for right in members:
                if left != right:
                    pairs.add((left, right))
    return pairs, member_to_canonical


def load_reviewed_pairs() -> set[tuple[str, str]]:
    if not REVIEWED_PATH.exists():
        return set()
    payload = json.loads(REVIEWED_PATH.read_text(encoding="utf-8"))
    return {
        (row["gold_action"], row["stored_action"])
        for row in payload.get("reviewed_pairs", []) or []
    }


def build_rows(agent_normalized_paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    gold_counts, gold_cases = load_gold_actions()
    agent_counts: Counter[str] = Counter()
    for agent_normalized in agent_normalized_paths:
        path_counts, _ = collect_actions(agent_normalized)
        agent_counts.update(path_counts)
    accepted_pairs, member_to_canonical = load_equivalence_rules()
    reviewed_pairs = load_reviewed_pairs()

    rows: list[dict[str, Any]] = []
    for agent_action in sorted(agent_counts):
        agent_family = package_family(agent_action)
        agent_semantics = action_semantics(agent_action)
        scored: list[dict[str, Any]] = []
        for gold_action in sorted(gold_counts):
            if agent_action == gold_action:
                continue
            is_accepted = (gold_action, agent_action) in accepted_pairs or (agent_action, gold_action) in accepted_pairs
            was_reviewed = (gold_action, agent_action) in reviewed_pairs or (agent_action, gold_action) in reviewed_pairs
            if was_reviewed and not is_accepted:
                continue

            gold_family = package_family(gold_action)
            gold_semantics = action_semantics(gold_action)
            same_family = agent_family == gold_family
            overlap = agent_semantics & gold_semantics
            name_similarity = SequenceMatcher(None, norm(action_part(agent_action)), norm(action_part(gold_action))).ratio()

            if is_accepted:
                status = "accepted_rule"
                score = 1.0
            elif not same_family or not overlap or conflicting_semantics(agent_semantics, gold_semantics):
                continue
            else:
                score = 0.65 + (0.25 * name_similarity) + (0.1 * len(overlap))
                status = "strong_candidate" if name_similarity >= 0.25 else "possible_candidate"

            scored.append(
                {
                    "agent_action": agent_action,
                    "agent_count": agent_counts[agent_action],
                    "gold_action": gold_action,
                    "gold_count": gold_counts[gold_action],
                    "gold_cases": ";".join(sorted(gold_cases[gold_action])),
                    "candidate_status": status,
                    "already_accepted": is_accepted,
                    "was_previously_reviewed": was_reviewed,
                    "agent_canonical": member_to_canonical.get(agent_action, agent_action),
                    "gold_canonical": member_to_canonical.get(gold_action, gold_action),
                    "agent_family": agent_family,
                    "gold_family": gold_family,
                    "semantic_overlap": ";".join(sorted(overlap)),
                    "name_similarity": round(name_similarity, 4),
                    "combined_score": round(score, 4),
                }
            )

        scored.sort(
            key=lambda row: (
                row["candidate_status"] != "accepted_rule",
                row["candidate_status"] != "strong_candidate",
                -row["combined_score"],
                row["gold_action"],
            )
        )
        rows.extend(scored[:5])

    unmatched = [
        {
            "agent_action": agent_action,
            "agent_count": agent_counts[agent_action],
            "agent_family": package_family(agent_action),
            "agent_semantics": ";".join(sorted(action_semantics(agent_action))),
        }
        for agent_action in sorted(agent_counts)
        if not any(row["agent_action"] == agent_action for row in rows)
    ]

    summary = {
        "gold_unique_actions": len(gold_counts),
        "agent_unique_actions": len(agent_counts),
        "candidate_rows": len(rows),
        "accepted_rule_rows": sum(row["candidate_status"] == "accepted_rule" for row in rows),
        "strong_candidate_rows": sum(row["candidate_status"] == "strong_candidate" for row in rows),
        "possible_candidate_rows": sum(row["candidate_status"] == "possible_candidate" for row in rows),
        "unmatched_agent_actions": len(unmatched),
        "agent_sources": [str(path) for path in agent_normalized_paths],
        "note": "Candidate pool is limited to actions present in the agent normalized output and actions present in the 13 gold normalized workflows. RAG DB actions are not used.",
    }
    return rows, unmatched, summary


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build action-equivalence candidates limited to agent and gold actions.")
    parser.add_argument("--agent-normalized", type=Path, action="append", default=[])
    parser.add_argument("--agent-normalized-glob", action="append", default=[])
    parser.add_argument("--combined-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    agent_paths = list(args.agent_normalized)
    if args.combined_manifest:
        manifest = json.loads(args.combined_manifest.read_text(encoding="utf-8"))
        for run in manifest.get("runs") or []:
            for path in ((run.get("converted_recommendation") or {}).get("normalized") or []):
                agent_paths.append((WORKSPACE_ROOT / path).resolve() if not Path(path).is_absolute() else Path(path))
    for pattern in args.agent_normalized_glob:
        agent_paths.extend(Path().glob(pattern))
    if not agent_paths:
        agent_paths = [DEFAULT_AGENT_NORMALIZED]
    agent_paths = sorted({path.resolve() for path in agent_paths})
    rows, unmatched, summary = build_rows(agent_paths)

    columns = [
        "agent_action",
        "agent_count",
        "gold_action",
        "gold_count",
        "gold_cases",
        "candidate_status",
        "already_accepted",
        "was_previously_reviewed",
        "agent_canonical",
        "gold_canonical",
        "agent_family",
        "gold_family",
        "semantic_overlap",
        "name_similarity",
        "combined_score",
    ]
    write_csv(output_dir / "agent_gold_action_candidates.csv", rows, columns)
    (output_dir / "agent_gold_action_candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "agent_gold_unmatched_actions.csv", unmatched, ["agent_action", "agent_count", "agent_family", "agent_semantics"])
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.md").write_text(
        "\n".join(
            [
                "# Agent-Gold Action Candidate Summary",
                "",
                f"- Gold unique actions: `{summary['gold_unique_actions']}`",
                f"- Agent unique actions: `{summary['agent_unique_actions']}`",
                f"- Candidate rows: `{summary['candidate_rows']}`",
                f"- Accepted-rule rows: `{summary['accepted_rule_rows']}`",
                f"- Strong candidate rows: `{summary['strong_candidate_rows']}`",
                f"- Possible candidate rows: `{summary['possible_candidate_rows']}`",
                f"- Unmatched agent actions: `{summary['unmatched_agent_actions']}`",
                "",
                summary["note"],
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
