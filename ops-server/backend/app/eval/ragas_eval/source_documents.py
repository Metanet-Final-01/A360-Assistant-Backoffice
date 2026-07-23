from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
import requests


logger = logging.getLogger(__name__)

_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _PARENTS[5] if len(_PARENTS) > 5 else _PARENTS[-1]
_SCRIPTS_DATASETS_DIR = _REPO_ROOT / "scripts" / "ragas_eval" / "datasets"
if str(_SCRIPTS_DATASETS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DATASETS_DIR))

try:
    from local_dsn import local_dsn  # type: ignore  # noqa: E402
except ModuleNotFoundError:

    def local_dsn() -> str:
        host = os.getenv("DATABASE_HOST") or "127.0.0.1"
        port = os.getenv("DATABASE_PORT") or "5432"
        database_name = os.getenv("DATABASE_NAME") or "a360"
        username = os.getenv("DATABASE_USERNAME") or "a360_admin"
        password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
        return f"postgresql://{username}:{password}@{host}:{port}/{database_name}"


_SCHEMA_SOURCE_TYPES = {"action_schema", "package_overview"}
_RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8200").rstrip("/")
_RAG_SERVICE_TOKEN = os.getenv("RAG_SERVICE_TOKEN", "")


class SourceDocumentsUnavailableError(RuntimeError):
    pass


def _rag_headers() -> dict[str, str] | None:
    return {"Authorization": f"Bearer {_RAG_SERVICE_TOKEN}"} if _RAG_SERVICE_TOKEN else None


def _rag_get(path: str, params: dict | None = None) -> object:
    response = requests.get(
        f"{_RAG_SERVER_URL}{path}",
        params={key: value for key, value in (params or {}).items() if value not in (None, "")},
        headers=_rag_headers(),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _fallback_message() -> str:
    return (
        "source_documents 테이블에 연결할 수 없고 RAG Server 원본 문서 fallback도 실패했습니다. "
        "RAG 데이터 적재를 먼저 실행했는지 확인하세요."
    )


def _fallback_search(
    query: str = "",
    source_type: str | None = None,
    limit: int = 100,
    schema_source: str | None = None,
) -> list[dict]:
    try:
        result = _rag_get(
            "/rag/source-documents",
            {"q": query, "source_type": source_type, "limit": limit, "schema_source": schema_source},
        )
        return result if isinstance(result, list) else []
    except requests.RequestException as exc:
        raise SourceDocumentsUnavailableError(_fallback_message()) from exc


def _fallback_random_sample(
    source_type: str | None = None,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
    min_content_length: int = 0,
    schema_source: str | None = None,
) -> list[dict]:
    try:
        result = _rag_get(
            "/rag/source-documents/random",
            {
                "source_type": source_type,
                "limit": limit,
                "exclude_ids": ",".join(exclude_ids or []),
                "min_content_length": min_content_length,
                "schema_source": schema_source,
            },
        )
        return result if isinstance(result, list) else []
    except requests.RequestException as exc:
        raise SourceDocumentsUnavailableError(_fallback_message()) from exc


def _fallback_get_by_id(doc_id: str) -> dict | None:
    try:
        result = _rag_get(f"/rag/source-documents/{doc_id}")
        return result if isinstance(result, dict) else None
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise SourceDocumentsUnavailableError(_fallback_message()) from exc
    except requests.RequestException as exc:
        raise SourceDocumentsUnavailableError(_fallback_message()) from exc


def _fetch_schema_sources(parent_ids: list[str]) -> dict[str, str]:
    if not parent_ids:
        return {}
    try:
        response = requests.get(
            f"{_RAG_SERVER_URL}/rag/schema-sources",
            params={"parent_ids": ",".join(parent_ids)},
            headers=_rag_headers(),
            timeout=5,
        )
        response.raise_for_status()
        return response.json().get("schema_sources", {})
    except requests.RequestException as exc:
        logger.warning("schema_source lookup failed; continuing without it: %s", exc)
        return {}


def _attach_schema_source(rows: list[dict]) -> None:
    target_ids = [row["id"] for row in rows if row.get("source_type") in _SCHEMA_SOURCE_TYPES]
    schema_sources = _fetch_schema_sources(target_ids)
    for row in rows:
        if row.get("source_type") in _SCHEMA_SOURCE_TYPES:
            row["schema_source"] = row.get("schema_source") or schema_sources.get(row["id"])
        else:
            row["schema_source"] = None


def get_source_types(doc_ids: list[str]) -> dict[str, str]:
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
        docs = _fallback_search(limit=500)
        return {doc["id"]: doc["source_type"] for doc in docs if doc["id"] in set(doc_ids)}


def enrich_cases_with_document_meta(cases: list[dict]) -> None:
    doc_ids = [case["reference_doc_ids"][0] for case in cases if case.get("reference_doc_ids")]
    source_types = get_source_types(doc_ids)
    schema_source_target_ids = [
        doc_id for doc_id in doc_ids if source_types.get(doc_id) in _SCHEMA_SOURCE_TYPES
    ]
    schema_sources = _fetch_schema_sources(schema_source_target_ids)
    for case in cases:
        doc_id = case["reference_doc_ids"][0] if case.get("reference_doc_ids") else None
        case["source_type"] = source_types.get(doc_id) if doc_id else None
        case["schema_source"] = schema_sources.get(doc_id) if doc_id else None


def search(
    query: str = "",
    source_type: str | None = None,
    limit: int = 100,
    schema_source: str | None = None,
) -> list[dict]:
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
                columns = [description.name for description in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        return _fallback_search(query, source_type=source_type, limit=limit, schema_source=schema_source)

    _attach_schema_source(rows)
    if schema_source is not None:
        rows = [row for row in rows if row.get("schema_source") == schema_source]
    return rows[:limit]


def random_sample(
    source_type: str | None = None,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
    min_content_length: int = 0,
    schema_source: str | None = None,
) -> list[dict]:
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
                columns = [description.name for description in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        return _fallback_random_sample(
            source_type=source_type,
            limit=limit,
            exclude_ids=exclude_ids,
            min_content_length=min_content_length,
            schema_source=schema_source,
        )

    _attach_schema_source(rows)
    if schema_source is not None:
        rows = [row for row in rows if row.get("schema_source") == schema_source]
    return rows[:limit]


def get_by_id(doc_id: str) -> dict | None:
    try:
        with psycopg.connect(local_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM source_documents WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [description.name for description in cur.description]
                doc = dict(zip(columns, row))
                _attach_schema_source([doc])
                return doc
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        return _fallback_get_by_id(doc_id)
