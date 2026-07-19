"""골드셋 작성 화면(RAGAS 데이터셋 탭)의 문서 브라우저용 — `scripts/ragas_eval/datasets/
build_source_documents.py`가 채워둔 로컬 Postgres `source_documents` 테이블을 읽기 전용으로
조회한다.

이 테이블은 로컬 전용이라(원격 Neon과 무관, chunk_size 실험용 pre-chunk 원본 스냅샷)
RAG_DATABASE_URL을 의도적으로 안 보고 DATABASE_* 로컬 값으로만 연결한다 — 연결 문자열은
build_source_documents.py와 완전히 같은 함수(scripts/ragas_eval/datasets/local_dsn.py)를
공유한다(두 곳에 복붙해서 한쪽만 고치다 어긋나는 걸 방지, CodeRabbit #42 지적).
"""

import logging
import os
import sys
from pathlib import Path

import psycopg
import requests

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCRIPTS_DATASETS_DIR = _REPO_ROOT / "scripts" / "ragas_eval" / "datasets"
if str(_SCRIPTS_DATASETS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DATASETS_DIR))

from local_dsn import local_dsn  # noqa: E402

_SCHEMA_SOURCE_TYPES = {"action_schema", "package_overview"}
_RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8200").rstrip("/")


class SourceDocumentsUnavailableError(RuntimeError):
    """로컬 source_documents 테이블에 연결할 수 없음 — build_source_documents.py를 먼저
    실행해야 한다는 뜻일 수 있다."""


def _fetch_schema_sources(parent_ids: list[str]) -> dict[str, str]:
    """parent_id 목록에 대응하는 schema_source(jar/llm_agent)를 rag-server의
    GET /rag/schema-sources를 통해 배치 조회한다. 로컬 source_documents 테이블엔
    이 컬럼이 아예 없어서(테이블을 다시 빌드하지 않고) 조회 시점에 붙인다.

    ops-server는 관측/RAG DB에 직접 연결하지 않고 rag-server API를 거친다는 정책을
    따른다(validation_log.py와 동일 패턴, CodeRabbit #42 지적 이후 정립 — 예전엔
    이 함수가 psycopg로 원격 DB에 직접 붙었는데, ops-server와 rag-server가 둘 다
    최상위 패키지명이 'app'이라 같은 프로세스에서 `from app.rag import config`를
    부르면 이미 로드된 ops-server의 app 패키지와 충돌해 조용히 실패하는 문제도
    있었다).

    rag-server가 안 떠 있거나 실패해도 조용히 빈 dict를 반환한다 — 이 조회는 부가
    정보라 실패해도 문서 브라우저 자체는 계속 동작해야 한다."""
    if not parent_ids:
        return {}
    try:
        response = requests.get(
            f"{_RAG_SERVER_URL}/rag/schema-sources",
            params={"parent_ids": ",".join(parent_ids)},
            timeout=5,
        )
        response.raise_for_status()
        return response.json().get("schema_sources", {})
    except requests.RequestException as e:
        logger.warning("schema_source 조회 실패 (문서 브라우저는 계속 동작): %s", e)
        return {}


def _attach_schema_source(rows: list[dict]) -> None:
    target_ids = [r["id"] for r in rows if r.get("source_type") in _SCHEMA_SOURCE_TYPES]
    schema_sources = _fetch_schema_sources(target_ids)
    for r in rows:
        r["schema_source"] = (
            schema_sources.get(r["id"]) if r.get("source_type") in _SCHEMA_SOURCE_TYPES else None
        )


def get_source_types(doc_ids: list[str]) -> dict[str, str]:
    """doc_id(로컬 source_documents.id = 원격 rag_documents.parent_id) 목록에
    대응하는 source_type을 로컬 테이블에서 배치 조회한다. 로컬에 없는 doc_id(진짜
    고아 케이스 등)는 결과 dict에서 아예 빠진다."""
    if not doc_ids:
        return {}
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, source_type from source_documents where id = any(%s::text[])",
                    (doc_ids,),
                )
                return {row[0]: row[1] for row in cur.fetchall()}
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        return {}


