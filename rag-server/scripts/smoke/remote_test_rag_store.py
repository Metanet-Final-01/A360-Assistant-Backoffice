from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv
from opensearchpy import OpenSearch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT, ".env"))

TEST_TABLE = os.getenv("RAG_TEST_TABLE", "rag_documents_test")
TEST_INDEX = os.getenv("RAG_TEST_INDEX", "rag_documents_test")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def database_dsn() -> str:
    url = os.getenv("RAG_DATABASE_URL")
    if not url:
        raise SystemExit("RAG_DATABASE_URL is required for this remote test")
    return url.replace("postgresql+psycopg://", "postgresql://")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def vector(seed: float) -> str:
    values = [seed] * EMBEDDING_DIM
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


@dataclass(frozen=True)
class TestDoc:
    id: str
    source_type: str
    title: str
    content: str
    package_name: str | None = None
    action_name: str | None = None
    locale: str = "ko-KR"
    url: str = "https://example.test/a360/scheduler"
    parent_id: str | None = None
    chunk_index: int = 0
    metadata: dict | None = None
    embedding_seed: float = 0.001


def pg_create_schema(conn: psycopg.Connection) -> None:
    ddl = f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS {TEST_TABLE} (
        id            text PRIMARY KEY,
        source_type   text NOT NULL,
        package_name  text,
        action_name   text,
        locale        text,
        title         text NOT NULL,
        url           text,
        content       text NOT NULL,
        metadata      jsonb NOT NULL DEFAULT '{{}}',
        embedding     vector({EMBEDDING_DIM}),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        parent_id     text,
        chunk_index   integer NOT NULL DEFAULT 0,
        content_hash  text
    );
    CREATE INDEX IF NOT EXISTS idx_{TEST_TABLE}_package ON {TEST_TABLE} (package_name);
    CREATE INDEX IF NOT EXISTS idx_{TEST_TABLE}_source ON {TEST_TABLE} (source_type);
    CREATE INDEX IF NOT EXISTS idx_{TEST_TABLE}_parent ON {TEST_TABLE} (parent_id);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def pg_upsert(conn: psycopg.Connection, docs: list[TestDoc]) -> None:
    sql = f"""
    INSERT INTO {TEST_TABLE}
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
        embedding = COALESCE(EXCLUDED.embedding, {TEST_TABLE}.embedding),
        updated_at = now()
    """
    with conn.cursor() as cur:
        for doc in docs:
            cur.execute(
                sql,
                (
                    doc.id,
                    doc.source_type,
                    doc.package_name,
                    doc.action_name,
                    doc.locale,
                    doc.title,
                    doc.url,
                    doc.content,
                    json.dumps(doc.metadata or {"test": True}, ensure_ascii=False),
                    doc.parent_id or doc.id,
                    doc.chunk_index,
                    content_hash(doc.content),
                    vector(doc.embedding_seed),
                ),
            )
    conn.commit()


def pg_delete(conn: psycopg.Connection, doc_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TEST_TABLE} WHERE id = %s", (doc_id,))
    conn.commit()


def pg_snapshot(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, source_type, title, content_hash, parent_id, chunk_index
            FROM {TEST_TABLE}
            ORDER BY id
            """
        )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def pg_column_compare(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('rag_documents', 'rag_documents_test')
            ORDER BY table_name, ordinal_position
            """
        )
        rows = cur.fetchall()
    by_table: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        by_table.setdefault(table_name, []).append(column_name)
    prod = by_table.get("rag_documents", [])
    test = by_table.get("rag_documents_test", [])
    return {
        "rag_documents_columns": len(prod),
        "rag_documents_test_columns": len(test),
        "same_column_names": prod == test,
        "prod_only": [column for column in prod if column not in test],
        "test_only": [column for column in test if column not in prod],
    }


def os_client() -> OpenSearch:
    host = os.getenv("OPENSEARCH_HOST")
    if not host:
        raise SystemExit("OPENSEARCH_HOST is required for this remote test")
    kwargs = {"hosts": [host], "http_compress": True, "timeout": 30}
    if host.startswith("https"):
        kwargs.update(use_ssl=True, verify_certs=True)
    username = os.getenv("OPENSEARCH_USERNAME")
    if username:
        kwargs["http_auth"] = (username, os.getenv("OPENSEARCH_PASSWORD"))
    return OpenSearch(**kwargs)


def os_ensure_index(client: OpenSearch) -> None:
    if client.indices.exists(index=TEST_INDEX):
        return
    client.indices.create(
        index=TEST_INDEX,
        body={
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "filter": {
                        "korean_cjk_bigram": {"type": "cjk_bigram"},
                        "english_stop": {"type": "stop", "stopwords": "_english_"},
                    },
                    "analyzer": {
                        "korean_cjk": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["cjk_width", "lowercase", "korean_cjk_bigram", "english_stop"],
                        }
                    },
                },
            },
            "mappings": {
                "properties": {
                    "id": {"type": "keyword"},
                    "source_type": {"type": "keyword"},
                    "package_name": {"type": "keyword"},
                    "action_name": {"type": "keyword"},
                    "schema_source": {"type": "keyword"},
                    "locale": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "korean_cjk", "fields": {"raw": {"type": "keyword"}}},
                    "url": {"type": "keyword", "index": False},
                    "content": {"type": "text", "analyzer": "korean_cjk"},
                    "parent_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                }
            },
        },
    )


