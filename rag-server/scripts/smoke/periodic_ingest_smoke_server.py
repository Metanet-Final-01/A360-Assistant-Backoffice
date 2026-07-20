from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from opensearchpy import OpenSearch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT, ".env"))

TABLE = os.getenv("RAG_PERIODIC_TEST_TABLE", "rag_documents_periodic_test")
INDEX = os.getenv("RAG_PERIODIC_TEST_INDEX", "rag_documents_periodic_test")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

app = FastAPI(title="A360 Periodic Ingest Smoke Server")


def require_test_identifier(value: str, *, kind: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise RuntimeError(f"{kind} must be a safe SQL/OpenSearch identifier: {value!r}")
    if value == "rag_documents" or "test" not in value:
        raise RuntimeError(f"{kind} must point to a dedicated test resource: {value!r}")
    return value


TABLE = require_test_identifier(TABLE, kind="RAG_PERIODIC_TEST_TABLE")
INDEX = require_test_identifier(INDEX, kind="RAG_PERIODIC_TEST_INDEX")


def database_dsn() -> str:
    url = os.getenv("RAG_DATABASE_URL")
    if not url:
        raise RuntimeError("RAG_DATABASE_URL is required")
    return url.replace("postgresql+psycopg://", "postgresql://")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def embedding(seed: float = 0.0001) -> str:
    return "[" + ",".join(f"{seed:.7f}" for _ in range(EMBEDDING_DIM)) + "]"


def ensure_pg_schema(conn: psycopg.Connection) -> None:
    ddl = f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS {TABLE} (
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
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_package ON {TABLE} (package_name);
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_source ON {TABLE} (source_type);
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_parent ON {TABLE} (parent_id);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def upsert_pg(option: int, clean: bool, run_at: str) -> dict:
    doc_id = "periodic-smoke-latest"
    content = f"Periodic ingest smoke test. option={option} clean={clean} run_at={run_at}"
    metadata = {"test": True, "kind": "periodic_ingest_smoke", "option": option, "clean": clean, "run_at": run_at}
    with psycopg.connect(database_dsn()) as conn:
        ensure_pg_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {TABLE}
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
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                RETURNING id, content_hash, updated_at
                """,
                (
                    doc_id,
                    "scheduler_test",
                    "PeriodicSmoke",
                    "UpsertSmoke",
                    "ko-KR",
                    "Periodic ingest smoke latest",
                    "https://example.test/a360/periodic-smoke",
                    content,
                    json.dumps(metadata, ensure_ascii=False),
                    doc_id,
                    0,
                    content_hash(content),
                    embedding(),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "content_hash": row[1], "updated_at": row[2].isoformat()}


def os_client() -> OpenSearch:
    host = os.getenv("OPENSEARCH_HOST")
    if not host:
        raise RuntimeError("OPENSEARCH_HOST is required")
    kwargs = {"hosts": [host], "http_compress": True, "timeout": 30}
    if host.startswith("https"):
        kwargs.update(use_ssl=True, verify_certs=True)
    if os.getenv("OPENSEARCH_USERNAME"):
        kwargs["http_auth"] = (os.getenv("OPENSEARCH_USERNAME"), os.getenv("OPENSEARCH_PASSWORD"))
    return OpenSearch(**kwargs)


def ensure_os_index(client: OpenSearch) -> None:
    if client.indices.exists(index=INDEX):
        return
    client.indices.create(
        index=INDEX,
        body={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "id": {"type": "keyword"},
                    "source_type": {"type": "keyword"},
                    "package_name": {"type": "keyword"},
                    "action_name": {"type": "keyword"},
                    "schema_source": {"type": "keyword"},
                    "locale": {"type": "keyword"},
                    "title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                    "url": {"type": "keyword", "index": False},
                    "content": {"type": "text"},
                    "parent_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                }
            },
        },
    )


def upsert_os(option: int, clean: bool, run_at: str) -> dict:
    doc_id = "periodic-smoke-latest"
    client = os_client()
    ensure_os_index(client)
    client.index(
        index=INDEX,
        id=doc_id,
        body={
            "id": doc_id,
            "source_type": "scheduler_test",
            "package_name": "PeriodicSmoke",
            "action_name": "UpsertSmoke",
            "schema_source": "smoke_fixture",
            "locale": "ko-KR",
            "title": "Periodic ingest smoke latest",
            "url": "https://example.test/a360/periodic-smoke",
            "content": f"Periodic ingest smoke test. option={option} clean={clean} run_at={run_at}",
            "parent_id": doc_id,
            "chunk_index": 0,
        },
        refresh=True,
    )
    return {"index": INDEX, "id": doc_id, "count": client.count(index=INDEX)["count"]}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "table": TABLE, "index": INDEX}


@app.post("/rag/ingest")
def smoke_ingest(option: int = 3, clean: bool = False) -> dict:
    run_at = datetime.now(timezone.utc).isoformat()
    pg = upsert_pg(option, clean, run_at)
    os_result = upsert_os(option, clean, run_at)
    return {"status": "smoke-upserted", "run_at": run_at, "postgres": pg, "opensearch": os_result}
