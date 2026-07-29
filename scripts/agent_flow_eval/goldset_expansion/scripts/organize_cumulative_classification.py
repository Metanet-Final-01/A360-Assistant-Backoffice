"""GPT가 준 분류 xlsx(예: workflow_goldset_screening_cumulative_80.xlsx, "전체 판정" 시트에
ID/후보/분류 컬럼)에 맞춰 candidate_pool/gpt_bundle 밑의 goldset 파일들을 Main/Challenge/
제외/미분류 폴더로 재정리한다. GPT 라운드가 늘어날 때마다(예: 다음엔 80->120개) 이 스크립트를
그대로 다시 돌리면 된다. --xlsx로 매번 최신 분류표를 지정.

주의: xlsx의 4자리 ID는 "봇 폴더" 단위 번호라 유일하지 않다. 한 봇 폴더 밑에 서브
워크플로우 파일이 여러 개 있으면 같은 ID를 여러 파일이 공유한다(예: 같은 ID로
GetOutcomes/GetCandidateNumber 둘 다 존재). 그래서 ID만으로 파일을 찾으면 안 되고,
후보가 여럿이면 반드시 candidate 이름(정규화: 소문자+영숫자만)으로 파일명 꼬리와
대조해 구분해야 한다. 이름으로도 못 가르면(예: 0203 API User Management Bot -
APIMaster/DeleteUserID 두 후보) 사람이 xlsx의 판정 근거를 읽고 수동으로 골라야 한다
- 이 스크립트는 그런 경우 "매칭 실패"로만 보고하고 자동 추측하지 않는다."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import openpyxl

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from path_utils import goldset_expansion_dir  # noqa: E402

BUNDLE_ROOT = goldset_expansion_dir() / "candidate_pool" / "gpt_bundle"
MAIN_34_DIR = BUNDLE_ROOT / "candidates_main_34"
# 34개 원본 외에 GPT가 아직 다 보지 못한 나머지 후보 풀 (사람이 rule-based 필터로 이미
# 분리해 둔 15개 + Exaone reasoning ON이 "있음" 판정을 안 준 나머지 99개).
NEW_CANDIDATE_POOL_DIRS_NAMES = [
    "candidates_deprioritized_15",
    "candidates_exaone_rejected_99",
]


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_rows(xlsx_path: Path, sheet: str) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[sheet]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        id_, name, classification = str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip()
        rows.append({"id": id_, "name": name, "classification": classification})
    return rows


def build_pool_index(pool_dirs: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for pool_dir in pool_dirs:
        for f in pool_dir.glob("*.goldset.json"):
            index.setdefault(f.name[:4], []).append(f)
    return index


def match_file(id_: str, name: str, pool_index: dict[str, list[Path]]) -> Path | None:
    candidates = pool_index.get(id_, [])
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    target = normalize(name)
    for f in candidates:
        stem = f.name[: -len(".goldset.json")]
        tail = stem.rsplit("__", 1)[-1]
        if normalize(tail) == target:
            return f
    for f in candidates:
        stem = f.name[: -len(".goldset.json")]
        if target in normalize(stem):
            return f
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, required=True, help="GPT가 준 분류 xlsx 경로")
    parser.add_argument("--sheet", default="전체 판정", help="ID/후보/분류 컬럼이 있는 시트 이름")
    parser.add_argument("--out", type=Path, default=BUNDLE_ROOT / "gpt_final_classification_new")
    args = parser.parse_args()

    pool_dirs = [MAIN_34_DIR] + [BUNDLE_ROOT / n for n in NEW_CANDIDATE_POOL_DIRS_NAMES]
    new_candidate_pool_dirs = pool_dirs[1:]

    main_out = args.out / "Main"
    challenge_out = args.out / "Challenge"
    excluded_out = args.out / "제외"
    remaining_out = args.out / "미분류_잔여후보"
    for d in (main_out, challenge_out, excluded_out, remaining_out):
        d.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.xlsx, args.sheet)
    pool_index = build_pool_index(pool_dirs)
    dest_by_classification = {"Main": main_out, "Challenge": challenge_out, "제외": excluded_out}

    counts = {"Main": 0, "Challenge": 0, "제외": 0}
    unmatched = []
    used_paths: set[Path] = set()

    for row in rows:
        id_, name, classification = row["id"], row["name"], row["classification"]
        dest_dir = dest_by_classification.get(classification)
        if dest_dir is None:
            unmatched.append((id_, name, classification, "알수없는 분류"))
            continue

        src = match_file(id_, name, pool_index)
        if src is None:
            unmatched.append((id_, name, classification, "파일 매칭 실패"))
            continue
        if src in used_paths:
            unmatched.append((id_, name, classification, f"이미 다른 행에서 사용된 파일: {src.name}"))
            continue

        dest_path = dest_dir / src.name
        if dest_path.exists():
            unmatched.append((id_, name, classification, f"목적지 이름충돌: {src.name}"))
            continue
        shutil.copy(src, dest_path)
        used_paths.add(src)
        counts[classification] += 1

    remaining_count = 0
    for pool_dir in new_candidate_pool_dirs:
        for f in pool_dir.glob("*.goldset.json"):
            if f not in used_paths:
                shutil.copy(f, remaining_out / f.name)
                remaining_count += 1

    print(f"Main: {counts['Main']}")
    print(f"Challenge: {counts['Challenge']}")
    print(f"제외: {counts['제외']}")
    print(f"미분류 잔여: {remaining_count}")
    if unmatched:
        print(f"\n매칭 실패/충돌 {len(unmatched)}건 (수동 확인 필요):")
        for u in unmatched:
            print(f"  {u}")
    print(f"\n저장 위치: {args.out}")


if __name__ == "__main__":
    main()