def enrich_cases_with_document_meta(cases: list[dict]) -> None:
    """골드셋 케이스 목록(전체목록 탭)에 근거 문서의 source_type/schema_source를
    붙인다 — 문서 유형/jar·llm_agent 출처별로 케이스 수를 세거나 필터링할 수 있게
    하기 위함(2026-07-20, "종류별로 조회 추가" 요청).

    reference_doc_ids[0] 하나만 대표로 본다 — 목록 필터링 용도로는 그걸로 충분하고,
    케이스 대부분이 근거 문서 1개다. 로컬/원격 어디에도 없는 doc_id(진짜 고아 케이스,
    예: excel_open_spreadsheet)는 둘 다 None으로 남는다."""
    doc_ids = [c["reference_doc_ids"][0] for c in cases if c.get("reference_doc_ids")]
    source_types = get_source_types(doc_ids)
    schema_source_target_ids = [
        doc_id for doc_id in doc_ids if source_types.get(doc_id) in _SCHEMA_SOURCE_TYPES
    ]
    schema_sources = _fetch_schema_sources(schema_source_target_ids)
    for c in cases:
        doc_id = c["reference_doc_ids"][0] if c.get("reference_doc_ids") else None
        c["source_type"] = source_types.get(doc_id) if doc_id else None
        c["schema_source"] = schema_sources.get(doc_id) if doc_id else None


def search(
    query: str = "",
    source_type: str | None = None,
    limit: int = 100,
    schema_source: str | None = None,
) -> list[dict]:
    # schema_source 필터가 걸리면 로컬 테이블에 없는 컬럼이라 SQL로 못 거르므로,
    # 넉넉한 풀을 가져온 뒤(최대 limit의 10배, 500건 상한) 원격 조회로 걸러낸다.
    pool_limit = limit if schema_source is None else min(max(limit * 10, 200), 500)
    sql = """
        SELECT id, source_type, title, package_name, action_name, path_titles, url,
               left(content, 200) AS preview
        FROM source_documents
        WHERE (%(q)s::text = '' OR title ILIKE %(like)s::text OR content ILIKE %(like)s::text)
          AND (%(source_type)s::text IS NULL OR source_type = %(source_type)s::text)
        ORDER BY package_name NULLS LAST, path_titles NULLS LAST, title
        LIMIT %(limit)s::int
    """
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "q": query,
                        "like": f"%{query}%",
                        "source_type": source_type,
                        "limit": pool_limit,
                    },
                )
                columns = [d.name for d in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e

    _attach_schema_source(rows)
    if schema_source is not None:
        rows = [r for r in rows if r.get("schema_source") == schema_source]
    return rows[:limit]


def random_sample(
    source_type: str | None = None,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
    min_content_length: int = 0,
    schema_source: str | None = None,
) -> list[dict]:
    """골드셋 작성용 랜덤 문서 추출 — 프론트가 전체 목록을 가져와 JS로 섞는 게 아니라
    DB에서 직접 뽑는다(문서 수천 개를 매번 다 내려받지 않도록).

    schema_source(jar/llm_agent)는 로컬 테이블에 없는 컬럼이라 SQL로 못 거른다 —
    대신 넉넉한 풀을 random() 순서로 가져온 뒤(이미 무작위 정렬된 상태이므로 앞에서부터
    잘라도 무작위성은 유지됨) 원격 조회로 schema_source를 붙이고 걸러낸다."""
    pool_limit = limit if schema_source is None else min(max(limit * 30, 200), 500)
    sql = """
        SELECT id, source_type, title, content, package_name, action_name, path_titles, url
        FROM source_documents
        WHERE (%(source_type)s::text IS NULL OR source_type = %(source_type)s::text)
          AND length(content) >= %(min_len)s::int
          AND (%(exclude)s::text[] IS NULL OR NOT (id = ANY(%(exclude)s::text[])))
        ORDER BY random()
        LIMIT %(limit)s::int
    """
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "source_type": source_type,
                        "min_len": min_content_length,
                        "exclude": exclude_ids or None,
                        "limit": pool_limit,
                    },
                )
                columns = [d.name for d in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e

    _attach_schema_source(rows)
    if schema_source is not None:
        rows = [r for r in rows if r.get("schema_source") == schema_source]
    return rows[:limit]


def get_by_id(doc_id: str) -> dict | None:
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM source_documents WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [d.name for d in cur.description]
                doc = dict(zip(columns, row))
                _attach_schema_source([doc])
                return doc
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e
