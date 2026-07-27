from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def default_pm4py_src() -> Path:
    # Same external/pm4py checkout run_pm4py_conformance.py already uses — not
    # installed as a package, so it has to be added to sys.path before import.
    return default_workspace_root() / "a360-eval-sandbox" / "external" / "pm4py"


sys.path.insert(0, str(default_pm4py_src()))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pm4py  # noqa: E402
from action_filters import is_browser_session_lifecycle_action, is_disabled_step  # noqa: E402
from pm4py.objects.process_tree.obj import Operator, ProcessTree  # noqa: E402

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)

# pm4py's own Process Tree operator set (confirmed from pm4py/objects/process_tree/obj.py —
# there is no "try" operator, so try/catch/finally has to be composed from these).
_OPERATOR_JSON_NAME = {
    Operator.SEQUENCE: "sequence",
    Operator.XOR: "xor",
    Operator.LOOP: "loop",
}


@dataclass
class ConvertResult:
    category: str
    bot_dir: str
    goldset_file: str
    status: str
    leaf_count: int = 0
    error: str | None = None


def _leaf(parent: ProcessTree | None) -> tuple[ProcessTree, dict]:
    """A silent/tau transition — "nothing happens here". Used for an empty step
    list (e.g. an if-branch that had only Comment nodes, filtered to nothing) and
    for the loop operator's mandatory redo/exit children when the RPA Loop concept
    doesn't distinguish them (matches the padding pm4py's own simulation code does
    for a same-shaped LOOP with fewer than 3 children)."""
    return ProcessTree(parent=parent), {"operator": None, "label": None, "children": []}


def _make_leaf(label: str, parent: ProcessTree | None) -> tuple[ProcessTree, dict]:
    return ProcessTree(label=label, parent=parent), {"operator": None, "label": label, "children": []}


def convert_steps(steps: list[dict], parent: ProcessTree | None) -> tuple[ProcessTree, dict]:
    """A list of goldset steps -> one tree node. Empty -> tau. Single step -> that
    step's own node (no pointless 1-child sequence wrapper). Multiple -> SEQUENCE."""
    steps = [
        step for step in steps
        if not is_disabled_step(step)
        and not (
            step.get("type") == "action"
            and is_browser_session_lifecycle_action(step.get("package"), step.get("action"))
        )
    ]
    if not steps:
        return _leaf(parent)
    if len(steps) == 1:
        return convert_step(steps[0], parent)

    node = ProcessTree(operator=Operator.SEQUENCE, parent=parent)
    json_children = []
    for step in steps:
        child, child_json = convert_step(step, node)
        node.children.append(child)
        json_children.append(child_json)
    return node, {"operator": "sequence", "label": None, "children": json_children}


def _branches_by_name(step: dict) -> dict[str, dict]:
    return {b.get("branch"): b for b in step.get("branches", []) or []}


def convert_step(step: dict, parent: ProcessTree | None) -> tuple[ProcessTree, dict]:
    step_type = step["type"]

    if step_type == "action":
        return _make_leaf(f"{step['package']}.{step['action']}", parent)

    if step_type == "container":
        # Unrecognized-but-container package (e.g. HBCWorkflow) — same transparent
        # treatment as Step already got during extraction: just its steps, in order.
        return convert_steps(step.get("steps", []) or [], parent)

    if step_type == "if":
        # then-branch (step's own "steps") plus every elseIf/else branch are
        # mutually exclusive alternatives — only one happens per real execution.
        node = ProcessTree(operator=Operator.XOR, parent=parent)
        alternatives = [step.get("steps", []) or []] + [
            b.get("steps", []) or [] for b in step.get("branches", []) or []
        ]
        json_children = []
        for alt_steps in alternatives:
            child, child_json = convert_steps(alt_steps, node)
            node.children.append(child)
            json_children.append(child_json)
        return node, {"operator": "xor", "label": None, "children": json_children}

    if step_type == "loop":
        # pm4py LOOP is ternary (do, redo, exit) — RPA Loop doesn't distinguish
        # redo/exit bodies, and the LetterGenerator experiment (see PROVENANCE.md /
        # session notes) confirmed fitness is repeat-count invariant, so both are
        # silent transitions: repeating the body doesn't require a visible action,
        # and neither does stopping.
        node = ProcessTree(operator=Operator.LOOP, parent=parent)
        body, body_json = convert_steps(step.get("steps", []) or [], node)
        redo, redo_json = _leaf(node)
        exit_, exit_json = _leaf(node)
        node.children.extend([body, redo, exit_])
        return node, {"operator": "loop", "label": None, "children": [body_json, redo_json, exit_json]}

    if step_type == "trigger_loop":
        node = ProcessTree(operator=Operator.XOR, parent=parent)
        json_children = []
        for b in step.get("branches", []) or []:
            child, child_json = convert_steps(b.get("steps", []) or [], node)
            node.children.append(child)
            json_children.append(child_json)
        if not node.children:
            return _leaf(parent)
        return node, {"operator": "xor", "label": None, "children": json_children}

    if step_type == "try":
        # finally is mandatory (outside the XOR, always runs); catch is the
        # exception-path alternative to a fully-completed try body. Neither is an
        # equal third XOR branch — see session discussion on try/catch/finally
        # modeling (SEQUENCE(XOR(try, catch), finally)).
        branches = _branches_by_name(step)
        has_catch = "catch" in branches
        has_finally = "finally" in branches

        outer = ProcessTree(operator=Operator.SEQUENCE, parent=parent) if has_finally else None
        core_parent = outer if outer is not None else parent

        if has_catch:
            core = ProcessTree(operator=Operator.XOR, parent=core_parent)
            try_child, try_json = convert_steps(step.get("steps", []) or [], core)
            catch_child, catch_json = convert_steps(branches["catch"].get("steps", []) or [], core)
            core.children.extend([try_child, catch_child])
            core_json = {"operator": "xor", "label": None, "children": [try_json, catch_json]}
        else:
            core, core_json = convert_steps(step.get("steps", []) or [], core_parent)

        if outer is None:
            return core, core_json

        finally_child, finally_json = convert_steps(branches["finally"].get("steps", []) or [], outer)
        outer.children.extend([core, finally_child])
        return outer, {"operator": "sequence", "label": None, "children": [core_json, finally_json]}

    raise ValueError(f"unknown goldset step type: {step_type!r}")


