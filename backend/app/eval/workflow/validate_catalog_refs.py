"""scoring.yaml / goldset json에 적힌 package.action 참조가 실제 카탈로그
(evals/catalog_actions.json, data/ingest/packages.json에서 생성)에 존재하는지 검사한다.

먼저 `python evals/build_catalog.py`로 catalog_actions.json을 최신 상태로 만들어 둘 것.

action 키 자체에 점이 들어가는 경우가 있어(예: Loop의 "loop.commands.start") 항상
"첫 번째 점" 기준으로 package/action을 나눈다 (패키지명에는 점이 없다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).resolve().parent / "catalog_actions.json"


def load_catalog() -> dict[str, set[str]]:
    if not CATALOG_PATH.exists():
        raise SystemExit(f"{CATALOG_PATH} 가 없습니다. 먼저 `python evals/build_catalog.py`를 실행하세요.")
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {pkg: set(info["actions"].keys()) for pkg, info in raw.items()}


def split_key(key: str) -> tuple[str, str]:
    package, _, action = key.partition(".")
    return package, action


def check_key(key: str, catalog: dict[str, set[str]]) -> str | None:
    """문제가 있으면 사람이 읽을 오류 메시지를 반환하고, 문제없으면 None."""
    package, action = split_key(key)
    if package not in catalog:
        return f"'{key}': 패키지 '{package}'가 카탈로그에 없음"
    if action not in catalog[package]:
        return f"'{key}': 액션 '{action}'이 패키지 '{package}'에 없음"
    return None


def collect_keys_from_scoring_yaml(path: Path) -> list[str]:
    scoring = yaml.safe_load(path.read_text(encoding="utf-8"))
    keys: list[str] = []
    for rule in scoring.get("must_have_actions", []):
        keys.extend(rule.get("any_of", []))
    for rule in scoring.get("order_rules", []):
        keys.extend(rule.get("before_any", []))
        keys.extend(rule.get("after_any", []))
    keys.extend(scoring.get("forbidden_actions", []))
    return keys


def collect_keys_from_goldset(path: Path) -> dict[str, list[str]]:
    """case_id -> 참조된 package.action 키 목록."""
    cases = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for case in cases:
        expected = case["expected"]
        keys = [f"{a['package']}.{a['action']}" for a in expected.get("actions", [])]
        # packages 목록도 패키지 존재 여부만 검사할 수 있도록 더미 액션 없이 표시
        for pkg in expected.get("packages", []):
            keys.append(f"{pkg}.__package_only__")
        result[case["id"]] = keys
    return result


def check_key_package_only(key: str, catalog: dict[str, set[str]]) -> str | None:
    package, action = split_key(key)
    if action == "__package_only__":
        return None if package in catalog else f"패키지 '{package}'가 카탈로그에 없음"
    return check_key(key, catalog)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scoring", type=Path, help="scoring.yaml 경로")
    parser.add_argument("--goldset", type=Path, help="goldset_from_bots.json 경로")
    args = parser.parse_args()

    if not args.scoring and not args.goldset:
        raise SystemExit("--scoring 또는 --goldset 중 하나 이상 지정하세요.")

    catalog = load_catalog()
    problems: dict[str, Any] = {}

    if args.scoring:
        keys = collect_keys_from_scoring_yaml(args.scoring)
        errors = sorted({msg for key in keys if (msg := check_key_package_only(key, catalog))})
        problems["scoring"] = {"file": str(args.scoring), "checked": len(keys), "errors": errors}

    if args.goldset:
        by_case = collect_keys_from_goldset(args.goldset)
        goldset_errors: dict[str, list[str]] = {}
        for case_id, keys in by_case.items():
            errs = sorted({msg for key in keys if (msg := check_key_package_only(key, catalog))})
            if errs:
                goldset_errors[case_id] = errs
        problems["goldset"] = {
            "file": str(args.goldset),
            "case_count": len(by_case),
            "cases_with_errors": goldset_errors,
        }

    print(json.dumps(problems, ensure_ascii=False, indent=2))

    has_errors = any(
        v.get("errors") or v.get("cases_with_errors") for v in problems.values()
    )
    if has_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
