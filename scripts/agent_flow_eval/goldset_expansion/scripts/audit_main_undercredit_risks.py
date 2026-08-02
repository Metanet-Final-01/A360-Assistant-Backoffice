"""Audit Main goldset candidates for scoring under-credit risks.

This script intentionally audits the new Main candidate pool, not the legacy
13-case regression set. It uses the production scorer's flattening logic so the
reported scorable counts match active evaluation behavior.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "evaluation"
sys.path.insert(0, str(EVAL_ROOT))

from action_matching import (  # noqa: E402
    flatten_scored_actions,
    load_action_equivalence_map,
    load_conditional_equivalence_groups,
)


MAIN_DIR = ROOT / "goldset_expansion" / "export_main_challenge" / "deliverable" / "Main"
REPORT_DIR = ROOT / "goldset_expansion" / "reports" / "main_undercredit_audit"

HIGH_FREEDOM_PACKAGES = {"dll", "python", "javascript", "vbscript", "rest", "soap"}
HIGH_FREEDOM_ACTION_RE = re.compile(r"(runmacro|runapp|execute\s*sql|stored\s*procedure)", re.IGNORECASE)
ALIAS_FAMILIES = {
    "excel_old_new": {"Excel", "Excel_MS", "Excel advanced", "Microsoft 365 Excel package in Automation 360"},
    "browser_old_new": {"WebAutomation", "Browser", "Recorder"},
    "mail_old_new": {"Email", "Gmail", "Microsoft 365 Outlook"},
}
UTILITY_PACKAGES = {"String", "Folder", "Datetime", "Number", "File", "Dictionary", "List"}


def iter_steps(steps: list[dict[str, Any]]) -> Any:
    for step in steps or []:
        yield step
        if step.get("type") == "container":
            yield from iter_steps(step.get("steps") or [])
        for branch in step.get("branches") or []:
            yield from iter_steps(branch.get("steps") or [])
        if step.get("type") in {"if", "loop", "trigger_loop", "try"}:
            yield from iter_steps(step.get("steps") or [])


def action_name(step: dict[str, Any]) -> str:
    return f"{step.get('package') or ''}.{step.get('action') or ''}"


def is_high_freedom(step: dict[str, Any]) -> bool:
    package = (step.get("package") or "").strip().lower()
    action = step.get("action") or ""
    return package in HIGH_FREEDOM_PACKAGES or bool(HIGH_FREEDOM_ACTION_RE.search(action))


def structure_counts(steps: list[dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    for step in iter_steps(steps):
        step_type = step.get("type")
        package = (step.get("package") or "").replace(" ", "").lower()
        action = (step.get("action") or "").replace(" ", "").lower()
        text = f"{step_type}.{package}.{action}"
        if step_type == "if" or "if" in text:
            counts["if"] += 1
        if step_type == "loop" or "loop" in text:
            counts["loop"] += 1
        if step_type == "trigger_loop" or "triggerloop" in text:
            counts["trigger_loop"] += 1
        if step_type == "try" or "errorhandler" in text or "catch" in text:
            counts["error_handler"] += 1
    return counts


def alias_risks(packages: set[str]) -> list[str]:
    risks: list[str] = []
    for name, family in ALIAS_FAMILIES.items():
        if packages & family:
            risks.append(name)
    return risks


def decision(row: dict[str, Any]) -> str:
    if row["high_freedom_count"] > 0:
        return "exclude_or_challenge"
    if row["scorable_action_count"] < 8:
        return "exclude"
    if row["top_package_ratio"] >= 0.75:
        return "exclude"
    if row["scorable_action_count"] >= 30 and row["package_count"] >= 6:
        return "top_candidate"
    if row["scorable_action_count"] >= 15 and row["package_count"] >= 5:
        return "secondary_candidate"
    return "manual_review"


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plain_map = load_action_equivalence_map()
    conditional_groups = load_conditional_equivalence_groups()
    rows: list[dict[str, Any]] = []

    for path in sorted(MAIN_DIR.glob("*.cleaned.goldset.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        all_action_steps = [s for s in iter_steps(payload.get("steps") or []) if s.get("type") == "action"]
        high_freedom = [action_name(s) for s in all_action_steps if is_high_freedom(s)]
        scored = flatten_scored_actions(payload.get("steps") or [], plain_map=plain_map, conditional_groups=conditional_groups)
        package_counts = Counter(a.package or "" for a in scored)
        action_counts = Counter(a.canonical_label for a in scored)
        structures = structure_counts(payload.get("steps") or [])
        top_package, top_count = package_counts.most_common(1)[0] if package_counts else ("", 0)
        scorable_count = len(scored)
        top_ratio = top_count / scorable_count if scorable_count else 0.0
        utility_count = sum(count for package, count in package_counts.items() if package in UTILITY_PACKAGES)
        row = {
            "candidate_file": path.name,
            "workflow_id": path.name.split("_", 1)[0],
            "raw_action_count": len(all_action_steps),
            "scorable_action_count": scorable_count,
            "package_count": len(package_counts),
            "top_package": top_package,
            "top_package_count": top_count,
            "top_package_ratio": round(top_ratio, 4),
            "utility_action_count": utility_count,
            "utility_ratio": round(utility_count / scorable_count, 4) if scorable_count else 0.0,
            "if_count": structures["if"],
            "loop_count": structures["loop"],
            "trigger_loop_count": structures["trigger_loop"],
            "error_handler_count": structures["error_handler"],
            "high_freedom_count": len(high_freedom),
            "high_freedom_examples": "; ".join(sorted(set(high_freedom))[:8]),
            "alias_risk_families": "; ".join(alias_risks(set(package_counts))),
            "top_actions": "; ".join(f"{label} x{count}" for label, count in action_counts.most_common(8)),
        }
        row["decision"] = decision(row)
        rows.append(row)

    columns = [
        "decision",
        "workflow_id",
        "candidate_file",
        "raw_action_count",
        "scorable_action_count",
        "package_count",
        "top_package",
        "top_package_count",
        "top_package_ratio",
        "utility_action_count",
        "utility_ratio",
        "if_count",
        "loop_count",
        "trigger_loop_count",
        "error_handler_count",
        "high_freedom_count",
        "high_freedom_examples",
        "alias_risk_families",
        "top_actions",
    ]
    rows.sort(key=lambda r: (["top_candidate", "secondary_candidate", "manual_review", "exclude", "exclude_or_challenge"].index(r["decision"]), r["workflow_id"], r["candidate_file"]))
    with (REPORT_DIR / "main_candidate_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Main Candidate Under-Credit Audit",
        "",
        "This report audits the new Main candidate pool only. The legacy 13-case set is not used as final goldset evidence.",
        "",
        "## Recommended Use",
        "",
        "| decision | workflow_id | scorable | packages | top package | utility ratio | structures | alias risks |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    for row in rows:
        structures = f"if {row['if_count']}, loop {row['loop_count']}, trigger {row['trigger_loop_count']}, error {row['error_handler_count']}"
        lines.append(
            f"| {row['decision']} | {row['workflow_id']} | {row['scorable_action_count']} | {row['package_count']} | "
            f"{row['top_package']} {row['top_package_ratio']:.2f} | {row['utility_ratio']:.2f} | {structures} | {row['alias_risk_families']} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Exclude or demote high-freedom workflows at candidate-selection time, not by deleting individual actions.",
            "- Use Action/Judge matching for old/new package names, but do not add aliases for DLL/Python/REST-style black-box work.",
            "- Candidates dominated by one utility package should be removed or manually annotated with gold_core_actions before scoring.",
        ]
    )
    (REPORT_DIR / "main_candidate_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT_DIR)


if __name__ == "__main__":
    main()
