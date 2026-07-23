"""골드셋 작성 화면(RAGAS 데이터셋 탭)의 문서 브라우저용 — `scripts/ragas_eval/datasets/
build_source_documents.py`가 채워둔 로컬 Postgres `source_documents` 테이블을 읽기 전용으로
조회한다.

이 테이블은 로컬 전용이라(원격 Neon과 무관, chunk_size 실험용 pre-chunk 원본 스냅샷)
RAG_DATABASE_URL을 의도적으로 안 보고 DATABASE_* 로컬 값으로만 연결한다 — 연결 문자열은
build_source_documents.py와 완전히 같은 함수(scripts/ragas_eval/datasets/local_dsn.py)를
공유한다(두 곳에 복붙해서 한쪽만 고치다 어긋나는 걸 방지, CodeRabbit #42 지적).
"""

import sys
from pathlib import Path

import psycopg


class SourceDocumentsUnavailableError(RuntimeError):
    """로컬 source_documents 테이블에 연결할 수 없음 — build_source_documents.py를 먼저
    실행해야 한다는 뜻일 수 있다."""


def _local_dsn() -> str:
    """local_dsn.py는 scripts/ragas_eval/datasets/(백엔드 트리 밖, 로컬 골드셋 작성 전용)에 있다.
    컨테이너 배포엔 이 디렉터리가 복사되지 않으므로 임포트 시점이 아니라 호출 시점에 로드해
    부팅을 막지 않는다 — 없으면 SourceDocumentsUnavailableError로 알린다(이 기능은 로컬 전용).
    build_source_documents.py와 같은 함수를 공유하는 계약은 그대로다(경로가 있을 때만)."""
    parents = Path(__file__).resolve().parents
    if len(parents) > 5:
        scripts_dir = parents[5] / "scripts" / "ragas_eval" / "datasets"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
    try:
        from local_dsn import local_dsn
    except ImportError as exc:
        raise SourceDocumentsUnavailableError(
            "local_dsn 모듈을 찾을 수 없습니다(scripts/ragas_eval/datasets/local_dsn.py) — "
            "이 문서 브라우저는 로컬 골드셋 작성 환경 전용입니다."
        ) from exc
    return local_dsn()


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
        with psycopg.connect(_local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {"q": query, "like": f"%{query}%", "source_type": source_type, "limit": limit},
                )
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
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
        with psycopg.connect(_local_dsn(), connect_timeout=5) as conn:
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
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e


def get_by_id(doc_id: str) -> dict | None:
    try:
        with psycopg.connect(_local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM source_documents WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [d.name for d in cur.description]
                return dict(zip(columns, row))
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        # OperationalError = DB/서버에 연결 자체가 안 됨. UndefinedTable(ProgrammingError의
        # 하위클래스라 OperationalError를 안 잡는다)은 연결은 되는데 테이블이 아직
        # 없는 경우(최초 실행) — 둘 다 "먼저 build_source_documents.py를 실행하라"는
        # 같은 사용자 안내로 이어져야 한다.
        raise SourceDocumentsUnavailableError(
            "로컬 source_documents 테이블에 연결할 수 없습니다 — "
            "scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요."
        ) from e
