from __future__ import annotations

import argparse
import json
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# This is a *processing* step, not analysis: it establishes what a goldset candidate's
# real, complete action footprint is (own actions + every TaskBot.runTask-referenced
# sub-task's actions, resolved recursively) -- a prerequisite fact the final candidate
# selection depends on. analysis/ stays read-only reporting on top of what processing/
# establishes.
#
# Why this exists: `TaskBot.runTask` only stores a file-path reference to the sub-task
# (in its `taskbot` attribute's `taskbotFile` string) -- the sub-task's real actions
# live in a separate sibling `<name>.goldset.json` in the same bot folder, not inlined.
# A goldset file can therefore look RAG-fully-covered by analysis/check_actions_in_rag.py
# while hiding genuinely-uncovered work behind an opaque runTask call -- confirmed for
# real: 0188_invoicely-assistant-bot---main and
# 0389_aaridesktop-createservicenowincident both showed "fully_covered" on their own
# direct actions, but every one of their referenced sub-tasks (Client Creation,
# Initialize, Invoicely Login, Attended_CreateIncident,
# Attended_ServiceNowCreateIncident) fails RAG coverage on packages like Wait/Window/
# Forms/Credential Manager. This script makes that visible instead of silent.

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)


@dataclass
class SubtaskCoverageRow:
    bot_name: str
    source_file: str
    own_action_count: int
    own_fully_covered: bool
    own_missing_pairs: list[str]
    transitive_action_count: int
    transitive_fully_covered: bool
    transitive_missing_pairs: list[str]
    resolved_subtask_files: list[str]
    unresolved_subtask_references: list[str]
    own_refs: list[str]
    # Is this file a root of the REAL call graph -- i.e. no other file in this bot
    # contains an actual TaskBot.runTask node targeting it? Deliberately NOT derived
    # from workflow_index.json's manual_dependencies/scanned_dependencies (those mirror
    # Automation Anywhere's own manifest metadata, which can be wrong: confirmed for
    # real on 0167_outlook-email-notifier, whose manifest scannedDependencies lists
    # itself even though its actual workflow content has zero TaskBot.runTask nodes at
    # all, and on 0338_lettergenerationbot, whose manifest manualDependencies form a
    # cycle between LetterGenerationBot and ProofOfFunds_Process even though NEITHER
    # file -- nor SendPOF_Email -- contains a single real TaskBot.runTask node). The
    # manifest fields are Automation Anywhere's own secondary/derived metadata; the
    # actual `TaskBot.runTask` nodes inside the workflow JSON are the primary source of
    # truth for what a bot really calls at runtime, so this is computed from those, not
    # from the manifest.
    is_real_call_graph_root: bool


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def default_rag_documents_jsonl(workspace_root: Path) -> Path:
    return workspace_root / "A360-Assistant-Ops" / "rag-server" / "data" / "ingest" / "rag_documents.jsonl"


def load_rag_action_catalog(rag_documents_jsonl: Path) -> set[tuple[str, str]]:
    """Same contract as analysis/check_actions_in_rag.py::load_rag_action_catalog --
    duplicated (not imported) to keep processing/ independently runnable as a batch
    step without depending on analysis/."""
    catalog: set[tuple[str, str]] = set()
    with rag_documents_jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            if doc.get("source_type") == "action_schema" and doc.get("package_name") and doc.get("action_name"):
                catalog.add((doc["package_name"], doc["action_name"]))
    return catalog


def extract_taskbot_filename(step: dict) -> str | None:
    """A TaskBot.runTask step's `taskbot` attribute holds a repository path like
    "repository:///Automation Anywhere/Bot Store/<bot>/<Filename>" (URL-encoded) --
    the referenced sub-task's own workflow filename is the last path segment."""
    for attr in step.get("attributes", []) or []:
        if attr.get("name") != "taskbot":
            continue
        file_str = (attr.get("value") or {}).get("taskbotFile", {}).get("string")
        if not file_str:
            continue
        decoded = urllib.parse.unquote(file_str)
        return decoded.rstrip("/").split("/")[-1]
    return None


