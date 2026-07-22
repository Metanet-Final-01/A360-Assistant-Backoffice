"""canonical 워크플로우 JSON을 Mermaid flowchart 텍스트로 바꾸는 기술검증 프로토타입.

⚠️ 실험 코드다 — 기존 페이지(`ops-server/frontend/views/*`)나 백엔드 API에 연결
하지 않는다. `python -m scripts.workflow_viz_experiment.canonical_to_mermaid`로
직접 실행해서 `docs/local/workflow_viz_experiment_2026-07-19/`에 결과 파일만
남긴다. 실시간 실행 표시·SSE·폴링·편집 기능 없음(정적 렌더링만).

입력은 `ops-server/backend/data/workflow_goldset_cases.json`의 실제 케이스
(canonical_steps)다. 이 파일의 케이스 2개는 전부 action/loop 타입만 있고
if/try 분기가 없어서, 분기 렌더링 자체가 되는지는 별도의 합성(synthetic)
스니펫으로만 확인한다 — 합성 스니펫은 실제 데이터가 아니라는 걸 파일명에
명시한다.
"""

from __future__ import annotations

import json
from pathlib import Path

_CASES_PATH = (
    Path(__file__).resolve().parents[2] / "ops-server" / "backend" / "data" / "workflow_goldset_cases.json"
)
_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "local" / "workflow_viz_experiment_2026-07-19"

# 합성(synthetic) 분기 예시 — 실제 골드셋 케이스엔 if/try가 없어서 렌더러가
# 분기를 실제로 그릴 수 있는지만 별도로 검증하기 위해 손으로 만든 스니펫.
_SYNTHETIC_BRANCH_SNIPPET = [
    {"type": "action", "package": "MessageBox", "action": "messageBox", "uid": "s1"},
    {
        "type": "if",
        "uid": "if1",
        "steps": [
            {"type": "action", "package": "Excel", "action": "readCell", "uid": "s2"},
        ],
        "branches": [
            {
                "type": "else",
                "steps": [
                    {"type": "action", "package": "Excel", "action": "writeCell", "uid": "s3"},
                ],
            }
        ],
    },
    {"type": "action", "package": "MessageBox", "action": "messageBox", "uid": "s4"},
]


class _NodeIdAllocator:
    """Mermaid 노드 id는 영숫자만 안전 — uid가 없거나 겹칠 수 있어 순번으로 새로 발급."""

    def __init__(self) -> None:
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"n{self._counter}"


def _escape_label(text: str) -> str:
    return text.replace('"', "'").replace("\n", " ")[:60]


def _action_label(step: dict) -> str:
    package = step.get("package") or "?"
    action = step.get("action") or "?"
    return f"{package}.{action}"


def convert_step_list_to_mermaid(
    steps: list[dict], alloc: _NodeIdAllocator, lines: list[str], entry_id: str | None
) -> str:
    """steps(형제 목록)를 순서대로 연결한 Mermaid 노드/엣지를 lines에 append하고,
    이 목록이 끝나는 마지막 노드의 '출구' id를 반환한다(다음 형제와 이어붙이기 위함).

    각 스텝은 (entry_id, exit_id)를 갖는다 — 보통 스텝(action/loop)은 entry==exit
    (노드 하나)지만, if/try는 entry(분기 판단 노드)와 exit(분기 합류 노드)가 서로
    다르다. 이전 노드는 반드시 이 스텝의 entry로 연결해야 하고, 다음 스텝은 이
    스텝의 exit에서 이어져야 한다 — 이 둘을 하나의 id로 뭉뚱그리면(초기 버전의
    버그) if/try 앞에 있던 노드가 분기 판단 노드를 건너뛰고 합류 노드로 잘못
    연결된다. 합성 분기 스니펫으로 실제로 이 버그를 잡아서 고쳤다."""
    prev_exit_id = entry_id
    for step in steps:
        step_entry_id, step_exit_id = _convert_single_step(step, alloc, lines)
        if prev_exit_id is not None:
            lines.append(f"    {prev_exit_id} --> {step_entry_id}")
        prev_exit_id = step_exit_id
    return prev_exit_id


