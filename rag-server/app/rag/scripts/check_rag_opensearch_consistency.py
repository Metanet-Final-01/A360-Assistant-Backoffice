"""RAG DB(pgvector `rag_documents`)와 Bonsai OpenSearch 색인의 문서 정합성 점검 (읽기 전용).

하이브리드 검색(pgvector 벡터 + BM25 RRF)은 두 저장소가 **같은 문서 집합**을 가져야 정상
동작한다. 둘은 같은 id를 공유한다 — OpenSearch `_id` == DB `rag_documents.id`
(store/opensearch_client.py `bulk_index`가 그렇게 색인). 그런데 `bulk_index`는
`op_type=index`(upsert, stale 삭제 안 함)라 다음 drift가 생길 수 있다:

  - db_only: DB엔 있는데 OS 색인에 없음  → BM25로 검색 안 됨(하이브리드 반쪽).
  - os_only: OS엔 있는데 DB엔 없음      → 죽은 hit(존재하지 않는 DB 행을 가리키는 orphan).

기존 reindex_opensearch_from_db.py는 **카운트만** 비교한다(count 일치 ≠ id 일치). 이 스크립트는
id 집합을 직접 비교해 drift를 드러낸다. **읽기 전용** — 스키마·데이터를 바꾸지 않으므로 읽기
전용 크레덴셜로도 돌 수 있다(수정은 reindex --apply의 역할).

종료 코드: 0=정합, 1=drift 발견, 2=점검 불가(DB·OS 접속/조회 실패 또는 OS 색인 없음) — 모든
인프라 실패는 traceback이 아니라 구조화 JSON + exit 2로 낸다. ops·CI 게이트로 쓸 수 있다.

사용: python -m app.rag.scripts.check_rag_opensearch_consistency [--sample N] [--batch-size N]
"""
from __future__ import annotations

import argparse
import heapq
import json
import sys
from typing import Any

from app.rag import config


def fetch_db_ids(conn, *, batch_size: int = 1000) -> tuple[set[str], dict[str, int]]:
    """rag_documents의 모든 id와 source_type별 카운트를 읽는다(읽기 전용, id 커서 페이지네이션)."""
    ids: set[str] = set()
    by_source: dict[str, int] = {}
    last_id = ""
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type
                FROM rag_documents
                WHERE id > %s
                ORDER BY id
                LIMIT %s
                """,
                (last_id, batch_size),
            )
            rows = cur.fetchall()
        if not rows:
            break
        for doc_id, source_type in rows:
            ids.add(doc_id)
            by_source[source_type] = by_source.get(source_type, 0) + 1
        last_id = rows[-1][0]
    return ids, by_source


def fetch_os_ids(client) -> set[str] | None:
    """OpenSearch 색인의 모든 _id를 읽는다(_source 미포함 = 가벼움). 색인이 없으면 None."""
    if not client.indices.exists(index=config.OPENSEARCH_INDEX):
        return None
    from opensearchpy.helpers import scan

    return {
        hit["_id"]
        for hit in scan(
            client,
            index=config.OPENSEARCH_INDEX,
            query={"query": {"match_all": {}}},
            _source=False,
        )
    }


def compare(
    db_ids: set[str],
    os_ids: set[str],
    db_by_source: dict[str, int],
    *,
    sample: int,
) -> dict[str, Any]:
    """두 id 집합의 drift를 리포트로 만든다 — 순수 함수(단위 테스트 대상).

    db_only = DB에만(OS 색인 누락), os_only = OS에만(DB orphan). 둘 다 비어야 in_sync.
    """
    db_only = db_ids - os_ids
    os_only = os_ids - db_ids
    db_only_count = len(db_only)
    os_only_count = len(os_only)
    # 큰 코퍼스에서 전체 diff를 sort 후 슬라이스하면 O(n log n)·메모리 낭비 — 샘플만 필요하므로
    # heapq.nsmallest로 상위 sample개만 뽑는다(결과는 sorted[:sample]과 동일한 결정적 순서).
    return {
        "in_sync": not db_only and not os_only,
        "db_total": len(db_ids),
        "os_total": len(os_ids),
        "in_both": len(db_ids) - db_only_count,  # = db_total - db_only (교집합 재계산 없이)
        "db_only_count": db_only_count,  # DB엔 있는데 OS 색인에 없음 → BM25 검색 불가
        "os_only_count": os_only_count,  # OS엔 있는데 DB엔 없음 → 죽은 hit(orphan)
        "db_only_sample": heapq.nsmallest(sample, db_only),
        "os_only_sample": heapq.nsmallest(sample, os_only),
        "db_by_source_type": dict(sorted(db_by_source.items())),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAG DB(rag_documents)와 Bonsai OpenSearch 색인의 문서 id 정합성 점검(읽기 전용).",
    )
    parser.add_argument("--sample", type=int, default=20, help="drift id 샘플 출력 개수 (기본 20).")
    parser.add_argument("--batch-size", type=int, default=1000, help="DB id 조회 페이지 크기.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.sample < 0:
        raise SystemExit("--sample must be >= 0")

    from app.rag.store import db, opensearch_client

    # 읽기 전용 계약: ensure_schema(DDL·commit)는 호출하지 않는다 — 진단 스크립트가 스키마를
    # 바꾸면 안 되고, 읽기 전용 크레덴셜로도 돌 수 있어야 한다(적재 파이프라인만 쓰기 허용).
    # 테이블이 없으면 fetch_db_ids 쿼리가 실패 → 아래 except가 "점검 불가(exit 2)"로 처리한다.
    conn = None
    try:
        conn = db.connect()
        db_ids, db_by_source = fetch_db_ids(conn, batch_size=args.batch_size)
    except Exception as exc:  # noqa: BLE001 — DB 접속·조회 실패도 구조화 출력+exit 2 (게이트 계약)
        print(json.dumps({
            "in_sync": False,
            "error": f"RAG DB 접속/조회 실패: {type(exc).__name__}: {exc}",
            "index": config.OPENSEARCH_INDEX,
            "db_total": None,
            "os_total": None,
        }, ensure_ascii=False, indent=2))
        return 2
    finally:
        if conn is not None:
            conn.close()

    client = opensearch_client.connect()
    try:
        os_ids = fetch_os_ids(client)
    except Exception as exc:  # noqa: BLE001 — 접속·인증·조회 실패를 진단 메시지로
        print(json.dumps({
            "in_sync": False,
            "error": f"OpenSearch 접속/조회 실패: {type(exc).__name__}: {exc}",
            "index": config.OPENSEARCH_INDEX,
            "db_total": len(db_ids),
            "os_total": None,
        }, ensure_ascii=False, indent=2))
        return 2

    if os_ids is None:
        print(json.dumps({
            "in_sync": False,
            "error": f"OpenSearch 색인 '{config.OPENSEARCH_INDEX}'이 없습니다 — 적재/reindex를 먼저 실행하세요.",
            "index": config.OPENSEARCH_INDEX,
            "db_total": len(db_ids),
            "os_total": None,
        }, ensure_ascii=False, indent=2))
        return 2

    report = {"index": config.OPENSEARCH_INDEX, **compare(db_ids, os_ids, db_by_source, sample=args.sample)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["in_sync"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
