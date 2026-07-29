"""RAG action-catalog 커버리지를 2단계로 나눠서 체크한다 (사용자 요청: 별도 파일로 분리).

1순위(정확일치, 자동 신뢰): package/action을 소문자화 + 공백 제거만 정규화한 뒤
   완전히 일치하는 것. `If`/`IF`/`if` 같은 표기 노이즈만 잡고, 그 이상의 규칙은 쓰지
   않는다 -- PROVENANCE.md에서 이미 대소문자/공백/구두점 정규화로는 실제 리네임 122건
   중 0건만 복구됐다고 확인된 바 있으니, 이 정규화가 "진짜 다른 액션을 같다고 우기는"
   위험은 없다.

2순위(후보, 자동 채택 금지): 패키지가 같고 액션명이 접두사/접미사/부분문자열로
   겹치는 것. `Prompt.ForValue`/`Prompt.promptForValue`(접미사 일치)나
   `Salesforce.Authentication`/`Salesforce.authenticate`(접두사 일치) 같은 진짜
   리네임을 놓치지 않기 위한 것이지만, `DataTable.writeToFile`/`cloudWriteToFileAction`
   처럼 겹침 비율은 비슷해도 실제로는 다른 액션인 경우를 자동으로는 절대 같다고
   판정하지 않는다 -- 후보로만 뽑아서 공식문서 대조 등 사람/LLM 검증 트랙으로 넘긴다.

transitive(TaskBot.runTask로 이어지는 하위 워크플로우 포함) 계산과
is_real_call_graph_root(메인/서브 판정)는 processing/resolve_subtask_coverage.py의
기존 함수를 그대로 재사용한다 -- 중복 구현하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "processing"))

from resolve_subtask_coverage import (  # noqa: E402
    collect_direct_pairs_and_refs,
    default_workspace_root,
    load_rag_action_catalog,
    resolve_transitive,
)

MIN_CANDIDATE_OVERLAP_CHARS = 4  # 이보다 짧은 겹침은 우연의 일치일 확률이 높아 후보에서 뺀다


def normalize_name(value: str) -> str:
    return "".join(value.lower().split())


def build_normalized_catalog(catalog: set[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    """정규화된 (package, action) -> 원본 (package, action). 여러 원본이 같은 정규화
    키로 뭉치면(드묾) 먼저 본 것을 유지 -- 후보 생성 단계에서나 쓰이지, 1순위 커버리지
    판정 자체는 집합 멤버십만 보므로 이 충돌이 결과를 바꾸지 않는다."""
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for pkg, act in catalog:
        key = (normalize_name(pkg), normalize_name(act))
        result.setdefault(key, (pkg, act))
    return result


def overlap_kind(a: str, b: str) -> str | None:
    """a, b는 이미 normalize_name된 상태. 겹침 종류를 반환하거나(접두사/접미사/부분문자열)
    MIN_CANDIDATE_OVERLAP_CHARS 미만이면 None."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < MIN_CANDIDATE_OVERLAP_CHARS:
        return None
    if longer.startswith(shorter):
        return "prefix"  # shorter is a prefix of longer (e.g. authenticate vs authentication)
    if longer.endswith(shorter):
        return "suffix"  # shorter is a suffix of longer (e.g. forvalue vs promptforvalue)
    if shorter in longer:
        return "substring"  # shorter appears in the middle of longer
    return None


def find_candidates(
    missing_pair: tuple[str, str],
    normalized_catalog: dict[tuple[str, str], tuple[str, str]],
) -> list[dict]:
    pkg, act = missing_pair
    norm_pkg, norm_act = normalize_name(pkg), normalize_name(act)
    candidates = []
    for (cat_pkg_norm, cat_act_norm), (orig_pkg, orig_act) in normalized_catalog.items():
        if cat_pkg_norm != norm_pkg:
            continue  # 패키지가 정규화 후에도 다르면 후보에서 제외 (스코프를 좁혀 오탐 감소)
        kind = overlap_kind(norm_act, cat_act_norm)
        if kind:
            candidates.append({
                "catalog_package": orig_pkg,
                "catalog_action": orig_act,
                "overlap_kind": kind,
            })
    return candidates


@dataclass
class TieredCoverageRow:
    bot_name: str
    source_file: str
    is_main_workflow: bool
    own_action_count: int
    tier1_exact_covered: bool  # 정규화 후 완전일치만으로 own actions 전부 커버되는지
    tier1_missing: list[str]
    tier2_candidates_by_missing: dict[str, list[dict]]  # tier1_missing 각각에 대한 후보 목록
    transitive_action_count: int
    transitive_tier1_covered: bool
    transitive_tier1_missing: list[str]


