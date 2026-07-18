"""골드셋 작성 화면(RAGAS 데이터셋 탭)의 문서 브라우저용 — `scripts/ragas_eval/datasets/
build_source_documents.py`가 채워둔 로컬 Postgres `source_documents` 테이블을 읽기 전용으로
조회한다.

이 테이블은 로컬 전용이라(원격 Neon과 무관, chunk_size 실험용 pre-chunk 원본 스냅샷)
RAG_DATABASE_URL을 의도적으로 안 보고 DATABASE_* 로컬 값으로만 연결한다 — build_candidate.py의
local_dsn()과 동일한 계약.
"""

import os

import psycopg


def local_dsn() -> str:
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5432"
    name = os.getenv("DATABASE_NAME") or "a360"
    user = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
    return f"host={host} port={port} dbname={name} user={user} password={password}"


class SourceDocumentsUnavailableError(RuntimeError):
    """로컬 source_documents 테이블에 연결할 수 없음 — build_source_documents.py를 먼저
    실행해야 한다는 뜻일 수 있다."""


def search(query: str = "", source_type: str | None = None, limit: int = 100) -> list[dict]:
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
                    {"q": query, "like": f"%{query}%", "source_type": source_type, "limit": limit},
                )
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    except psycopg.OperationalError as e:
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e


def random_sample(
    source_type: str | None = None,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
    min_content_length: int = 0,
) -> list[dict]:
    """골드셋 작성용 랜덤 문서 추출 — 프론트가 전체 목록을 가져와 JS로 섞는 게 아니라
    DB에서 직접 뽑는다(문서 수천 개를 매번 다 내려받지 않도록)."""
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
                        "limit": limit,
                    },
                )
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    except psycopg.OperationalError as e:
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e


def get_by_id(doc_id: str) -> dict | None:
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM source_documents WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [d.name for d in cur.description]
                return dict(zip(columns, row))
    except psycopg.OperationalError as e:
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e
