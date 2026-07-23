from __future__ import annotations

import random
import threading
from functools import lru_cache
from typing import Any

import psycopg

from app.rag import config
from app.rag.build.merge import build_rag_documents, load_docs
from app.rag.pipeline import _load_source_inputs


_lock = threading.RLock()
_SCHEMA_SOURCE_TYPES = {"action_schema", "package_overview"}


@lru_cache(maxsize=1)
def _documents() -> tuple[dict[str, Any], ...]:
    with _lock:
        packages, docs, bots = _load_source_inputs("all")
        pre_chunk_docs = build_rag_documents(packages, docs, locale="ko", bots=bots, chunk_size=None)
        raw_docs_by_url = {d.get("url"): d for d in load_docs(config.DOCS_JSONL) if d.get("url")}
        rows: list[dict[str, Any]] = []
        for doc in pre_chunk_docs:
            content = doc.get("content") or ""
            if not content.strip():
                continue
            metadata = doc.get("metadata") or {}
            breadcrumbs = metadata.get("breadcrumbs")
            raw = raw_docs_by_url.get(doc.get("url")) if doc.get("source_type") == "doc_page" else None
            rows.append(
                {
                    "id": doc["id"],
                    "source_type": doc.get("source_type"),
                    "title": doc.get("title") or "",
                    "content": content,
                    "package_name": doc.get("package_name"),
                    "action_name": doc.get("action_name"),
                    "parent_menu_id": raw.get("parent_menu_id") if raw else None,
                    "menu_id": raw.get("menu_id") if raw else None,
                    "depth": len(breadcrumbs) if breadcrumbs else None,
                    "path_titles": breadcrumbs,
                    "url": doc.get("url"),
                    "schema_source": metadata.get("schema_source")
                    if doc.get("source_type") in _SCHEMA_SOURCE_TYPES
                    else None,
                }
            )
        if rows:
            return tuple(rows)
        return _documents_from_database()


def _documents_from_database() -> tuple[dict[str, Any], ...]:
    rows_by_parent: dict[str, dict[str, Any]] = {}
    with psycopg.connect(config.database_dsn(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, parent_id, source_type, package_name, action_name, title, url,
                       content, metadata, chunk_index
                FROM rag_documents
                WHERE content IS NOT NULL AND btrim(content) <> ''
                ORDER BY parent_id, chunk_index NULLS LAST, id
                """
            )
            for (
                doc_id,
                parent_id,
                source_type,
                package_name,
                action_name,
                title,
                url,
                content,
                metadata,
                chunk_index,
            ) in cur.fetchall():
                row_id = parent_id or doc_id
                metadata = metadata or {}
                row = rows_by_parent.setdefault(
                    row_id,
                    {
                        "id": row_id,
                        "source_type": source_type,
                        "title": title or "",
                        "content": "",
                        "package_name": package_name,
                        "action_name": action_name,
                        "parent_menu_id": metadata.get("parent_menu_id"),
                        "menu_id": metadata.get("menu_id"),
                        "depth": len(metadata.get("breadcrumbs") or []) or None,
                        "path_titles": metadata.get("breadcrumbs"),
                        "url": url,
                        "schema_source": metadata.get("schema_source")
                        if source_type in _SCHEMA_SOURCE_TYPES
                        else None,
                        "_chunks": [],
                    },
                )
                row["_chunks"].append((chunk_index if chunk_index is not None else 0, content))

    documents: list[dict[str, Any]] = []
    for row in rows_by_parent.values():
        chunks = [content for _, content in sorted(row.pop("_chunks"), key=lambda item: item[0])]
        row["content"] = "\n\n".join(chunks)
        documents.append(row)
    return tuple(documents)


def clear_cache() -> None:
    _documents.cache_clear()


def capabilities() -> dict[str, Any]:
    docs = _documents()
    by_type: dict[str, int] = {}
    for doc in docs:
        source_type = doc.get("source_type") or "unknown"
        by_type[source_type] = by_type.get(source_type, 0) + 1
    return {"count": len(docs), "source_types": by_type}


def _matches(doc: dict[str, Any], query: str, source_type: str | None, schema_source: str | None) -> bool:
    if source_type and doc.get("source_type") != source_type:
        return False
    if schema_source and doc.get("schema_source") != schema_source:
        return False
    if query:
        needle = query.lower()
        return needle in (doc.get("title") or "").lower() or needle in (doc.get("content") or "").lower()
    return True


def _preview(doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, "preview": (doc.get("content") or "")[:200]}


def search(
    query: str = "",
    source_type: str | None = None,
    limit: int = 100,
    schema_source: str | None = None,
) -> list[dict[str, Any]]:
    rows = [
        _preview(doc)
        for doc in _documents()
        if _matches(doc, query, source_type, schema_source)
    ]
    rows.sort(key=lambda d: (d.get("package_name") or "~", str(d.get("path_titles") or ""), d.get("title") or ""))
    return rows[:limit]


def random_sample(
    source_type: str | None = None,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
    min_content_length: int = 0,
    schema_source: str | None = None,
) -> list[dict[str, Any]]:
    exclude = set(exclude_ids or [])
    rows = [
        dict(doc)
        for doc in _documents()
        if doc["id"] not in exclude
        and len(doc.get("content") or "") >= min_content_length
        and _matches(doc, "", source_type, schema_source)
    ]
    random.shuffle(rows)
    return rows[:limit]


def get_by_id(doc_id: str) -> dict[str, Any] | None:
    return next((dict(doc) for doc in _documents() if doc["id"] == doc_id), None)
