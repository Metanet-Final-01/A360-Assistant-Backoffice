"""data/ingest/packages.json(잡아온 A360 패키지 jar의 package.json 원본)에서
패키지/액션 정식 목록을 뽑아 evals/catalog_actions.json으로 굳힌다.

docs/RAG_CATALOG.md는 이 데이터를 사람이 읽기 좋게 렌더링한 사본이라 텍스트가 손으로
다시 타이핑되거나 요약되며 오타가 섞일 수 있다. scoring.yaml/goldset 등 채점 기준에
쓸 "진짜 존재하는 package.action" 목록은 반드시 이 원본(jar의 package.json)에서
직접 뽑아야 한다 — 실제로 scoring.yaml을 이 카탈로그와 대조해보니 Excel_Basic,
WebRecorder, Outlook, Database 같은 존재하지 않는 패키지명이 섞여 있었다.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # app/eval/workflow -> eval -> app -> backend(repo root)
SOURCE = ROOT / "data" / "ingest" / "packages.json"
OUTPUT = Path(__file__).resolve().parent / "catalog_actions.json"


def build() -> dict:
    packages = json.loads(SOURCE.read_text(encoding="utf-8"))
    catalog = {}
    for pkg in packages:
        name = pkg["package_name"]
        actions = {
            action["name"]: {
                "label": action.get("label", ""),
                "return_type": action.get("return_type"),
            }
            for action in pkg.get("actions", [])
        }
        catalog[name] = {
            "label": pkg.get("package_label", ""),
            "source_jar": pkg.get("source_jar"),
            "actions": actions,
        }
    return dict(sorted(catalog.items()))


def main() -> None:
    catalog = build()
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    action_count = sum(len(p["actions"]) for p in catalog.values())
    print(f"{len(catalog)}개 패키지, {action_count}개 액션 -> {OUTPUT}")


if __name__ == "__main__":
    main()
