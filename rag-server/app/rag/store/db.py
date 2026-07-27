"""pgvector 저장소. docker-compose의 pgvector/pgvector:pg16 컨테이너를 그대로 사용한다."""

import hashlib
import json

import psycopg

from .. import config
from ..observability import log_call

_DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag_documents (
    id            text PRIMARY KEY,
    source_type   text NOT NULL,
    package_name  text,
    action_name   text,
    locale        text,
    title         text NOT NULL,
    url           text,
    content       text NOT NULL,
    metadata      jsonb NOT NULL DEFAULT '{{}}',
    embedding     vector({config.EMBEDDING_DIM}),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- 이미 존재하는 테이블에도 청킹 컬럼이 반영되도록 ADD COLUMN IF NOT EXISTS로 추가 (별도 마이그레이션 도구 없이)
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS parent_id text;
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS chunk_index integer NOT NULL DEFAULT 0;
-- 재적재 시 내용이 안 바뀐 문서를 건너뛰기 위한 content 해시 (재크롤링마다 전체 재임베딩되던 문제)
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS content_hash text;
CREATE INDEX IF NOT EXISTS idx_rag_documents_package ON rag_documents (package_name);
CREATE INDEX IF NOT EXISTS idx_rag_documents_source ON rag_documents (source_type);
CREATE INDEX IF NOT EXISTS idx_rag_documents_parent ON rag_documents (parent_id);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(config.database_dsn())


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_content_hashes(conn: psycopg.Connection, ids: list[str]) -> dict[str, str]:
    """주어진 id들의 저장된 content_hash — 재적재 시 내용이 안 바뀐 문서를 건너뛰는 데 쓴다
    (cmd_ingest). id가 없거나 content_hash가 아직 없는(과거 컬럼 추가 전 row) 문서는
    결과에서 빠져 "변경됨"으로 취급된다 — 안전한 기본값(모르면 재처리)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, content_hash FROM rag_documents WHERE id = ANY(%s) AND content_hash IS NOT NULL",
            (ids,),
        )
        return dict(cur.fetchall())


def clear_all(conn: psycopg.Connection) -> None:
    """rag_documents 전체 삭제. upsert는 새 build에 없는 옛 row를 절대 지우지 않으므로,
    RAG 구조를 크게 바꿔 재적재할 때(예: 패키지/액션 스키마 커버리지 범위 변경) 쓰는
    명시적 초기화 — 자동으로 호출되지 않고 `ingest --clean`에서만 실행된다."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE rag_documents")
    conn.commit()


def upsert_documents(
    conn: psycopg.Connection,
    documents: list[dict],
    embeddings: list | None,
    *,
    batch_size: int = 200,
) -> int:
    sql = """
        INSERT INTO rag_documents
            (id, source_type, package_name, action_name, locale, title, url, content, metadata,
             parent_id, chunk_index, content_hash, embedding, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, now())
        ON CONFLICT (id) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            package_name = EXCLUDED.package_name,
            action_name = EXCLUDED.action_name,
            locale = EXCLUDED.locale,
            title = EXCLUDED.title,
            url = EXCLUDED.url,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            parent_id = EXCLUDED.parent_id,
            chunk_index = EXCLUDED.chunk_index,
            content_hash = EXCLUDED.content_hash,
            embedding = COALESCE(EXCLUDED.embedding, rag_documents.embedding),
            updated_at = now()
    """
    with conn.cursor() as cur:
        for i, doc in enumerate(documents):
            vector = None
            if embeddings is not None and embeddings[i] is not None:
                vector = "[" + ",".join(f"{x:.7f}" for x in embeddings[i]) + "]"
            cur.execute(
                sql,
                (
                    doc["id"],
                    doc["source_type"],
                    doc.get("package_name"),
                    doc.get("action_name"),
                    doc.get("locale"),
                    doc["title"],
                    doc.get("url"),
                    doc["content"],
                    json.dumps(doc.get("metadata", {}), ensure_ascii=False),
                    doc.get("parent_id", doc["id"]),
                    doc.get("chunk_index", 0),
                    content_hash(doc["content"]),
                    vector,
                ),
            )
        conn.commit()
    return len(documents)


@log_call("vector_search", capture_args=("limit",), capture_result=lambda r: {"count": len(r)})
def search(conn: psycopg.Connection, query_embedding: list[float], limit: int = 5) -> list[dict]:
    vector = "[" + ",".join(f"{x:.7f}" for x in query_embedding) + "]"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_type, package_name, action_name, title, url, content,
                   parent_id, chunk_index,
                   1 - (embedding <=> %s::vector) AS score
            FROM rag_documents
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, vector, limit),
        )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def corpus_overlap_stats(conn: psycopg.Connection, parent_ids: list[str]) -> dict:
    """기존 코퍼스와 이번 빌드의 겹침을 한 쿼리로 요약한다 (읽기 전용 — 감사/경고용).

    반환: total_rows(DB 전체 행), total_parents(DB의 distinct parent_id),
          unseen_parents(DB에만 있고 이번 빌드에는 없는 parent_id 수).

    delete_orphans의 범위가 "이번 빌드에 등장한 parent_id"로 한정돼 있어서, id/parent_id
    산식 자체가 바뀐 재적재(v1 코퍼스 위에 v2 산출물을 비-clean 적재)에서는 옛 행이 단
    한 건도 안 지워진다 — 유효한 임베딩을 단 채 검색에 계속 잡힌다(M3). 호출부가 이
    통계로 그 상황을 감지해 "--clean이 필요하다"고 경고한다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT parent_id),"
            " count(DISTINCT parent_id) FILTER (WHERE NOT (parent_id = ANY(%s)))"
            " FROM rag_documents",
            (list(parent_ids),),
        )
        total_rows, total_parents, unseen_parents = cur.fetchone()
    return {
        "total_rows": total_rows or 0,
        "total_parents": total_parents or 0,
        "unseen_parents": unseen_parents or 0,
    }


def delete_orphans(conn: psycopg.Connection, keep_ids: list[str], parent_ids: list[str]) -> list[str]:
    """이번 빌드에 없는 옛 row를 지운다. 커밋하지 않는다 — 호출부가 upsert_documents와
    같은 트랜잭션에 묶어 확정한다(중간에 실패하면 삭제도 함께 롤백되도록).

    범위를 "이번 빌드에 등장한 parent_id"로 한정하는 이유: ingest는 `--source docs`/`github`
    처럼 코퍼스의 일부만 재적재할 수 있어서, 이번 빌드에 없는 parent는 남의 소스일 뿐
    고아가 아니다. 그 범위 안에서 id가 keep_ids에 없는 row를 지우면 (a) 문서가 사라진 경우와
    (b) 청크 수가 5→2로 줄어 뒤쪽 청크(idx 2~4)만 옛 본문으로 잔존하는 경우가 함께 정리된다.

    안전장치: keep_ids가 비면 아무것도 지우지 않는다 — 빌드 산출물이 비었거나 로드에
    실패했을 때 DELETE가 전량 삭제로 돌변하는 사고를 막는다.

    반환: 삭제된 row의 id 목록 (건수는 len(), OpenSearch에서도 같은 id를 지우는 데 쓴다)."""
    if not keep_ids or not parent_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM rag_documents WHERE parent_id = ANY(%s) AND NOT (id = ANY(%s)) RETURNING id",
            (parent_ids, keep_ids),
        )
        return [row[0] for row in cur.fetchall()]


