"""GET /eval/format-guide가 돌려줄 안내 데이터. format_examples/ 파일을 그대로 읽어서
보여준다 — 안내 문구가 예시 파일과 따로 놀지 않도록, 설명 텍스트만 여기 하드코딩하고
실제 JSON 값은 파일에서 읽는다."""

import json
from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent / "format_examples"


def _read_json(rel_path: str) -> dict:
    return json.loads((_EXAMPLES_DIR / rel_path).read_text(encoding="utf-8"))


def build_format_guide() -> dict:
    return {
        "pm4py": {
            "summary": (
                "정답 워크플로우(Petri net, .pnml)와 에이전트가 예측한 액션 순서를 정렬해 "
                "fitness/precision을 계산합니다. Loop/If/ErrorHandler 컨테이너는 정답 Petri "
                "net에 리프로 나타나지 않아 채점 전에 걸러집니다."
            ),
            "input_example": {
                "note": "predicted_actions — 에이전트 예측 결과 (필수 필드: source_bot, predicted_actions[].package/.action)",
                "value": _read_json("pm4py/predicted_actions_example.json"),
            },
            "output_example": {
                "note": "conformance_result — pm4py로 채점한 원본 출력. EvalRunRecord.raw에 그대로 넣으세요.",
                "value": _read_json("pm4py/conformance_result_example.json"),
            },
        },
        "worfbench": {
            "summary": (
                "에이전트가 만든 서브태스크 그래프('Node: ... Edges: ...')와 정답 그래프를 "
                "비교해 precision/recall/f1을 계산합니다."
            ),
            "input_example": {
                "note": "pred_traj — WorFBench 입력 1건 (system/user/assistant 대화 + meta.actions)",
                "value": _read_json("worfbench/pred_traj_example.json"),
            },
            "output_example": {
                "note": "eval_result — WorFBench로 채점한 원본 출력. EvalRunRecord.raw에 그대로 넣으세요.",
                "value": _read_json("worfbench/eval_result_example.json"),
            },
        },
    }
