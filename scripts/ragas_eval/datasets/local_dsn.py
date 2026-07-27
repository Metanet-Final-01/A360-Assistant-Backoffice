"""로컬 전용 Postgres(source_documents 테이블) 연결 문자열 — 단일 진실.

`build_source_documents.py`(적재)와 `ops-server/backend/app/eval/ragas_eval/
source_documents.py`(조회) 둘 다 이 함수를 쓴다 — 예전엔 두 곳에 똑같은 함수를
복붙해뒀는데, 그중 하나만 고치면 조용히 어긋나는 문제가 있었다(CodeRabbit #42
지적). DATABASE_PASSWORD가 없으면 하드코딩된 기본값으로 조용히 넘어가지 않고
바로 실패시킨다 — 로컬 전용이라도 비밀번호를 소스에 박아두지 않는다.
"""

import os


def local_dsn() -> str:
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5432"
    name = os.getenv("DATABASE_NAME") or "a360"
    user = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD")
    if not password:
        raise RuntimeError(
            "DATABASE_PASSWORD 환경변수가 없습니다 — 로컬 source_documents DB "
            "비밀번호를 .env에 설정하세요(하드코딩된 기본값을 쓰지 않습니다)."
        )
    return f"host={host} port={port} dbname={name} user={user} password={password}"