def os_index_docs(client: OpenSearch, docs: list[TestDoc]) -> None:
    for doc in docs:
        client.index(
            index=TEST_INDEX,
            id=doc.id,
            body={
                "id": doc.id,
                "source_type": doc.source_type,
                "package_name": doc.package_name,
                "action_name": doc.action_name,
                "schema_source": (doc.metadata or {}).get("schema_source"),
                "locale": doc.locale,
                "title": doc.title,
                "url": doc.url,
                "content": doc.content,
                "parent_id": doc.parent_id or doc.id,
                "chunk_index": doc.chunk_index,
            },
            refresh=True,
        )


def os_delete(client: OpenSearch, doc_id: str) -> None:
    if client.exists(index=TEST_INDEX, id=doc_id):
        client.delete(index=TEST_INDEX, id=doc_id, refresh=True)


def os_snapshot(client: OpenSearch) -> dict:
    count = client.count(index=TEST_INDEX)["count"]
    search = client.search(
        index=TEST_INDEX,
        body={
            "size": 10,
            "query": {"match_all": {}},
            "sort": [{"id": {"order": "asc"}}],
        },
    )
    return {
        "count": count,
        "ids": [hit["_source"]["id"] for hit in search["hits"]["hits"]],
    }


def os_mapping_compare(client: OpenSearch) -> dict:
    prod_mapping = client.indices.get_mapping(index=os.getenv("OPENSEARCH_INDEX", "rag_documents"))
    test_mapping = client.indices.get_mapping(index=TEST_INDEX)
    prod_index = next(iter(prod_mapping))
    test_index = next(iter(test_mapping))
    prod_fields = sorted(prod_mapping[prod_index]["mappings"]["properties"].keys())
    test_fields = sorted(test_mapping[test_index]["mappings"]["properties"].keys())
    return {
        "prod_index": prod_index,
        "test_index": test_index,
        "prod_fields": len(prod_fields),
        "test_fields": len(test_fields),
        "same_field_names": prod_fields == test_fields,
        "prod_only": [field for field in prod_fields if field not in test_fields],
        "test_only": [field for field in test_fields if field not in prod_fields],
    }


def main() -> None:
    docs = [
        TestDoc(
            id="scheduler-test-action-create",
            source_type="action_schema",
            package_name="SchedulerTest",
            action_name="Create Record",
            title="SchedulerTest - Create Record",
            content="테스트 액션 문서입니다. 주기적 적재가 신규 문서를 추가하는 케이스입니다.",
            metadata={"test": True, "schema_source": "test_fixture", "case": "create"},
            embedding_seed=0.001,
        ),
        TestDoc(
            id="scheduler-test-action-update",
            source_type="action_schema",
            package_name="SchedulerTest",
            action_name="Update Record",
            title="SchedulerTest - Update Record v1",
            content="업데이트 전 테스트 문서입니다.",
            metadata={"test": True, "schema_source": "test_fixture", "case": "update-before"},
            embedding_seed=0.002,
        ),
        TestDoc(
            id="scheduler-test-doc-parent-0",
            source_type="doc_page",
            title="SchedulerTest 문서 chunk 0",
            content="첫 번째 chunk입니다. parent_id와 chunk_index 검증용입니다.",
            parent_id="scheduler-test-doc-parent",
            chunk_index=0,
            metadata={"test": True, "case": "chunk-0"},
            embedding_seed=0.003,
        ),
        TestDoc(
            id="scheduler-test-delete-me",
            source_type="bot_example",
            title="SchedulerTest 삭제 대상",
            content="CRUD delete 검증을 위해 잠시 생성되는 문서입니다.",
            metadata={"test": True, "case": "delete"},
            embedding_seed=0.004,
        ),
    ]
    updated = TestDoc(
        id="scheduler-test-action-update",
        source_type="action_schema",
        package_name="SchedulerTest",
        action_name="Update Record",
        title="SchedulerTest - Update Record v2",
        content="업데이트 후 테스트 문서입니다. content_hash가 변경되어야 합니다.",
        metadata={"test": True, "schema_source": "test_fixture", "case": "update-after"},
        embedding_seed=0.005,
    )

    with psycopg.connect(database_dsn()) as conn:
        pg_create_schema(conn)
        pg_upsert(conn, docs)
        before_update = pg_snapshot(conn)
        pg_upsert(conn, [updated])
        pg_delete(conn, "scheduler-test-delete-me")
        after_crud = pg_snapshot(conn)
        pg_compare = pg_column_compare(conn)

    client = os_client()
    os_ensure_index(client)
    os_index_docs(client, docs)
    os_index_docs(client, [updated])
    os_delete(client, "scheduler-test-delete-me")

    print(
        json.dumps(
            {
                "postgres": {
                    "table": TEST_TABLE,
                    "before_update_count": len(before_update),
                    "after_crud_count": len(after_crud),
                    "schema_compare": pg_compare,
                    "rows": after_crud,
                },
                "opensearch": {
                    "index": TEST_INDEX,
                    **os_snapshot(client),
                    "mapping_compare": os_mapping_compare(client),
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
