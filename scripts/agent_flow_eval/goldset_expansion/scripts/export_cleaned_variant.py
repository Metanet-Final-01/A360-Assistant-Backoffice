"""Main-18/Challenge-21 de리버러블에 "클린" 버전을 추가로 만든다: LogToFile/MessageBox/
Screen 같은 공통 보조 액션(진짜 워크플로우 액션이지 주석은 아님 - 에러 로깅/확인 팝업/
실패 스크린샷 등)을 제거한 뒤 pm4py/WorFBench로 재변환한다. 기존 "원본 그대로" 파일은
그대로 두고 `.cleaned.*` 접미사로 나란히 추가한다.

제거 후 유의미한 액션이 거의 안 남는 후보가 있으면 그것도 같이 보고한다(사용자가 명시
적으로 요청한 검사) - 그런 후보는 애초에 노이즈 비중이 너무 커서 클린 버전 자체가
의미 없을 수 있다는 신호."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from action_filters import normalize_steps_for_evaluation  # noqa: E402
from path_utils import goldset_expansion_dir  # noqa: E402

LOW_ACTION_THRESHOLD = 3  # 클린 후 남은 액션이 이보다 적으면 "확인 필요"로 표시

EXPORT_ROOT = goldset_expansion_dir() / "export_main_challenge"
DELIVERABLE_ROOT = EXPORT_ROOT / "deliverable"
STAGE_ROOT = EXPORT_ROOT / "_clean_stage"
ZIP_PATH = EXPORT_ROOT / "main_challenge_normalized_pm4py_worfbench.zip"


def strip_noise(steps: list[dict]) -> list[dict]:
    return normalize_steps_for_evaluation(steps)


def count_actions(steps: list[dict]) -> int:
    total = 0
    for step in steps:
        if step.get("type") == "action":
            total += 1
        total += count_actions(step.get("steps", []) or [])
        for b in step.get("branches", []) or []:
            total += count_actions(b.get("steps", []) or [])
    return total


def main() -> None:
    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    STAGE_ROOT.mkdir(parents=True)

    report_rows = []
    for classification in ("Main", "Challenge"):
        src_dir = DELIVERABLE_ROOT / classification
        for f in sorted(src_dir.glob("*.goldset.json")):
            short_id = f.name[: -len(".goldset.json")]
            goldset = json.loads(f.read_text(encoding="utf-8"))

            before = count_actions(goldset.get("steps", []) or [])
            cleaned_steps = strip_noise(goldset.get("steps", []) or [])
            after = count_actions(cleaned_steps)

            cleaned_goldset = dict(goldset)
            cleaned_goldset["steps"] = cleaned_steps

            workflows_dir = STAGE_ROOT / classification / short_id / "workflows"
            workflows_dir.mkdir(parents=True, exist_ok=True)
            (workflows_dir / f"{short_id}.cleaned.goldset.json").write_text(
                json.dumps(cleaned_goldset, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            report_rows.append({
                "classification": classification,
                "short_id": short_id,
                "before": before,
                "after": after,
                "removed": before - after,
                "flag": after < LOW_ACTION_THRESHOLD,
            })

    # WorFBench 재변환 - CATEGORY_DIRS를 Main/Challenge로 맞추고, *.goldset.json을
    # 찾는 glob이 "*.cleaned.goldset.json"도 함께 잡아버리므로(끝이 .goldset.json으로
    # 같음) 문제없이 그대로 재사용 가능. PM4Py는 2026-08-03 완전히 삭제함(안 쓰기로
    # 확정) - 여기서도 더 이상 호출하지 않는다.
    processing_dir = goldset_expansion_dir().parent / "processing"
    sys.path.insert(0, str(processing_dir))
    import convert_to_worfbench as worfbench_conv

    worfbench_conv.CATEGORY_DIRS = ("Main", "Challenge")

    failed = []
    for category, bot_dir_rel, goldset_path in worfbench_conv.collect_goldset_files(STAGE_ROOT):
        r = worfbench_conv.process_goldset_file(category, bot_dir_rel, goldset_path)
        if r.status != "created":
            failed.append((bot_dir_rel, "worfbench", r.error))

    print(f"클린 변환 실패: {len(failed)}건")
    for bot_dir_rel, which, err in failed:
        print(f"  {which} {bot_dir_rel}: {err}")

    # 클린 산출물을 기존 deliverable 폴더에 나란히 복사 (원본 파일은 그대로 둠)
    copied = 0
    for classification in ("Main", "Challenge"):
        category_dir = STAGE_ROOT / classification
        dest_dir = DELIVERABLE_ROOT / classification
        if not category_dir.exists():
            continue
        for bot_dir in category_dir.iterdir():
            workflows_dir = bot_dir / "workflows"
            if not workflows_dir.exists():
                continue
            for f in workflows_dir.iterdir():
                shutil.copy(f, dest_dir / f.name)
                copied += 1

    shutil.rmtree(STAGE_ROOT)

    # zip 재생성 (기존 원본 + 새 클린 버전 다 포함)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(DELIVERABLE_ROOT.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(DELIVERABLE_ROOT))

    print(f"\n클린 파일 {copied}개 추가 복사 완료")
    print(f"zip 갱신: {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1024:.1f} KB)")

    print("\n=== 공통 평가 변환 규칙 적용 전/후 액션 수 ===")
    print(f"{'분류':10} {'후보':60} {'제거전':>6} {'제거후':>6} {'제거됨':>6}")
    flagged = []
    for row in sorted(report_rows, key=lambda r: r["after"]):
        marker = " <- 확인 필요 (제거 후 액션 매우 적음)" if row["flag"] else ""
        print(f"{row['classification']:10} {row['short_id']:60} {row['before']:6} {row['after']:6} {row['removed']:6}{marker}")
        if row["flag"]:
            flagged.append(row)

    print(f"\n총 {len(report_rows)}개 중 제거 후 액션 {LOW_ACTION_THRESHOLD}개 미만인 후보: {len(flagged)}개")
    for row in flagged:
        print(f"  [{row['classification']}] {row['short_id']}: {row['before']} -> {row['after']}")


if __name__ == "__main__":
    main()
