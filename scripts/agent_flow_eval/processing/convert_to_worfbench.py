from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from action_filters import is_disabled_step, should_exclude_action

CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)

# Copied verbatim from A360-Assistant-Ops/ops-server/backend/app/eval/workflow/adapters.py
# (to_worfbench_pred_traj) — WorFBench's scorer parses the assistant turn with a fixed
# Node:/Edges: grammar, so this wording is not free to change.
_WORFBENCH_SYSTEM_PROMPT = (
    "You are a helpful and intelligent task planner, and your target is to decompose "
    "the assigned task into multiple subtasks for task completion and analyze the "
    "precedence relationships among subtasks.\nAt the beginning of your interactions, "
    "you will be given the task description and actions list you can take to finish "
    "the task, and you should decompose the given task into subtasks that can be "
    "accomplished using the provided actions or APIs. And then, you should analyze the "
    "precedence relationships among these subtasks, ensuring that each subtask is "
    "sequenced correctly relative to others. Based on the analysis, you should construct "
    'a workflow consisting of the identified subtasks to complete the task. You should '
    'use "Node: \\n1. <subtask 1>\\n2. <subtask 2>" to denote subtasks, and use (x,y) to '
    "denote that <subtask x> is a predecessor of <subtask y>, (START,x) to indicate the "
    "beginning with <subtask x>, and (x,END) to signify the conclusion with <subtask x>. "
    "Remember that x, y are numbers.\nYour response should use the following format:\n\n"
    "Node:\n1.<subtask 1>\n2.<subtask 2>\n...\nEdges:(START,1) ... (n,END)"
)

CONTROL_TYPES = {"if", "loop", "try", "trigger_loop"}


@dataclass
class ConvertResult:
    category: str
    bot_dir: str
    goldset_file: str
    status: str
    action_count: int = 0
    worfbench_fidelity: str | None = None
    control_flow_types: list[str] | None = None
    error: str | None = None


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset"


def flatten_all_branches(steps: list[dict], found_types: set[str]) -> list[dict]:
    """스텝 트리를 액션 한 줄로 펼친다. **분기는 하나도 버리지 않는다** -
    if의 본문과 elseIf/else를 전부 넣는다(catch만 제외, try는 본문+finally).

    2026-08-04 수정: 원래 이 함수는 `canonical_path`라는 이름으로 "대표 경로
    하나만 남긴다"(if는 then 분기만, trigger_loop는 첫 분기만)를 했는데,
    그러면 정답 워크플로우의 요구사항이 실제로 사라졌다 - 실측하면 0376에서
    20개 중 6개, 0098에서 4개가 없어졌다. 정답이 3개 분기로 3가지 경우를
    처리한다면 봇도 3가지를 다 만들어야 하므로 분기를 버리면 안 된다.

    같은 시점에 `evaluation/adapters/worfbench_adapter.py`의 쌍둥이 함수와
    규칙을 통일했다. 그쪽은 원래부터 전부 펼치고 있었는데 이름만
    `_canonical_path`라 정반대로 읽혔다 - 거의 같은 이름의 함수 둘이 서로
    다르게 동작하던 상태를 없앤 것이다. **이 함수의 출력물은 어떤 채점기도
    입력으로 쓰지 않으므로(채점은 goldset.json을 직접 읽는다) 이 수정으로
    바뀌는 점수는 없다.**

    루프는 본문을 1회만 넣는다 - 반복 횟수는 실행 시점에 정해져서 정적
    워크플로우에서는 펼칠 수 없다. 메인 채점기도 똑같이 하는 공통 규칙이다.
    found_types에는 등장한 제어 구조를 기록해서, 호출부가 이 근사(루프 1회,
    catch 제외)가 실제로 일어났는지 표시할 수 있게 한다."""
    actions: list[dict] = []
    for step in steps:
        if is_disabled_step(step):
            continue
        step_type = step["type"]
        if step_type == "action":
            if should_exclude_action(step.get("package"), step.get("action")):
                continue
            actions.append({"package": step["package"], "action": step["action"]})
            continue

        if step_type in CONTROL_TYPES:
            found_types.add(step_type)

        if step_type in {"if", "loop", "container"}:
            actions.extend(flatten_all_branches(step.get("steps", []) or [], found_types))
            for b in step.get("branches", []) or []:
                actions.extend(flatten_all_branches(b.get("steps", []) or [], found_types))
        elif step_type == "trigger_loop":
            for b in step.get("branches", []) or []:
                actions.extend(flatten_all_branches(b.get("steps", []) or [], found_types))
        elif step_type == "try":
            actions.extend(flatten_all_branches(step.get("steps", []) or [], found_types))
            for b in step.get("branches", []) or []:
                if b.get("branch") == "finally":
                    actions.extend(flatten_all_branches(b.get("steps", []) or [], found_types))
        else:
            raise ValueError(f"unknown goldset step type: {step_type!r}")
    return actions


