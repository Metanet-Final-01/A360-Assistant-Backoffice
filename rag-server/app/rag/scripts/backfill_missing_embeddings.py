from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

from app.rag import config
from app.rag.retrieval.embed import embed_texts
from app.rag.store import db


LOCK_KEY = 2026071601
VECTOR_TYPE_RE = re.compile(r"vector\((\d+)\)")


@dataclass(frozen=True)
class MissingDocument:
    id: str
    source_type: str
    package_name: str | None
    action_name: str | None
    title: str
    content: str


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in values) + "]"


def acquire_lock(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        return bool(cur.fetchone()[0])


def release_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def database_info(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()")
        database, host, port = cur.fetchone()
        cur.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = 'rag_documents'
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
        row = cur.fetchone()
    type_name = row[0] if row else None
    match = VECTOR_TYPE_RE.fullmatch(type_name or "")
    return {
        "database": database,
        "host": host,
        "port": port,
        "embedding_column_type": type_name,
        "embedding_column_dim": int(match.group(1)) if match else None,
    }


def count_missing(conn, where_sql: str, params: list[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM rag_documents WHERE embedding IS NULL {where_sql}", params)
        return int(cur.fetchone()[0])


def summarize_missing(conn, where_sql: str, params: list[Any], sample_limit: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT source_type, COALESCE(package_name, ''), COUNT(*)
            FROM rag_documents
            WHERE embedding IS NULL {where_sql}
            GROUP BY source_type, COALESCE(package_name, '')
            ORDER BY COUNT(*) DESC, source_type, COALESCE(package_name, '')
            """,
            params,
        )
        groups = [
            {"source_type": source_type, "package_name": package_name or None, "count": count}
            for source_type, package_name, count in cur.fetchall()
        ]
        cur.execute(
            f"""
            SELECT id, source_type, package_name, action_name, title, updated_at
            FROM rag_documents
            WHERE embedding IS NULL {where_sql}
            ORDER BY updated_at, id
            LIMIT %s
            """,
            [*params, sample_limit],
        )
        samples = [
            {
                "id": row[0],
                "source_type": row[1],
                "package_name": row[2],
                "action_name": row[3],
                "title": row[4],
                "updated_at": row[5].isoformat() if row[5] else None,
            }
            for row in cur.fetchall()
        ]
    by_source = Counter()
    for group in groups:
        by_source[group["source_type"]] += group["count"]
    return {
        "total_missing": sum(by_source.values()),
        "by_source_type": dict(sorted(by_source.items())),
        "groups": groups,
        "samples": samples,
    }


def fetch_batch(conn, where_sql: str, params: list[Any], limit: int) -> list[MissingDocument]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, source_type, package_name, action_name, title, content
            FROM rag_documents
            WHERE embedding IS NULL {where_sql}
            ORDER BY updated_at, id
            LIMIT %s
            """,
            [*params, limit],
        )
        return [MissingDocument(*row) for row in cur.fetchall()]


def update_embeddings(conn, docs: list[MissingDocument], embeddings: list[list[float]]) -> None:
    with conn.cursor() as cur:
        for doc, embedding in zip(docs, embeddings, strict=True):
            if len(embedding) != config.EMBEDDING_DIM:
                raise RuntimeError(
                    f"Embedding dimension mismatch for {doc.id}: "
                    f"got {len(embedding)}, expected {config.EMBEDDING_DIM}"
                )
            cur.execute(
                """
                UPDATE rag_documents
                SET embedding = %s::vector
                WHERE id = %s AND embedding IS NULL
                """,
                (vector_literal(embedding), doc.id),
            )
    conn.commit()


def build_where(args: argparse.Namespace) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if args.source_type:
        clauses.append("source_type = %s")
        params.append(args.source_type)
    if args.updated_at:
        clauses.append("updated_at >= %s::timestamptz")
        params.append(args.updated_at)
    if args.package_name:
        clauses.append("package_name = %s")
        params.append(args.package_name)
    return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for rag_documents rows where embedding IS NULL."
    )
    parser.add_argument("--apply", action="store_true", help="Write embeddings. Omit for dry-run summary only.")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, help="Maximum rows to backfill in this run.")
    parser.add_argument("--source-type", help="Optional source_type filter.")
    parser.add_argument("--package-name", help="Optional exact package_name filter.")
    parser.add_argument("--updated-at", help="Optional lower bound, e.g. 2026-07-15T13:36:00Z.")
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")

    where_sql, params = build_where(args)
    conn = db.connect()
    try:
        db.ensure_schema(conn)
        db_info = database_info(conn)
        summary = summarize_missing(conn, where_sql, params, args.sample_limit)
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "connection_source": "RAG_DATABASE_URL",  # 폴백 제거(RPA-262) — 이제 유일 소스
            **db_info,
            "embedding_provider": config.EMBEDDING_PROVIDER,
            "embedding_model": config.EMBEDDING_MODEL,
            "embedding_dim": config.EMBEDDING_DIM,
            **summary,
        }, ensure_ascii=False, indent=2))

        if not args.apply:
            print("Dry-run only. Re-run with --apply to update embedding IS NULL rows.")
            return 0

        column_dim = db_info.get("embedding_column_dim")
        if column_dim and column_dim != config.EMBEDDING_DIM:
            raise SystemExit(
                f"Embedding dimension mismatch before API call: "
                f"DB column is vector({column_dim}), config.EMBEDDING_DIM={config.EMBEDDING_DIM}"
            )

        if not acquire_lock(conn):
            raise SystemExit("Another embedding backfill appears to be running; advisory lock was not acquired.")
        try:
            remaining_limit = args.limit
            updated = 0
            started = time.perf_counter()
            while True:
                batch_limit = args.batch_size
                if remaining_limit is not None:
                    if remaining_limit <= 0:
                        break
                    batch_limit = min(batch_limit, remaining_limit)
                docs = fetch_batch(conn, where_sql, params, batch_limit)
                if not docs:
                    break
                embeddings = embed_texts([doc.content for doc in docs])
                update_embeddings(conn, docs, embeddings)
                updated += len(docs)
                if remaining_limit is not None:
                    remaining_limit -= len(docs)
                print(f"backfilled {updated} rows")
            print(json.dumps({
                "status": "ok",
                "updated": updated,
                "remaining_missing": count_missing(conn, where_sql, params),
                "duration_seconds": round(time.perf_counter() - started, 3),
            }, ensure_ascii=False, indent=2))
        finally:
            release_lock(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
