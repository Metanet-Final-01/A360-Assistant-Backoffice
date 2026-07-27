from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from app.rag import config
from app.rag.store import db, opensearch_client


def fetch_documents(conn, *, batch_size: int):
    last_id = ""
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, package_name, action_name, locale, title, url, content,
                       metadata, parent_id, chunk_index
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
        for row in rows:
            metadata = row[8] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            yield {
                "id": row[0],
                "source_type": row[1],
                "package_name": row[2],
                "action_name": row[3],
                "locale": row[4],
                "title": row[5],
                "url": row[6],
                "content": row[7],
                "metadata": metadata,
                "parent_id": row[9] or row[0],
                "chunk_index": row[10] or 0,
            }
        last_id = rows[-1][0]


def db_summary(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE embedding IS NULL) FROM rag_documents")
        total, missing_embeddings = cur.fetchone()
        cur.execute(
            """
            SELECT source_type, COUNT(*)
            FROM rag_documents
            GROUP BY source_type
            ORDER BY source_type
            """
        )
        by_source = dict(cur.fetchall())
    return {"db_total": total, "db_missing_embeddings": missing_embeddings, "db_by_source_type": by_source}


def index_count(client) -> int | None:
    if not client.indices.exists(index=config.OPENSEARCH_INDEX):
        return None
    return int(client.count(index=config.OPENSEARCH_INDEX)["count"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex OpenSearch from Neon/pgvector rag_documents.")
    parser.add_argument("--apply", action="store_true", help="Actually write to OpenSearch. Omit for dry-run.")
    parser.add_argument(
        "--delete-index",
        action="store_true",
        help="Delete and recreate the target index before bulk indexing. Recommended for full sync.",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")

    conn = db.connect()
    client = opensearch_client.connect()
    try:
        db.ensure_schema(conn)
        before_count = index_count(client)
        summary = {
            "mode": "apply" if args.apply else "dry-run",
            "connection_source": "RAG_DATABASE_URL",  # 폴백 제거(RPA-262) — 이제 유일 소스
            "database": db_summary(conn),
            "opensearch_index": config.OPENSEARCH_INDEX,
            "opensearch_count_before": before_count,
            "delete_index": args.delete_index,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if not args.apply:
            print("Dry-run only. Re-run with --apply --delete-index for a full OpenSearch sync.")
            return 0
        if not args.delete_index:
            print("Proceeding without deleting the index; stale OpenSearch docs may remain.", file=sys.stderr)

        started = time.perf_counter()
        if args.delete_index:
            opensearch_client.delete_index(client)
        opensearch_client.ensure_index(client)

        indexed = 0
        batch: list[dict[str, Any]] = []
        for doc in fetch_documents(conn, batch_size=args.batch_size):
            batch.append(doc)
            if len(batch) >= args.batch_size:
                indexed += opensearch_client.bulk_index(client, batch)
                print(f"indexed {indexed}")
                batch.clear()
        if batch:
            indexed += opensearch_client.bulk_index(client, batch)
            print(f"indexed {indexed}")

        print(json.dumps({
            "status": "ok",
            "indexed": indexed,
            "opensearch_count_after": index_count(client),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
