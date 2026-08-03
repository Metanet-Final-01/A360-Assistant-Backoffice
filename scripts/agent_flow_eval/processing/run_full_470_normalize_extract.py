"""normalize_manifests.py / extract_workflows.py는 98개 제목기반 숏리스트의
3-카테고리 폴더 구조(root_dir/<category>/<bot_dir>/)를 전제로 순회한다. 470개 전체
감사에는 그 사전 분류가 없는 flat 구조(root_dir/<bot_dir>/, unpack_all_downloads.py의
출력)를 쓰므로, 두 스크립트의 핵심 per-bot 함수(process_bot)를 그대로 재사용하되
category는 "full_470" 고정 라벨로 채워서 flat하게 돈다. 원본 두 스크립트는 건드리지
않는다(98개 숏리스트 경로는 그대로 동작해야 함)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import extract_workflows
import normalize_extracted_workflows
import normalize_manifests

CATEGORY_LABEL = "full_470"


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_root_dir() -> Path:
    return default_workspace_root() / "Test" / "botstore_deep" / "full_470_unpacked"


def default_dataset_dir() -> Path:
    return default_workspace_root() / "Test" / "botstore_deep" / "full_470_dataset"


def collect_flat_bot_dirs(root_dir: Path) -> list[Path]:
    return sorted(
        path for path in root_dir.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run normalize_manifests + extract_workflows over the flat full-470 unpacked pool."
    )
    parser.add_argument("--root-dir", type=Path, default=default_root_dir())
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    args = parser.parse_args()

    root_dir = args.root_dir.resolve()
    dataset_dir = args.dataset_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    bot_dirs = collect_flat_bot_dirs(root_dir)
    print(f"봇 폴더 {len(bot_dirs)}개 발견 (manifest.json 있는 것만)")

    normalize_results = []
    extract_results = []
    for index, bot_dir in enumerate(bot_dirs, 1):
        print(f"[{index}/{len(bot_dirs)}] {bot_dir.name}", flush=True)
        normalize_results.append(
            normalize_manifests.process_bot(CATEGORY_LABEL, bot_dir, root_dir, output_root=None)
        )
        extract_results.extend(
            extract_workflows.process_bot(CATEGORY_LABEL, bot_dir, root_dir, dataset_dir)
        )

    normalize_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "summary": {
            "total": len(normalize_results),
            "created": sum(1 for r in normalize_results if r.status == "created"),
            "failed": sum(1 for r in normalize_results if r.status == "failed"),
        },
        "rows": [asdict(r) for r in normalize_results],
    }
    norm_json = root_dir / "manifest_normalization_report.json"
    norm_json.write_text(json.dumps(normalize_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("normalize summary:", normalize_payload["summary"])

    extract_summary = {
        "total": len(extract_results),
        "created": sum(1 for r in extract_results if r.status == "created"),
        "missing": sum(1 for r in extract_results if r.status == "missing"),
        "failed": sum(1 for r in extract_results if r.status == "failed"),
    }
    extract_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "dataset_dir": str(dataset_dir),
        "summary": extract_summary,
        "rows": [asdict(r) for r in extract_results],
    }
    dataset_dir.mkdir(parents=True, exist_ok=True)
    extract_json = dataset_dir / "workflow_extraction_report.json"
    extract_json.write_text(json.dumps(extract_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("extract summary:", extract_summary)

    # 3단계: extract_workflows.py가 만든 raw workflows/*.json 각각을 canonical
    # *.goldset.json으로 변환 (normalize_extracted_workflows.py의 per-file 함수 재사용).
    # collect_workflow_files는 모듈 상수 CATEGORY_DIRS(3개 고정 숏리스트명)를 내부에서
    # 직접 참조하므로, flat 구조의 우리 카테고리 라벨을 쓰도록 호출 직전에만 바꿔둔다
    # (원본 파일은 건드리지 않음 -- 98개 숏리스트 경로는 그대로 영향받지 않는다).
    normalize_extracted_workflows.CATEGORY_DIRS = (CATEGORY_LABEL,)
    goldset_entries = normalize_extracted_workflows.collect_workflow_files(dataset_dir)
    goldset_results = [
        normalize_extracted_workflows.process_workflow_file(category, bot_dir_rel, workflow_path)
        for category, bot_dir_rel, workflow_path in goldset_entries
    ]
    goldset_summary = {
        "total": len(goldset_results),
        "created": sum(1 for r in goldset_results if r.status == "created"),
        "failed": sum(1 for r in goldset_results if r.status == "failed"),
    }
    goldset_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "summary": goldset_summary,
        "rows": [asdict(r) for r in goldset_results],
    }
    goldset_json = dataset_dir / "goldset_normalization_report.json"
    goldset_json.write_text(json.dumps(goldset_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("goldset summary:", goldset_summary)

    print(json.dumps({
        "normalize_report": str(norm_json),
        "extract_report": str(extract_json),
        "goldset_report": str(goldset_json),
        "normalize_summary": normalize_payload["summary"],
        "extract_summary": extract_summary,
        "goldset_summary": goldset_summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
