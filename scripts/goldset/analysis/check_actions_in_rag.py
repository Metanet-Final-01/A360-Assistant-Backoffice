from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)

# A manual, one-off "verified rename" alias table was tried and removed. Even after
# comparing parameter lists field-by-field, at least one entry (DataTable.writeToFile ->
# Data Table's "cloudWriteToFileAction") turned out to be less certain than it first
# looked: only 2 of that package's 16 actions carry a "cloud" prefix, which could mean a
# genuinely different execution path (e.g. cloud-storage-specific) rather than a plain
# rename of the same local-file operation -- and the JAR-manifest-based bot-authoring
# process is confirmed fully deterministic within a version (see PROVENANCE.md), so any
# real fix belongs at the *package-version* level (e.g. mining AA's GitHub package repo
# history for actual rename commits/tags), not a hand-picked, per-entry guess no matter
# how carefully parameters are compared. Back to exact-match only until that exists.


@dataclass
class FileCoverage:
    category: str
    bot_name: str
    source_file: str
    distinct_pairs: int
    matched_pairs: int
    missing_pairs: list[str]
    fully_covered: bool


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
    """(package_name, action_name) pairs actually documented as `action_schema` in the
    RAG build export -- this is the exact catalog a recommendation LLM can draw from.
    NOTE: as of this check, the *live* rag_documents table in the configured
    RAG_DATABASE_URL has 0 action_schema/package_overview rows (only doc_page, from an
    earlier docs-only ingest) -- this file is the pipeline's build output, not yet
    pushed via `pipeline.py ingest`. Treated here as "what the catalog will contain
    once ingested", which is the only concrete catalog available to check against."""
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


def collect_action_pairs(steps: list[dict], pairs: Counter) -> None:
    """Every `action` and `container` step is a real package.commandName invocation
    (container also has nested children, but the container node itself was invoked) --
    both count. `if`/`loop`/`try`/`trigger_loop` are pure control structure, not
    invocations, and are only recursed into."""
    for step in steps:
        step_type = step.get("type")
        if step_type in ("action", "container"):
            pkg, act = step.get("package"), step.get("action")
            if pkg and act:
                pairs[(pkg, act)] += 1
        collect_action_pairs(step.get("steps", []) or [], pairs)
        for branch in step.get("branches", []) or []:
            collect_action_pairs(branch.get("steps", []) or [], pairs)


def check_file(goldset_path: Path, catalog: set[tuple[str, str]]) -> tuple[Counter, list[tuple[str, str]]]:
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    pairs: Counter = Counter()
    collect_action_pairs(goldset.get("steps", []) or [], pairs)
    missing = sorted(pair for pair in pairs if pair not in catalog)
    return pairs, missing


