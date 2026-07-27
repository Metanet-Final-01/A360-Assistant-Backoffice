from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / "action_equivalence_rules.json"
REVIEWED_PATH = ROOT / "action_equivalence_reviewed_pairs.json"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def merge_group(groups: list[dict[str, Any]], incoming: dict[str, Any]) -> None:
    canonical = incoming["canonical"]
    members = dedupe_preserve_order([*incoming.get("members", []), canonical])
    member_set = set(members)
    matching_indexes = [
        index for index, group in enumerate(groups)
        if member_set & set(group.get("members", []))
    ]

    if not matching_indexes:
        groups.append({"canonical": canonical, "members": members})
        return

    target_index = matching_indexes[0]
    target = groups[target_index]
    merged_members = dedupe_preserve_order([*target.get("members", []), *members])
    target["members"] = merged_members

    for index in reversed(matching_indexes[1:]):
        other = groups.pop(index)
        target["members"] = dedupe_preserve_order([*target["members"], *other.get("members", [])])


def add_reviewed_pair(reviewed_pairs: list[dict[str, str]], source: str, candidate: str, update_source: str) -> None:
    existing = {
        (item.get("gold_action"), item.get("stored_action"), item.get("source"))
        for item in reviewed_pairs
    }
    row = {
        "gold_action": source,
        "stored_action": candidate,
        "source": update_source,
    }
    key = (row["gold_action"], row["stored_action"], row["source"])
    if key not in existing:
        reviewed_pairs.append(row)


def validate_rules(groups: list[dict[str, Any]]) -> None:
    member_to_canonical: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    for group in groups:
        canonical = group["canonical"]
        group["members"] = dedupe_preserve_order(group.get("members", []))
        if canonical not in group["members"]:
            group["members"].append(canonical)
        for member in group["members"]:
            existing = member_to_canonical.get(member)
            if existing and existing != canonical:
                conflicts.append((member, existing, canonical))
            member_to_canonical[member] = canonical
    if conflicts:
        conflict_text = "; ".join(f"{member}: {left} vs {right}" for member, left, right in conflicts)
        raise SystemExit(f"Action equivalence conflicts found: {conflict_text}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge accepted/rejected action equivalence review updates.")
    parser.add_argument("update_file", type=Path)
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--reviewed", type=Path, default=REVIEWED_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    update_file = args.update_file.resolve()
    update = load_json(update_file, {})
    update_source = update.get("source") or update_file.name

    rules = load_json(args.rules, {"equivalence_groups": []})
    groups = rules.setdefault("equivalence_groups", [])
    for group in update.get("accepted_equivalence_groups", []) or []:
        merge_group(groups, group)
    validate_rules(groups)
    write_json(args.rules, rules)

    reviewed = load_json(args.reviewed, {"reviewed_pairs": []})
    reviewed_pairs = reviewed.setdefault("reviewed_pairs", [])
    for pair in update.get("rejected_pairs", []) or []:
        add_reviewed_pair(reviewed_pairs, pair["source"], pair["candidate"], update_source)
    write_json(args.reviewed, reviewed)

    print(
        json.dumps(
            {
                "rules": str(args.rules),
                "reviewed": str(args.reviewed),
                "accepted_groups_applied": len(update.get("accepted_equivalence_groups", []) or []),
                "rejected_pairs_applied": len(update.get("rejected_pairs", []) or []),
                "total_groups": len(groups),
                "total_reviewed_pairs": len(reviewed_pairs),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