def collect_direct_pairs_and_refs(steps: list[dict], pairs: set[tuple[str, str]], refs: set[str]) -> None:
    for step in steps:
        step_type = step.get("type")
        if step_type in ("action", "container"):
            pkg, act = step.get("package"), step.get("action")
            if pkg and act:
                pairs.add((pkg, act))
            if pkg == "TaskBot" and act == "runTask":
                filename = extract_taskbot_filename(step)
                if filename:
                    refs.add(filename)
        collect_direct_pairs_and_refs(step.get("steps", []) or [], pairs, refs)
        for branch in step.get("branches", []) or []:
            collect_direct_pairs_and_refs(branch.get("steps", []) or [], pairs, refs)


def resolve_transitive(
    workflows_dir: Path, goldset_stem: str, visited: set[str],
) -> tuple[set[tuple[str, str]], set[str]]:
    """Recursively resolves goldset_stem's own actions plus every TaskBot.runTask
    target's actions, found as a sibling `<name>.goldset.json` in the same bot's
    workflows/ dir. `visited` guards against cycles (A calls B calls A) and against
    re-resolving a sub-task reached via two different paths. Returns
    (all_pairs_including_own, unresolved_reference_filenames) -- a reference is
    "unresolved" when no matching `<name>.goldset.json` exists in this bot's corpus
    (e.g. the sub-task wasn't a tracked workflow contentType, or lives in a different
    bot package we never extracted): treated as NOT verifiable, hence not covered."""
    if goldset_stem in visited:
        return set(), set()
    visited.add(goldset_stem)

    goldset_path = workflows_dir / f"{goldset_stem}.goldset.json"
    if not goldset_path.exists():
        return set(), {goldset_stem}

    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str]] = set()
    refs: set[str] = set()
    collect_direct_pairs_and_refs(goldset.get("steps", []) or [], pairs, refs)

    unresolved: set[str] = set()
    for ref in refs:
        sub_pairs, sub_unresolved = resolve_transitive(workflows_dir, ref, visited)
        pairs |= sub_pairs
        unresolved |= sub_unresolved
    return pairs, unresolved


def process_file(bot_dir: Path, goldset_path: Path, catalog: set[tuple[str, str]]) -> tuple[SubtaskCoverageRow, str]:
    """Returns (row, stem) -- stem is this file's own name without the .goldset.json
    suffix, i.e. how OTHER files' real TaskBot.runTask refs would name it."""
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    own_pairs: set[tuple[str, str]] = set()
    own_refs: set[str] = set()
    collect_direct_pairs_and_refs(goldset.get("steps", []) or [], own_pairs, own_refs)
    own_missing = sorted(p for p in own_pairs if p not in catalog)

    stem = goldset_path.name[: -len(".goldset.json")]
    transitive_pairs, unresolved = resolve_transitive(bot_dir / "workflows", stem, visited=set())
    transitive_missing = sorted(p for p in transitive_pairs if p not in catalog)

    resolved_subtasks = sorted(own_refs - unresolved)

    row = SubtaskCoverageRow(
        bot_name=bot_dir.name, source_file=goldset_path.name,
        own_action_count=len(own_pairs), own_fully_covered=not own_missing,
        own_missing_pairs=[f"{p}.{a}" for p, a in own_missing],
        transitive_action_count=len(transitive_pairs),
        transitive_fully_covered=not transitive_missing and not unresolved,
        transitive_missing_pairs=[f"{p}.{a}" for p, a in transitive_missing],
        resolved_subtask_files=resolved_subtasks,
        unresolved_subtask_references=sorted(unresolved),
        own_refs=sorted(own_refs),
        is_real_call_graph_root=False,  # filled in by collect_all once the whole bot is known
    )
    return row, stem


