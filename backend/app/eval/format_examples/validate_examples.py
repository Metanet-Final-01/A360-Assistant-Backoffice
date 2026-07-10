"""이 폴더의 예시 데이터셋이 format_schemas.py의 스키마를 실제로 만족하는지 자체 검증.

validate_catalog_refs.py(sample_cases용)와 같은 목적 — 예시 파일을 고치다가
형식이 깨지는 걸 코드 리뷰 없이도 바로 잡아낸다.

실행: cd backend && python -m app.eval.format_examples.validate_examples
"""

import json
import sys
from pathlib import Path

from app.eval.format_schemas import (
    PM4pyConformanceResult,
    PM4pyPredictedActions,
    WorfbenchEvalResult,
    WorfbenchPredTrajEntry,
)

HERE = Path(__file__).parent

CHECKS = [
    ("pm4py/predicted_actions_example.json", PM4pyPredictedActions),
    ("pm4py/conformance_result_example.json", PM4pyConformanceResult),
    ("worfbench/pred_traj_example.json", WorfbenchPredTrajEntry),
    ("worfbench/eval_result_example.json", WorfbenchEvalResult),
]


def main() -> int:
    failed = False
    for rel_path, model in CHECKS:
        path = HERE / rel_path
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            model.model_validate(data)
        except Exception as e:  # noqa: BLE001 - 실패 목록을 다 보여주기 위해 계속 진행
            failed = True
            print(f"[FAIL] {rel_path}: {e}")
        else:
            print(f"[OK]   {rel_path}")
    if failed:
        print("\n예시 데이터셋이 스키마를 만족하지 않습니다 — format_schemas.py 또는 예시 파일을 확인하세요.")
        return 1
    print("\n모든 예시 데이터셋이 스키마를 만족합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
