"""a360-eval-sandbox의 A/B 비교 결과(eval_runs/<run>/pm4py_agent_conformance_results_*.json,
worfbench_openai_results_*.json)를 이 앱의 평가 로그(EvalRunRecord)로 가져온다.

sandbox의 AB_comparison_report.xlsx가 보여주는 "지표별 A/B 델타"를 이 앱의 조회·비교
화면(2개 선택 → 비교 차트)으로도 볼 수 있게 하려고 만들었다 — 엑셀을 웹에 그대로
올리는 대신, 각 bot×채점방법×run(A/B)을 EvalRunRecord 한 건으로 변환해 기존 조회·
비교 기능을 그대로 재사용한다.

사용:
    cd backend && python -m app.eval.scripts.import_sandbox_ab <run_folder> [--dry-run]

<run_folder>는 pm4py_agent_conformance_results_*.json / worfbench_openai_results_*.json이
있는 폴더 경로 (예: ../a360-eval-sandbox/Metadata/eval_runs/2026-07-09_04_ab-fixed-representation).
파일명 접미사(runA_rpa27, runB_dev 등)를 agent_label로 쓴다.

가져온 결과는 backend/data/eval_runs.jsonl에 append된다 — 이 파일은 .gitignore의
"backend/data/"에 걸려 로컬 전용이다. sandbox 원본이 이 리포에 커밋되는 게 아니라
조회·비교용 파생 레코드만 로컬에 쌓인다. 반대로 pm4py/worfbench가 "어떤 형식을
요구하는지" 자체를 보여주는 예시는 (커밋되는) ../format_examples/에 별도로 있다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/ 를 import 루트로

from app.eval.format_schemas import validate_format  # noqa: E402
from app.eval.log_schema import EvalMetric, EvalRunRecord  # noqa: E402
from app.eval.log_store import append_run  # noqa: E402

_PM4PY_PATTERN = re.compile(r"^pm4py_agent_conformance_results_(?P<label>.+)\.json$")
_WORFBENCH_PATTERN = re.compile(r"^worfbench_openai_results_(?P<label>.+)\.json$")


def _discover(run_dir: Path, pattern: re.Pattern) -> dict[str, Path]:
    return {
        m.group("label"): p
        for p in run_dir.glob("*.json")
        if (m := pattern.match(p.name))
    }


def _import_pm4py(path: Path, label: str, dry_run: bool) -> tuple[int, list[str]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    ok, problems = 0, []
    for r in records:
        errors = validate_format("pm4py", r)
        if errors:
            problems.append(f"pm4py/{label}/{r.get('source_bot', '?')}: {'; '.join(errors)}")
            continue
        metrics = [
            EvalMetric(name=name, value=r[key])
            for name, key in [("pm4py_fitness", "fitness"), ("pm4py_precision", "precision")]
            if r.get(key) is not None
        ]
        record = EvalRunRecord(
            run_id="",
            case_id=r["source_bot"],
            source="pm4py",
            agent_label=label,
            score=r.get("fitness"),
            metrics=metrics,
            raw=r,
        )
        if not dry_run:
            append_run(record)
        ok += 1
    return ok, problems


def _import_worfbench(path: Path, label: str, dry_run: bool) -> tuple[int, list[str]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    ok, problems = 0, []
    for r in records:
        errors = validate_format("worfbench", r)
        if errors:
            problems.append(f"worfbench/{label}/{r.get('source_bot', '?')}: {'; '.join(errors)}")
            continue
        metrics = [
            EvalMetric(name=f"worfbench_{key}", value=r[key])
            for key in ("precision", "recall", "f1_score")
            if r.get(key) is not None
        ]
        record = EvalRunRecord(
            run_id="",
            case_id=r["source_bot"],
            source="worfbench",
            agent_label=label,
            score=r.get("f1_score"),
            metrics=metrics,
            raw=r,
        )
        if not dry_run:
            append_run(record)
        ok += 1
    return ok, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="pm4py/worfbench 결과 json이 있는 폴더")
    parser.add_argument("--dry-run", action="store_true", help="검증만 하고 기록하지 않는다")
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"{run_dir}는 폴더가 아닙니다")

    pm4py_files = _discover(run_dir, _PM4PY_PATTERN)
    worfbench_files = _discover(run_dir, _WORFBENCH_PATTERN)
    if not pm4py_files and not worfbench_files:
        raise SystemExit(
            f"{run_dir}에서 pm4py_agent_conformance_results_*.json / "
            "worfbench_openai_results_*.json을 찾지 못했습니다"
        )

    total_ok, total_problems = 0, []
    for label, path in sorted(pm4py_files.items()):
        ok, problems = _import_pm4py(path, label, args.dry_run)
        total_ok += ok
        total_problems.extend(problems)
        print(f"pm4py  {label}: {ok}건 {'검증됨(dry-run)' if args.dry_run else '기록됨'}, {len(problems)}건 검증 실패")

    for label, path in sorted(worfbench_files.items()):
        ok, problems = _import_worfbench(path, label, args.dry_run)
        total_ok += ok
        total_problems.extend(problems)
        print(f"worfbench {label}: {ok}건 {'검증됨(dry-run)' if args.dry_run else '기록됨'}, {len(problems)}건 검증 실패")

    if total_problems:
        print("\n검증 실패 목록:")
        for p in total_problems:
            print(f"  - {p}")
        return 1

    print(f"\n총 {total_ok}건 {'검증' if args.dry_run else '기록'} 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