def collect_all(dataset_dir: Path, catalog: set[tuple[str, str]]) -> list[FileCoverage]:
    # Dedup by (bot_name, source_file): the same physical bot can be shortlisted under
    # more than one category folder (see PROVENANCE.md), which physically copies its
    # workflows/ files into each category's dataset dir. Counting every category copy
    # inflated the corpus size (98 category-slots / 155 goldset-file rows) well past the
    # true unique count (70 physical bots / 124 unique workflow files) -- confirmed by
    # cross-checking against select_eval_candidates.py's already-deduped candidate count.
    rows_by_key: dict[tuple[str, str], FileCoverage] = {}
    for category in CATEGORY_DIRS:
        category_dir = dataset_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            for goldset_path in sorted((bot_dir / "workflows").glob("*.goldset.json")):
                key = (bot_dir.name, goldset_path.name)
                if key in rows_by_key:
                    continue
                pairs, missing = check_file(goldset_path, catalog)
                rows_by_key[key] = FileCoverage(
                    category=category, bot_name=bot_dir.name, source_file=goldset_path.name,
                    distinct_pairs=len(pairs), matched_pairs=len(pairs) - len(missing),
                    missing_pairs=[f"{p}.{a}" for p, a in missing], fully_covered=not missing,
                )
    return list(rows_by_key.values())


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Goldset Actions vs RAG Action Catalog",
        "",
        "Every action referenced in a goldset workflow **must exist in the RAG action "
        "catalog** -- an LLM recommending a workflow can only ever cite actions it has "
        "actually been shown, so a goldset action absent from the catalog makes that "
        "part of the reference unreachable by construction, not just hard to predict.",
        "",
        "**Catalog caveat:** the live Postgres DB behind `RAG_DATABASE_URL` currently "
        "has **0** `action_schema`/`package_overview` rows (only `doc_page`, from an "
        "earlier docs-only ingest) -- this check runs against "
        "`rag-server/data/ingest/rag_documents.jsonl`, the pipeline's local build "
        "output, which does have 1616 `action_schema` entries ready but has not yet "
        "been pushed via `pipeline.py ingest`. Until that ingest runs, the live system "
        "cannot recommend ANY of these actions regardless of this check's result --  "
        "that's a separate, more urgent gap than anything below.",
        "",
        "**Exact match only.** A manual per-entry \"verified rename\" alias table was "
        "tried (e.g. `DataTable.writeToFile` -> `Data Table`'s `cloudWriteToFileAction`) "
        "and removed -- even careful parameter-list comparison couldn't fully rule out "
        "that some matches were a different underlying execution path (that specific "
        "pair: only 2 of 16 actions in the catalog package carry a \"cloud\" prefix, "
        "which could mean cloud-storage-specific behavior, not a plain rename). A "
        "systematic fix would need real package-version history (e.g. mining AA's "
        "GitHub package repos for actual rename commits/tags), not one-off guesses. "
        "Until that exists, some of the 'missing' pairs below are real renames the "
        "exact match can't see, not genuine absences -- but nothing here is "
        "auto-resolved, so treat 'missing' as a conservative overestimate.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- RAG catalog source: `{payload['rag_documents_jsonl']}`",
        f"- Distinct (package, action) pairs in catalog: `{payload['catalog_size']}`",
        f"- Goldset files checked: `{summary['total_files']}`",
        f"- Fully covered (every action found in catalog): `{summary['fully_covered_files']}`",
        f"- Has at least one missing action: `{summary['files_with_gaps']}`",
        "",
        "## Packages used in the goldset corpus that don't exist in the RAG catalog at all",
        "",
    ]
    for pkg in payload["packages_entirely_missing_from_catalog"]:
        lines.append(f"- `{pkg}`")
    lines.extend(["", "## Files with missing actions", ""])
    for row in payload["rows"]:
        if not row["fully_covered"]:
            missing = ", ".join(f"`{m}`" for m in row["missing_pairs"])
            lines.append(f"- `{row['bot_name']}` / `{row['source_file']}`: {missing}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check every goldset action against the RAG action_schema catalog.")
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
    catalog_packages = {pkg for pkg, _ in catalog}
    rows = collect_all(dataset_dir, catalog)

    used_packages: set[str] = set()
    for goldset_path in dataset_dir.glob("*/*/workflows/*.goldset.json"):
        pairs, _ = check_file(goldset_path, catalog)
        used_packages.update(pkg for pkg, _ in pairs)
    packages_entirely_missing = sorted(used_packages - catalog_packages)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rag_documents_jsonl": str(rag_documents_jsonl),
        "catalog_size": len(catalog),
        "packages_entirely_missing_from_catalog": packages_entirely_missing,
        "summary": {
            "total_files": len(rows),
            "fully_covered_files": sum(1 for r in rows if r.fully_covered),
            "files_with_gaps": sum(1 for r in rows if not r.fully_covered),
        },
        "rows": [asdict(r) for r in rows],
    }
    json_output = args.json_output or (dataset_dir / "rag_action_coverage_report.json")
    md_output = args.md_output or (dataset_dir / "rag_action_coverage_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({
        "json": str(json_output), "markdown": str(md_output),
        "catalog_size": len(catalog), "packages_entirely_missing_from_catalog": packages_entirely_missing,
        **payload["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