def _convert_single_step(step: dict, alloc: _NodeIdAllocator, lines: list[str]) -> tuple[str, str]:
    """스텝 하나를 그리고 (entry_id, exit_id)를 반환한다."""
    step_type = step.get("type")

    if step_type == "action":
        node_id = alloc.next_id()
        lines.append(f'    {node_id}["{_escape_label(_action_label(step))}"]')
        return node_id, node_id

    if step_type == "loop":
        loop_id = alloc.next_id()
        lines.append(f'    {loop_id}{{{{"반복(loop)"}}}}')
        body_steps = step.get("steps") or []
        if body_steps:
            body_last = convert_step_list_to_mermaid(body_steps, alloc, lines, loop_id)
            # 반복 구조를 보이도록 본문 마지막 노드에서 loop 노드로 되돌아가는 엣지.
            lines.append(f"    {body_last} -.반복.-> {loop_id}")
        return loop_id, loop_id

    if step_type in ("if", "try"):
        label = "조건 분기(if)" if step_type == "if" else "예외 처리(try)"
        decision_id = alloc.next_id()
        lines.append(f'    {decision_id}{{"{label}"}}')
        join_id = alloc.next_id()
        lines.append(f'    {join_id}((" "))')

        main_steps = step.get("steps") or []
        if main_steps:
            main_last = convert_step_list_to_mermaid(main_steps, alloc, lines, decision_id)
            lines.append(f"    {main_last} --> {join_id}")
        else:
            lines.append(f"    {decision_id} --> {join_id}")

        for branch in step.get("branches") or []:
            branch_steps = branch.get("steps") or []
            if branch_steps:
                branch_first_id = alloc.next_id()
                lines.append(f'    {branch_first_id}["{_escape_label("(분기 시작)")}"]')
                lines.append(f"    {decision_id} -.분기.-> {branch_first_id}")
                branch_last = convert_step_list_to_mermaid(
                    branch_steps, alloc, lines, branch_first_id
                )
                lines.append(f"    {branch_last} --> {join_id}")
        return decision_id, join_id

    if step_type in ("container", "trigger_loop"):
        node_id = alloc.next_id()
        label = f"{step.get('package', '?')} ({step_type})"
        lines.append(f'    {node_id}["{_escape_label(label)}"]')
        return node_id, node_id

    # 알 수 없는 타입 — 스키마에 새 타입이 생겼을 수 있으니 조용히 누락시키지 않고
    # 화면에 "미지원 타입"으로 표시한다.
    node_id = alloc.next_id()
    lines.append(f'    {node_id}["? 미지원 타입: {step_type}"]')
    return node_id, node_id


def canonical_to_mermaid(canonical_steps: list[dict], title: str) -> str:
    alloc = _NodeIdAllocator()
    lines: list[str] = ["flowchart TD"]
    start_id = alloc.next_id()
    lines.append(f'    {start_id}(["시작: {_escape_label(title)}"])')
    last_id = convert_step_list_to_mermaid(canonical_steps, alloc, lines, start_id)
    end_id = alloc.next_id()
    lines.append(f'    {end_id}(["종료"])')
    lines.append(f"    {last_id} --> {end_id}")
    return "\n".join(lines)


def count_nodes_in_canonical(steps: list[dict]) -> int:
    """Mermaid 렌더러가 실제로 몇 개의 노드를 만드는지(구조 검증용) 재귀 카운트."""
    count = 0
    for step in steps:
        step_type = step.get("type")
        if step_type == "action":
            count += 1
        elif step_type == "loop":
            count += 1 + count_nodes_in_canonical(step.get("steps") or [])
        elif step_type in ("if", "try"):
            count += 2  # decision + join
            count += count_nodes_in_canonical(step.get("steps") or [])
            for branch in step.get("branches") or []:
                count += 1  # 분기 시작 노드
                count += count_nodes_in_canonical(branch.get("steps") or [])
        else:
            count += 1
    return count


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = json.loads(_CASES_PATH.read_text(encoding="utf-8"))

    report_lines: list[str] = []

    for case in cases:
        case_id = case["case_id"]
        steps = case["canonical_steps"]
        mermaid_text = canonical_to_mermaid(steps, case["source_label"])
        out_path = _OUTPUT_DIR / f"canonical_{case_id}.mmd"
        out_path.write_text(mermaid_text, encoding="utf-8")

        node_count = count_nodes_in_canonical(steps)
        report_lines.append(
            f"- {case_id}({case['source_label']}): canonical_step_count="
            f"{case['canonical_step_count']}, 렌더러가 만든 노드 수(시작/종료 제외)="
            f"{node_count} -> {out_path.name}"
        )

    # 합성 분기 스니펫 — 실제 데이터 아님, 분기 렌더링 자체가 되는지만 확인용.
    synthetic_mermaid = canonical_to_mermaid(_SYNTHETIC_BRANCH_SNIPPET, "(합성 예시) if 분기 테스트")
    synthetic_path = _OUTPUT_DIR / "canonical_SYNTHETIC_branch_example.mmd"
    synthetic_path.write_text(synthetic_mermaid, encoding="utf-8")
    report_lines.append(f"- (합성, 실제 데이터 아님) if 분기 예시 -> {synthetic_path.name}")

    print("생성된 Mermaid 파일:")
    for line in report_lines:
        print(line)
    print(f"\n출력 위치: {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
