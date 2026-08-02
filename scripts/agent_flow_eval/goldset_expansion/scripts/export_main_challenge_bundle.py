"""Main-18 / Challenge-21 (제외 41, 미분류 잔여는 제외)만 골라서 "우리 원본"
(Test/botstore_deep/full_470_dataset)으로부터 직접 복사 -> pm4py/WorFBench 변환까지
돌린 뒤, 팀원에게 전달할 짧은 이름의 평평한(flat) 폴더 + zip으로 묶는다.

원시(raw) Automation Anywhere workflow json도 같이 담는다: extract_workflows.py가
`workflows/{stem}.json`(원시, 트리형 원본)을 만들고 normalize_extracted_workflows.py가
그 옆에 `workflows/{stem}.goldset.json`(정규화, steps 평탄화)을 만드는 구조라 - 같은
폴더에 둘 다 있다. source_file(=...goldset.json)에서 ".goldset.json" -> ".json"만
바꾸면 원시 파일 경로를 그대로 찾을 수 있다 (실제로 존재하는지 매번 확인함).

GPT 분류 폴더(candidate_pool/gpt_bundle/gpt_final_classification/1_Main_18 등)의
파일명은 원본 zip 내부 경로를 통째로 이어붙인 거라 100자가 넘고, Windows 압축 해제
시 MAX_PATH(260자)를 넘어 풀리지 않는 경우가 있다. 그래서:
1. 원본 위치(_all_results.json에 있는 진짜 bot_name/source_file)를 다시 찾아서
2. bot_name(원래 짧음, 예: "0011_CareerInsightAgentMVP") + 워크플로우 파일명 꼬리
   (source_file의 마지막 "__" 뒤 부분, 예: "Tool_LogReviewData")만으로 짧은 이름을
   새로 만들어 사용한다.
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from path_utils import full_470_dataset_dir, goldset_expansion_dir  # noqa: E402

DATASET = full_470_dataset_dir()
BUNDLE_ROOT = goldset_expansion_dir() / "candidate_pool" / "gpt_bundle"
RESULTS_PATH = goldset_expansion_dir() / "exaone_judge_logs_148" / "_all_results.json"
CLASSIFICATION_FOLDERS = {
    "Main": BUNDLE_ROOT / "gpt_final_classification" / "1_Main_18",
    "Challenge": BUNDLE_ROOT / "gpt_final_classification" / "2_Challenge_21",
}

OUT_ROOT = goldset_expansion_dir() / "export_main_challenge"
STAGE_ROOT = OUT_ROOT / "_stage"
DELIVERABLE_ROOT = OUT_ROOT / "deliverable"
ZIP_PATH = OUT_ROOT / "main_challenge_normalized_pm4py_worfbench.zip"


def short_tail(source_file: str) -> str:
    stem = source_file[: -len(".goldset.json")]
    return stem.rsplit("__", 1)[-1]


def build_short_id(bot_name: str, source_file: str) -> str:
    return f"{bot_name}__{short_tail(source_file)}"


def main() -> None:
    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    if DELIVERABLE_ROOT.exists():
        shutil.rmtree(DELIVERABLE_ROOT)
    STAGE_ROOT.mkdir(parents=True)
    DELIVERABLE_ROOT.mkdir(parents=True)

    all_results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    lookup = {f"{r['bot_name']}__{r['source_file']}": r for r in all_results}

    items = []  # (classification, short_id, bot_name, source_file)
    for classification, folder in CLASSIFICATION_FOLDERS.items():
        for f in sorted(folder.glob("*.goldset.json")):
            row = lookup.get(f.name)
            if row is None:
                raise RuntimeError(f"원본 매칭 실패 (재확인 필요): {classification}/{f.name}")
            short_id = build_short_id(row["bot_name"], row["source_file"])
            items.append((classification, short_id, row["bot_name"], row["source_file"]))

    # 1) 원본(Test/botstore_deep)에서 직접 복사 -> 짧은 이름으로 스테이징
    #    (정규화된 goldset.json + 원시(raw) 워크플로우 json 둘 다)
    raw_missing = []
    for classification, short_id, bot_name, source_file in items:
        src = DATASET / bot_name / "workflows" / source_file
        if not src.exists():
            raise RuntimeError(f"원본 파일이 없음: {src}")
        workflows_dir = STAGE_ROOT / classification / short_id / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, workflows_dir / f"{short_id}.goldset.json")

        raw_source_file = source_file[: -len(".goldset.json")] + ".json"
        raw_src = DATASET / bot_name / "workflows" / raw_source_file
        if raw_src.exists():
            shutil.copy(raw_src, workflows_dir / f"{short_id}.raw.json")
        else:
            raw_missing.append(f"{bot_name}/workflows/{raw_source_file}")

    print(f"스테이징 완료: Main {sum(1 for i in items if i[0]=='Main')}개, "
          f"Challenge {sum(1 for i in items if i[0]=='Challenge')}개")
    if raw_missing:
        print(f"원시(raw) 파일 없음 {len(raw_missing)}건 (정규화본만 있음):")
        for m in raw_missing:
            print(f"  {m}")

    # 2) pm4py / WorFBench 변환 (원본 스크립트를 import해서 CATEGORY_DIRS만 바꿔 재사용)
    processing_dir = goldset_expansion_dir().parent / "processing"
    sys.path.insert(0, str(processing_dir))
    import convert_to_pm4py as pm4py_conv
    import convert_to_worfbench as worfbench_conv

    pm4py_conv.CATEGORY_DIRS = ("Main", "Challenge")
    worfbench_conv.CATEGORY_DIRS = ("Main", "Challenge")

    pm4py_failed = []
    for category, bot_dir_rel, goldset_path in pm4py_conv.collect_goldset_files(STAGE_ROOT):
        r = pm4py_conv.process_goldset_file(category, bot_dir_rel, goldset_path)
        if r.status != "created":
            pm4py_failed.append((bot_dir_rel, r.error))

    worfbench_failed = []
    for category, bot_dir_rel, goldset_path in worfbench_conv.collect_goldset_files(STAGE_ROOT):
        r = worfbench_conv.process_goldset_file(category, bot_dir_rel, goldset_path)
        if r.status != "created":
            worfbench_failed.append((bot_dir_rel, r.error))

    print(f"pm4py 변환 실패: {len(pm4py_failed)}건, WorFBench 변환 실패: {len(worfbench_failed)}건")
    for bot_dir_rel, err in pm4py_failed + worfbench_failed:
        print(f"  {bot_dir_rel}: {err}")

    # 3) 짧은 파일들만 평평하게 모아서 최종 전달용 폴더 구성 (bot_dir/workflows 중첩 제거)
    for classification in CLASSIFICATION_FOLDERS:
        dest_dir = DELIVERABLE_ROOT / classification
        dest_dir.mkdir(parents=True, exist_ok=True)
        category_dir = STAGE_ROOT / classification
        if not category_dir.exists():
            continue
        for bot_dir in category_dir.iterdir():
            workflows_dir = bot_dir / "workflows"
            if not workflows_dir.exists():
                continue
            for f in workflows_dir.iterdir():
                shutil.copy(f, dest_dir / f.name)

    # 4) zip (짧은 이름이라 압축 해제 시 경로 길이 문제 없음)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(DELIVERABLE_ROOT.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(DELIVERABLE_ROOT))

    main_files = list((DELIVERABLE_ROOT / "Main").glob("*"))
    challenge_files = list((DELIVERABLE_ROOT / "Challenge").glob("*"))
    print(f"\n최종 전달 폴더: {DELIVERABLE_ROOT}")
    print(f"  Main: {len(main_files)}개 파일 (raw/goldset/pnml/ptml/tree.json/worfbench.json 합계)")
    print(f"  Challenge: {len(challenge_files)}개 파일")
    print(f"zip: {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