def count_leaves(tree_json: dict) -> int:
    if tree_json["label"] is not None:
        return 1
    return sum(count_leaves(c) for c in tree_json["children"])


def convert_goldset_file(goldset_path: Path) -> tuple[ProcessTree, dict]:
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    return convert_steps(goldset.get("steps", []) or [], None)


def collect_goldset_files(dataset_dir: Path) -> list[tuple[str, str, Path]]:
    entries: list[tuple[str, str, Path]] = []
    for category in CATEGORY_DIRS:
        category_dir = dataset_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            workflows_dir = bot_dir / "workflows"
            if not workflows_dir.exists():
                continue
            for goldset_path in sorted(workflows_dir.glob("*.goldset.json")):
                entries.append((category, str(bot_dir.relative_to(dataset_dir)), goldset_path))
    return entries


def process_goldset_file(category: str, bot_dir_rel: str, goldset_path: Path) -> ConvertResult:
    goldset_file = goldset_path.name
    try:
        tree, tree_json = convert_goldset_file(goldset_path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        return ConvertResult(category=category, bot_dir=bot_dir_rel, goldset_file=goldset_file, status="failed", error=f"{type(exc).__name__}: {exc}")

    stem = goldset_path.name[: -len(".goldset.json")]
    net, im, fm = pm4py.convert_to_petri_net(tree)
    pm4py.write_pnml(net, im, fm, str(goldset_path.with_name(stem + ".pnml")))
    pm4py.write_ptml(tree, str(goldset_path.with_name(stem + ".ptml")))
    (goldset_path.with_name(stem + ".tree.json")).write_text(
        json.dumps(tree_json, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return ConvertResult(
        category=category, bot_dir=bot_dir_rel, goldset_file=goldset_file, status="created",
        leaf_count=count_leaves(tree_json),
    )


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# PM4Py Conversion Report",
        "",
        "For each `*.goldset.json`, builds a `pm4py.ProcessTree` (SEQUENCE/XOR/LOOP — "
        "pm4py has no native try/catch/finally operator, so try is composed as "
        "SEQUENCE(XOR(try, catch), finally) and loop as a ternary LOOP(body, tau, tau) "
        "— repeat count doesn't matter, only structure) and writes three files next to "
        "it: `*.pnml` (Petri net — what the actual conformance-checking scripts read), "
        "`*.ptml` (the process tree itself, pm4py's own format), and `*.tree.json` "
        "(the same tree as plain readable JSON, no pm4py install required to inspect it).",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Dataset root: `{payload['dataset_dir']}`",
        f"- Total goldset files: `{summary['total']}`",
        f"- Created: `{summary['created']}`",
        f"- Failed: `{summary['failed']}`",
        f"- Total leaves (all trees): `{summary['total_leaves']}`",
    ]
    if summary["failed"]:
        lines.extend(["", "## Failed", ""])
        for row in payload["rows"]:
            if row["status"] == "failed":
                lines.append(f"- `{row['bot_dir']}/{row['goldset_file']}`: {row['error']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert *.goldset.json into pm4py's actual required inputs (.pnml for scoring, .ptml + .tree.json for readability)."
    )
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    if not dataset_dir.exists():
        raise SystemExit(f"Dataset directory does not exist: {dataset_dir}")

    results = [
        process_goldset_file(category, bot_dir_rel, goldset_path)
        for category, bot_dir_rel, goldset_path in collect_goldset_files(dataset_dir)
    ]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "summary": {
            "total": len(results),
            "created": sum(1 for r in results if r.status == "created"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "total_leaves": sum(r.leaf_count for r in results),
        },
        "rows": [asdict(r) for r in results],
    }
    json_output = args.json_output or (dataset_dir / "pm4py_conversion_report.json")
    md_output = args.md_output or (dataset_dir / "pm4py_conversion_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))
    if payload["summary"]["failed"]:
        raise SystemExit("Some goldset files failed to convert. Review the report.")


if __name__ == "__main__":
    main()
