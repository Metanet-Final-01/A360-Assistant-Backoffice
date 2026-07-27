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

from app.rag import source_documents as rag_source_documents  # noqa: E402

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

    # 적재된 rag_documents에서 원본 문서 풀을 만든다(청크는 parent_id로 재조립됨).
    # 예전에는 v1 로컬 산출물(packages.json·docs.jsonl)로 메모리에서 조립했는데, v2 웹크롤
    # 전용화로 그 산출물이 더 이상 생성되지 않고 근거 함수(pipeline._load_source_inputs)도
    # 제거됐다. rag-server의 근거 문서 풀과 같은 소스를 쓰도록 통일한다.
    print("적재된 rag_documents에서 원본 문서 풀 로드 중...")
    _COLUMNS = (
        "id", "source_type", "title", "content", "package_name", "action_name",
        "parent_menu_id", "menu_id", "depth", "path_titles", "url",
    )
    rows = [
        {key: doc.get(key) for key in _COLUMNS}
        for doc in rag_source_documents._documents_from_database()
        if (doc.get("content") or "").strip()
    ]

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
