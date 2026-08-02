"""critical_attribute 모듈이 실제로 동작하는지 실증하는 데모/자체 테스트.

주의: v1/v2/v3 에이전트가 이번 39개(Main-18/Challenge-21) 후보에 대해 실제로 예측한
workflow가 아직 없다(에이전트 배치 채점은 원래 13개 goldset에만 돌렸음). 그래서 진짜
"정답 vs 에이전트 예측" 비교는 아직 못 하고, 대신:
1. 정답 자기 자신과 비교(완전 일치 - match가 제대로 나오는지 확인)
2. 정답에서 selector를 의도적으로 바꾼 가짜 예측과 비교(uncertain 판정 + LLM judge
   호출까지 실제로 실행되는지 확인)
로 파이프라인 전체가 작동하는지만 검증한다. 진짜 채점은 에이전트 예측 데이터가
생기면 그때 이 모듈을 core_task.py/run_eval_case.py에 연결해서 돌려야 한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attribute_signature import critical_signature
from compare import compare_critical_attributes

DELIVERABLE = Path(__file__).resolve().parents[2] / "goldset_expansion" / "export_main_challenge" / "deliverable"


def walk_all(steps: list[dict], out: list[dict]) -> None:
    for s in steps:
        out.append(s)
        walk_all(s.get("steps", []) or [], out)
        for b in s.get("branches", []) or []:
            walk_all(b.get("steps", []) or [], out)


def main() -> None:
    gold_path = DELIVERABLE / "Challenge" / "0020_HelpDesk_Copilot__Change_Status_Of_Query.goldset.json"
    d = json.loads(gold_path.read_text(encoding="utf-8"))
    steps = []
    walk_all(d.get("steps", []), steps)
    recorder_steps = [s for s in steps if s.get("type") == "action" and s.get("package") == "Recorder"]
    print(f"{gold_path.name}: Recorder 스텝 {len(recorder_steps)}개 발견")

    gold_step = recorder_steps[0]
    gold_sig = critical_signature(gold_step)
    print("\n=== 1) 정답 vs 정답 자기자신 (match 나와야 정상) ===")
    report_self = compare_critical_attributes("action", gold_sig, gold_sig)
    for f in report_self.fields:
        print(f"  {f.field}: {f.verdict}")
    print("  요약:", report_self.summary_counts())

    print("\n=== 2) 정답 vs selector만 다른 가짜 예측 (uncertain 나와야 정상) ===")
    fake_pred_sig = json.loads(json.dumps(gold_sig))  # deep copy
    fake_pred_sig["target"]["css_selector"] = "div#root>div>main>section>ul.different-selector"
    report_diff = compare_critical_attributes("action", gold_sig, fake_pred_sig)
    for f in report_diff.fields:
        print(f"  {f.field}: {f.verdict}  (gold={f.gold_value!r} / pred={f.pred_value!r})")
    print("  요약:", report_diff.summary_counts())

    uncertain_fields = [f for f in report_diff.fields if f.verdict == "uncertain"]
    if uncertain_fields:
        print(f"\n=== 3) uncertain {len(uncertain_fields)}건에 대해 LLM judge 호출 시도 ===")
        try:
            from judge import judge_selector_equivalence
            result = judge_selector_equivalence(gold_sig["target"], fake_pred_sig["target"])
            print("  judge 응답:", result["verdict"], "-", result["reason"][:200])
        except Exception as e:  # noqa: BLE001 - 로컬 서버 연결 실패 시 정직하게 보고
            print(f"  LLM judge 호출 실패 (로컬 Exaone 서버 연결 확인 필요): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
