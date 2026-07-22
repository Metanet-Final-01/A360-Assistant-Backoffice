"""Chunk-size 실험용 로컬 테스트 테이블 빌더.

pre-chunk 원본(이미 에이전트가 분류해둔 docs.jsonl/bots.jsonl/packages.json을 병합만
함 — chunk_size=None 경로라 문서 파싱 에이전트를 다시 안 돌림)을 후보 chunk_size/
overlap으로 재청킹해서 로컬 pgvector에 테스트 전용 테이블로 적재한다.

운영 rag_documents/원격 Neon과는 완전히 분리한다 — RAG_DATABASE_URL(원격)은 의도적으로
무시하고, DATABASE_*(로컬 docker-compose 기본값)로만 연결한다.
"""

import argparse
import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_RAG_SERVER_ROOT = Path(__file__).resolve().parents[3] / "rag-server"
sys.path.insert(0, str(_RAG_SERVER_ROOT))
# rag-server의 config.py(DATA_DIR 등)가 상대경로라 이 스크립트를 어디서 실행하든
# rag-server를 기준으로 데이터를 찾도록 cwd를 맞춘다 — 안 맞추면 조용히 빈 리스트만
# 반환해서(예외 없음) "0개 청크"로 실패한 걸 눈치채기 어렵다(실제로 겪은 버그).
os.chdir(_RAG_SERVER_ROOT)

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.rag import config as rag_config  # noqa: E402
from app.rag.build.chunk import chunk_text  # noqa: E402
from app.rag.build.merge import build_rag_documents  # noqa: E402
from app.rag.pipeline import _load_source_inputs  # noqa: E402
from app.rag.retrieval.embed import embed_texts  # noqa: E402

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS {table} (
    id text PRIMARY KEY,
    parent_id text NOT NULL,
    chunk_index integer NOT NULL,
    title text NOT NULL DEFAULT '',
    content text NOT NULL,
    embedding vector({dim})
)
"""


def local_dsn() -> str:
    """로컬 전용 DSN. RAG_DATABASE_URL(원격 Neon)은 의도적으로 안 본다."""
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5432"
    name = os.getenv("DATABASE_NAME") or "a360"
    user = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
    return f"host={host} port={port} dbname={name} user={user} password={password}"


def table_name(chunk_size: int, overlap: int) -> str:
    return f"rag_documents_eval_cs{chunk_size}_ov{overlap}"


def build_candidate(chunk_size: int, overlap: int, overwrite: bool = False) -> str:
    table = table_name(chunk_size, overlap)
    dsn = local_dsn()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (table,))
            exists = cur.fetchone()[0] is not None
            row_count = 0
            if exists:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                row_count = cur.fetchone()[0]

        # row_count==0은 이전 실행이 DDL만 만들고 중단된 빈 껍데기다(존재 여부만 보면
        # 이걸 완성된 걸로 착각해서 건너뛴다 — 실제로 겪은 버그). 이 경우엔 있어도
        # 없는 것처럼 다시 만든다.
        if exists and row_count > 0 and not overwrite:
            print(f"{table} 이미 있음({row_count}건) — 건너뜀 (--overwrite로 강제 재생성)")
            return table
        if exists and row_count == 0:
            print(f"{table} 존재하지만 0건(이전 실행이 중단된 빈 껍데기) — 다시 만듭니다")

        with conn.cursor() as cur:
            if exists:
                cur.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table)))
            cur.execute(
                sql.SQL(_DDL).format(
                    table=sql.Identifier(table),
                    dim=sql.Literal(rag_config.EMBEDDING_DIM),
                )
            )
        conn.commit()

    print("pre-chunk 원본 로드 중 (에이전트 재실행 없음)...")
    packages, docs, bots = _load_source_inputs("all")
    pre_chunk_docs = build_rag_documents(packages, docs, locale="ko", bots=bots, chunk_size=None)

    rows: list[dict] = []
    for d in pre_chunk_docs:
        content = d.get("content") or ""
        if not content.strip():
            continue
        strategy = (
            "structured"
            if d.get("source_type") in ("action_schema", "package_overview", "bot_example")
            else "prose"
        )
        chunks = (
            chunk_text(content, chunk_size, overlap, strategy=strategy)
            if len(content) > chunk_size
            else [content]
        )
        parent_id = d["id"]
        for i, chunk in enumerate(chunks):
            rows.append(
                {
                    "id": f"{parent_id}#{i}",
                    "parent_id": parent_id,
                    "chunk_index": i,
                    "title": d.get("title") or "",
                    "content": chunk,
                }
            )

    print(
        f"{table}: {len(rows)}개 청크, 임베딩 생성 중 "
        f"({rag_config.EMBEDDING_PROVIDER}/{rag_config.EMBEDDING_MODEL})..."
    )

    # 예전엔 전부 임베딩한 뒤 한 번에 INSERT했다 — 중간에 죽으면(타임아웃, MemoryError 등)
    # 이미 낸 임베딩 비용이 통째로 날아가고 빈 테이블만 남는 문제가 실제로 반복됐다
    # (cs300_ov30, cs600_ov60에서 겪음). INSERT_BATCH_SIZE 단위로 임베딩→즉시 INSERT+commit을
    # 반복해서, 중단되어도 그때까지 커밋된 배치는 남게 한다.
    INSERT_BATCH_SIZE = 500
    insert_sql = sql.SQL(
        "INSERT INTO {table} (id, parent_id, chunk_index, title, content, embedding) "
        "VALUES (%s, %s, %s, %s, %s, %s::vector) "
        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding"
    ).format(table=sql.Identifier(table))

    with psycopg.connect(dsn) as conn:
        for batch_start in range(0, len(rows), INSERT_BATCH_SIZE):
            batch_rows = rows[batch_start : batch_start + INSERT_BATCH_SIZE]
            batch_embeddings = embed_texts([r["content"] for r in batch_rows])

            with conn.cursor() as cur:
                for row, vec in zip(batch_rows, batch_embeddings):
                    vector_literal = "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
                    cur.execute(
                        insert_sql,
                        (row["id"], row["parent_id"], row["chunk_index"], row["title"], row["content"], vector_literal),
                    )
            conn.commit()

            done = min(batch_start + INSERT_BATCH_SIZE, len(rows))
            print(f"  {done}/{len(rows)} (커밋됨 — 여기까지는 중단돼도 안 날아감)")

    print(f"{table}: {len(rows)}개 적재 완료 (로컬 DB — RAG_DATABASE_URL 원격은 안 건드림)")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="chunk_size 실험용 로컬 테스트 테이블 빌더")
    parser.add_argument("--chunk-size", type=int, required=True)
    parser.add_argument("--overlap", type=int, required=True)
    parser.add_argument("--overwrite", action="store_true", help="이미 있는 테이블을 지우고 다시 만든다")
    args = parser.parse_args()

    if args.chunk_size <= 0 or args.overlap < 0 or args.overlap >= args.chunk_size:
        sys.exit(f"chunk_size/overlap 값이 올바르지 않습니다: {args.chunk_size}/{args.overlap}")

    build_candidate(args.chunk_size, args.overlap, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