def collect_all(dataset_dir: Path, catalog: set[tuple[str, str]]) -> list[SubtaskCoverageRow]:
    rows_by_key: dict[tuple[str, str], SubtaskCoverageRow] = {}
    for category in CATEGORY_DIRS:
        category_dir = dataset_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            workflows_dir = bot_dir / "workflows"
            if not workflows_dir.exists():
                continue
            bot_rows: dict[str, SubtaskCoverageRow] = {}  # keyed by this file's own stem
            for goldset_path in sorted(workflows_dir.glob("*.goldset.json")):
                key = (bot_dir.name, goldset_path.name)
                if key in rows_by_key:
                    continue
                row, stem = process_file(bot_dir, goldset_path, catalog)
                bot_rows[stem] = row

            # Second pass, now that every file in this bot has been parsed: a file is a
            # real call-graph root iff no OTHER file's actual TaskBot.runTask nodes
            # (own_refs) name it -- computed purely from parsed workflow content, never
            # from the manifest's manual/scanned dependency fields.
            really_referenced: set[str] = set()
            for stem, row in bot_rows.items():
                really_referenced.update(ref for ref in row.own_refs if ref != stem)  # ignore literal self-refs too
            for stem, row in bot_rows.items():
                row.is_real_call_graph_root = stem not in really_referenced
                rows_by_key[(bot_dir.name, row.source_file)] = row
    return list(rows_by_key.values())


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Subtask-Resolved RAG Coverage",
        "",
        "For every unique goldset file, resolves every `TaskBot.runTask` reference to "
        "its sibling `<name>.goldset.json` in the same bot folder (recursively), and "
        "checks the FULL transitive action set against the RAG catalog -- not just the "
        "file's own direct actions. A file whose own actions are fully covered can "
        "still hide uncovered work behind an opaque `runTask` call to an unresolved or "
        "uncovered sub-task; this report makes that visible.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Total unique files: `{summary['total_files']}`",
        f"- Own-actions fully covered: `{summary['own_fully_covered']}`",
        f"- Transitive (own + all resolved sub-tasks) fully covered: `{summary['transitive_fully_covered']}`",
        f"- Files where transitive coverage differs from own coverage "
        f"(own said covered, transitive says NOT): `{summary['downgraded_by_subtask']}`",
        f"- Real call-graph roots (`is_real_call_graph_root`, computed from actual parsed "
        f"`TaskBot.runTask` nodes, not manifest dependency fields): `{summary['real_call_graph_roots']}`",
        "",
        "## Downgraded: looked covered, but a referenced sub-task isn't",
        "",
    ]
    for row in payload["rows"]:
        if row["own_fully_covered"] and not row["transitive_fully_covered"]:
            lines.append(
                f"- `{row['bot_name']}` / `{row['source_file']}`: resolved sub-tasks "
                f"{row['resolved_subtask_files']}, unresolved references "
                f"{row['unresolved_subtask_references']}, transitive missing "
                f"{row['transitive_missing_pairs']}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve TaskBot.runTask sub-task references recursively and check transitive RAG coverage."
    )
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--rag-documents-jsonl", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    workspace_root = default_workspace_root()
    rag_documents_jsonl = args.rag_documents_jsonl or default_rag_documents_jsonl(workspace_root)
    if not rag_documents_jsonl.exists():
        raise SystemExit(f"RAG documents export not found: {rag_documents_jsonl}")

    catalog = load_rag_action_catalog(rag_documents_jsonl)
    rows = collect_all(dataset_dir, catalog)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_files": len(rows),
            "own_fully_covered": sum(1 for r in rows if r.own_fully_covered),
            "transitive_fully_covered": sum(1 for r in rows if r.transitive_fully_covered),
            "downgraded_by_subtask": sum(1 for r in rows if r.own_fully_covered and not r.transitive_fully_covered),
            "real_call_graph_roots": sum(1 for r in rows if r.is_real_call_graph_root),
        },
        "rows": [asdict(r) for r in rows],
    }
    json_output = args.json_output or (dataset_dir / "subtask_transitive_coverage_report.json")
    md_output = args.md_output or (dataset_dir / "subtask_transitive_coverage_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
