"""토큰(cl100k_base, text-embedding-3-small 실제 토크나이저) 기준으로 chunk_size를
잡는 후보 테이블 빌더 — build_candidate.py(글자 기준)와 거의 동일하지만
length_function만 tiktoken 카운터로 바꿨다. 테이블명은 rag_documents_eval_tok{N}_ov{overlap}.
"""
import argparse
import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_RAG_SERVER_ROOT = Path(r"c:/Users/KDH/Documents/VisualStudio Code/A360-Assistant/A360-Assistant-Ops/rag-server")
sys.path.insert(0, str(_RAG_SERVER_ROOT))
os.chdir(_RAG_SERVER_ROOT)

import psycopg  # noqa: E402
import tiktoken  # noqa: E402
from psycopg import sql  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from app.rag import config as rag_config  # noqa: E402
from app.rag.build.chunk import _SEPARATORS_BY_STRATEGY, _normalize  # noqa: E402
from app.rag.build.merge import build_rag_documents  # noqa: E402
from app.rag.pipeline import _load_source_inputs  # noqa: E402
from app.rag.retrieval.embed import embed_texts  # noqa: E402

_ENCODING = tiktoken.get_encoding("cl100k_base")

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


def _token_len(text: str) -> int:
    return len(_ENCODING.encode(text))


def chunk_text_by_tokens(text: str, token_size: int, overlap_tokens: int, strategy: str = "prose") -> list[str]:
    normalized = _normalize(text)
    if not normalized:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=token_size,
        chunk_overlap=overlap_tokens,
        separators=_SEPARATORS_BY_STRATEGY[strategy],
        length_function=_token_len,
    )
    return splitter.split_text(normalized)


def local_dsn() -> str:
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5433"
    name = os.getenv("DATABASE_NAME") or "a360"
    user = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
    return f"host={host} port={port} dbname={name} user={user} password={password}"


def table_name(token_size: int, overlap_tokens: int) -> str:
    return f"rag_documents_eval_tok{token_size}_ov{overlap_tokens}"


def build_candidate_token(token_size: int, overlap_tokens: int, overwrite: bool = False) -> str:
    table = table_name(token_size, overlap_tokens)
    dsn = local_dsn()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (table,))
            exists = cur.fetchone()[0] is not None
            row_count = 0
            if exists:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                row_count = cur.fetchone()[0]

        if exists and row_count > 0 and not overwrite:
            print(f"{table} 이미 있음({row_count}건) — 건너뜀 (--overwrite로 강제 재생성)")
            return table

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
        token_len = _token_len(content)
        chunks = (
            chunk_text_by_tokens(content, token_size, overlap_tokens, strategy=strategy)
            if token_len > token_size
            else [content]
        )
        parent_id = d["id"]
        for i, chunk in enumerate(chunks):
            rows.append({
                "id": f"{parent_id}#{i}", "parent_id": parent_id, "chunk_index": i,
                "title": d.get("title") or "", "content": chunk,
            })

    print(f"{table}: {len(rows)}개 청크(토큰 기준 {token_size}), 임베딩 생성 중 "
          f"({rag_config.EMBEDDING_PROVIDER}/{rag_config.EMBEDDING_MODEL})...")

    INSERT_BATCH_SIZE = 500
    insert_sql = sql.SQL(
        "INSERT INTO {table} (id, parent_id, chunk_index, title, content, embedding) "
        "VALUES (%s, %s, %s, %s, %s, %s::vector) "
        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding"
    ).format(table=sql.Identifier(table))

    with psycopg.connect(dsn) as conn:
        for batch_start in range(0, len(rows), INSERT_BATCH_SIZE):
            batch_rows = rows[batch_start: batch_start + INSERT_BATCH_SIZE]
            batch_embeddings = embed_texts([r["content"] for r in batch_rows])
            with conn.cursor() as cur:
                for row, vec in zip(batch_rows, batch_embeddings):
                    vector_literal = "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
                    cur.execute(insert_sql, (row["id"], row["parent_id"], row["chunk_index"],
                                              row["title"], row["content"], vector_literal))
            conn.commit()
            done = min(batch_start + INSERT_BATCH_SIZE, len(rows))
            print(f"  {done}/{len(rows)} (커밋됨)")

    print(f"{table}: {len(rows)}개 적재 완료")
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-size", type=int, required=True)
    parser.add_argument("--overlap-tokens", type=int, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build_candidate_token(args.token_size, args.overlap_tokens, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
