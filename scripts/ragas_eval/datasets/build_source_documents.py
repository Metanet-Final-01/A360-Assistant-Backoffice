"""골드셋 작성용 원본 문서(pre-chunk) 스냅샷을 로컬 Postgres에 적재한다.

chunk_size와 무관한 원본 텍스트를 저장한다 — 골드셋의 reference_contexts는 이 테이블의
content에서 발췌해야 어느 chunk_size 후보에서든 재사용 가능하다(청크 id는 후보마다
달라지지만 원본 문서는 안 바뀌므로).

doc_page는 docs.jsonl의 menu_id/parent_menu_id/breadcrumbs를 그대로 들고 온다 — 페이지
계층(사이트 메뉴) 정보다. 페이지 내부 heading 구조(structure.sections[].heading)는
이번 스코프에서 버린다(합의됨) — 필요해지면 별도 컬럼으로 나중에 추가.
action_schema/package_overview는 JAR/에이전트 기반이라 menu_id 계열 계층이 없다(NULL) —
대신 package_name/action_name이 계층 역할을 한다.
"""

import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_SCRIPT_DIR = Path(__file__).resolve().parent
_RAG_SERVER_ROOT = _SCRIPT_DIR.parents[2] / "rag-server"
# local_dsn.py는 이 스크립트와 같은 디렉터리에 있다 — os.chdir로 CWD를 바꾸기
# 전에 절대경로로 넣어야 어디서 실행하든(-m 실행 포함) 찾는다.
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_RAG_SERVER_ROOT))
os.chdir(_RAG_SERVER_ROOT)

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.rag import config  # noqa: E402
from app.rag.build.merge import build_rag_documents, load_docs  # noqa: E402
from app.rag.pipeline import _load_source_inputs  # noqa: E402

from local_dsn import local_dsn  # noqa: E402


_DDL = """
CREATE TABLE IF NOT EXISTS source_documents (
    id text PRIMARY KEY,
    source_type text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    package_name text,
    action_name text,
    parent_menu_id text,
    menu_id text,
    depth integer,
    path_titles text[],
    url text
)
"""


def build() -> int:
    dsn = local_dsn()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()

    print("pre-chunk 원본 로드 중 (에이전트 재실행 없음)...")
    packages, docs, bots = _load_source_inputs("all")
    pre_chunk_docs = build_rag_documents(packages, docs, locale="ko", bots=bots, chunk_size=None)

    # doc_page의 menu_id/parent_menu_id는 build_rag_documents 결과에 안 남아있어서
    # (merge.py가 breadcrumbs만 metadata에 남김), 원본 docs.jsonl에서 url로 다시 조회.
    raw_docs_by_url = {d.get("url"): d for d in load_docs(config.DOCS_JSONL) if d.get("url")}

    rows = []
    for d in pre_chunk_docs:
        content = d.get("content") or ""
        if not content.strip():
            continue
        source_type = d.get("source_type")
        breadcrumbs = (d.get("metadata") or {}).get("breadcrumbs")
        menu_id = parent_menu_id = None
        depth = None
        if source_type == "doc_page":
            raw = raw_docs_by_url.get(d.get("url"))
            if raw is not None:
                menu_id = raw.get("menu_id")
                parent_menu_id = raw.get("parent_menu_id")
            if breadcrumbs:
                depth = len(breadcrumbs)
        rows.append(
            {
                "id": d["id"],
                "source_type": source_type,
                "title": d.get("title") or "",
                "content": content,
                "package_name": d.get("package_name"),
                "action_name": d.get("action_name"),
                "parent_menu_id": parent_menu_id,
                "menu_id": menu_id,
                "depth": depth,
                "path_titles": breadcrumbs,
                "url": d.get("url"),
            }
        )

    insert_sql = sql.SQL(
        "INSERT INTO source_documents "
        "(id, source_type, title, content, package_name, action_name, "
        " parent_menu_id, menu_id, depth, path_titles, url) "
        "VALUES (%(id)s, %(source_type)s, %(title)s, %(content)s, %(package_name)s, %(action_name)s, "
        " %(parent_menu_id)s, %(menu_id)s, %(depth)s, %(path_titles)s, %(url)s) "
        "ON CONFLICT (id) DO UPDATE SET "
        " source_type = EXCLUDED.source_type, title = EXCLUDED.title, "
        " content = EXCLUDED.content, package_name = EXCLUDED.package_name, "
        " action_name = EXCLUDED.action_name, parent_menu_id = EXCLUDED.parent_menu_id, "
        " menu_id = EXCLUDED.menu_id, depth = EXCLUDED.depth, "
        " path_titles = EXCLUDED.path_titles, url = EXCLUDED.url"
    )

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()

    print(f"source_documents: {len(rows)}개 적재 완료 (로컬 DB)")
    return len(rows)


if __name__ == "__main__":
    build()