def process_file(bot_dir: Path, goldset_path: Path, catalog: set[tuple[str, str]],
                  normalized_catalog: dict[tuple[str, str], tuple[str, str]],
                  normalized_exact_catalog: set[tuple[str, str]]) -> tuple[TieredCoverageRow, str, set[str]]:
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    own_pairs: set[tuple[str, str]] = set()
    own_refs: set[str] = set()
    collect_direct_pairs_and_refs(goldset.get("steps", []) or [], own_pairs, own_refs)

    own_missing_tier1 = sorted(
        (pkg, act) for pkg, act in own_pairs
        if (normalize_name(pkg), normalize_name(act)) not in normalized_exact_catalog
    )
    tier2 = {
        f"{pkg}.{act}": find_candidates((pkg, act), normalized_catalog)
        for pkg, act in own_missing_tier1
    }

    stem = goldset_path.name[: -len(".goldset.json")]
    transitive_pairs, unresolved = resolve_transitive(bot_dir / "workflows", stem, visited=set())
    transitive_missing_tier1 = sorted(
        (pkg, act) for pkg, act in transitive_pairs
        if (normalize_name(pkg), normalize_name(act)) not in normalized_exact_catalog
    )

    row = TieredCoverageRow(
        bot_name=bot_dir.name, source_file=goldset_path.name,
        is_main_workflow=False,  # collect_all에서 2차 패스로 채움
        own_action_count=len(own_pairs),
        tier1_exact_covered=not own_missing_tier1,
        tier1_missing=[f"{p}.{a}" for p, a in own_missing_tier1],
        tier2_candidates_by_missing=tier2,
        transitive_action_count=len(transitive_pairs),
        transitive_tier1_covered=not transitive_missing_tier1 and not unresolved,
        transitive_tier1_missing=[f"{p}.{a}" for p, a in transitive_missing_tier1],
    )
    return row, stem, own_refs


def collect_all(dataset_dir: Path, category: str, catalog: set[tuple[str, str]]) -> list[TieredCoverageRow]:
    normalized_catalog = build_normalized_catalog(catalog)
    normalized_exact_catalog = set(normalized_catalog.keys())

    category_dir = dataset_dir / category
    rows_by_key: dict[tuple[str, str], TieredCoverageRow] = {}
    for bot_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
        workflows_dir = bot_dir / "workflows"
        if not workflows_dir.exists():
            continue
        bot_rows: dict[str, TieredCoverageRow] = {}
        bot_refs: dict[str, set[str]] = {}
        for goldset_path in sorted(workflows_dir.glob("*.goldset.json")):
            row, stem, own_refs = process_file(
                bot_dir, goldset_path, catalog, normalized_catalog, normalized_exact_catalog
            )
            bot_rows[stem] = row
            bot_refs[stem] = own_refs

        really_referenced: set[str] = set()
        for stem, refs in bot_refs.items():
            really_referenced.update(ref for ref in refs if ref != stem)
        for stem, row in bot_rows.items():
            row.is_main_workflow = stem not in really_referenced
            rows_by_key[(bot_dir.name, row.source_file)] = row
    return list(rows_by_key.values())


def default_dataset_dir() -> Path:
    return default_workspace_root() / "Test" / "botstore_deep" / "full_470_dataset"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiered (exact vs candidate) RAG action-catalog coverage check.")
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir())
    parser.add_argument("--category", default="full_470")
    parser.add_argument("--rag-documents-jsonl", type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    workspace_root = default_workspace_root()
    rag_documents_jsonl = args.rag_documents_jsonl or (
        workspace_root / "A360-Assistant-Ops" / "rag-server" / "data" / "ingest" / "rag_documents.jsonl"
    )
    if not rag_documents_jsonl.exists():
        raise SystemExit(f"RAG documents export not found: {rag_documents_jsonl}")

    catalog = load_rag_action_catalog(rag_documents_jsonl)
    rows = collect_all(dataset_dir, args.category, catalog)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rag_documents_jsonl": str(rag_documents_jsonl),
        "summary": {
            "total_files": len(rows),
            "main_workflows": sum(1 for r in rows if r.is_main_workflow),
            "tier1_own_covered": sum(1 for r in rows if r.tier1_exact_covered),
            "tier1_transitive_covered": sum(1 for r in rows if r.transitive_tier1_covered),
            "main_and_tier1_transitive_covered": sum(
                1 for r in rows if r.is_main_workflow and r.transitive_tier1_covered
            ),
        },
        "rows": [asdict(r) for r in rows],
    }
    json_output = args.json_output or (dataset_dir / "rag_coverage_tiered_report.json")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"json": str(json_output), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