def build_node_edges(actions: list[dict]) -> str:
    node_lines = [f"{i}: {a['package']}.{a['action']}" for i, a in enumerate(actions, start=1)]
    if not actions:
        edge_pairs = ["(START,END)"]
    else:
        edge_pairs = ["(START,1)"] + [f"({i},{i + 1})" for i in range(1, len(actions))] + [f"({len(actions)},END)"]
    return "Node:\n" + "\n".join(node_lines) + "\nEdges:\n" + " ".join(edge_pairs)


def convert_goldset_file(goldset_path: Path, record_id: str) -> tuple[dict, int, str, list[str]]:
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    found_types: set[str] = set()
    actions = flatten_all_branches(goldset.get("steps", []) or [], found_types)

    fidelity = "exact" if not found_types else "approximated"
    record = {
        "source": "a360_rpa_goldset",
        "id": record_id,
        "conversations": [
            {"role": "system", "content": _WORFBENCH_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {goldset.get('source_file', record_id)}"},
            {"role": "assistant", "content": build_node_edges(actions)},
        ],
        "meta": {
            "worfbench_fidelity": fidelity,
            "control_flow_types": sorted(found_types),
            "actions": actions,
        },
    }
    return record, len(actions), fidelity, sorted(found_types)


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
    stem = goldset_file[: -len(".goldset.json")]
    record_id = f"{bot_dir_rel.replace(chr(92), '/')}/{stem}"
    try:
        record, action_count, fidelity, types = convert_goldset_file(goldset_path, record_id)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        return ConvertResult(category=category, bot_dir=bot_dir_rel, goldset_file=goldset_file, status="failed", error=f"{type(exc).__name__}: {exc}")

    output_path = goldset_path.with_name(stem + ".worfbench.json")
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return ConvertResult(
        category=category, bot_dir=bot_dir_rel, goldset_file=goldset_file, status="created",
        action_count=action_count, worfbench_fidelity=fidelity, control_flow_types=types,
    )


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# WorFBench Conversion Report (diagnostic-only, see caveat below)",
        "",
        "**Not an authoritative score.** WorFBench's f1chain assumes a DAG — it cannot "
        "represent If/Loop/ErrorHandler/TriggerLoop natively (the paper's own Appendix A.8 "
        "acknowledges this). For workflows that contain any of those, this converter "
        "collapses every branch point to one representative path (if -> then-branch only, "
        "loop -> body once, try -> try+finally only, catch/elseIf/else dropped) before "
        "flattening to WorFBench's Node/Edges chain format — the same rule applied "
        "consistently, not picked per file. Each output is tagged `worfbench_fidelity`: "
        "`exact` (no control flow at all — f1chain is fully valid here) or `approximated` "
        "(some branch was collapsed — report separately, use only to measure how much this "
        "distorts the metric and to demonstrate WorFBench's structural limitation on real "
        "data, not as a trustworthy workflow-correctness score).",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Dataset root: `{payload['dataset_dir']}`",
        f"- Total goldset files: `{summary['total']}`",
        f"- Created: `{summary['created']}`",
        f"- Failed: `{summary['failed']}`",
        f"- exact (no control flow): `{summary['exact']}`",
        f"- approximated (branch collapsed): `{summary['approximated']}`",
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
        description="Convert *.goldset.json into WorFBench's Node/Edges gold format (diagnostic-only for branchy workflows)."
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
            "exact": sum(1 for r in results if r.worfbench_fidelity == "exact"),
            "approximated": sum(1 for r in results if r.worfbench_fidelity == "approximated"),
        },
        "rows": [asdict(r) for r in results],
    }
    json_output = args.json_output or (dataset_dir / "worfbench_conversion_report.json")
    md_output = args.md_output or (dataset_dir / "worfbench_conversion_report.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(json.dumps({"json": str(json_output), "markdown": str(md_output), **payload["summary"]}, ensure_ascii=False, indent=2))
    if payload["summary"]["failed"]:
        raise SystemExit("Some goldset files failed to convert. Review the report.")


if __name__ == "__main__":
    main()
