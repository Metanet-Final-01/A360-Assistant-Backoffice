# -*- coding: utf-8 -*-
"""로컬 도커 전용 ingest 러너 — .env의 RAG_DATABASE_URL(네온)을 프로세스에서 무력화한다.

배경(2026-07-18 사고): rag-server/.env가 네온을 겨냥하고 있어 로컬 의도의 ingest가 공유 DB로
간 사고가 있었다. 이 러너는 config 임포트 전에 RAG_DATABASE_URL을 빈 값으로 고정해
(load_dotenv는 기존 env를 덮지 않음) DATABASE_* 폴백(127.0.0.1 도커)으로만 가게 하고,
실행 전 실효 DSN이 로컬인지 재검증한다. .env 파일은 수정하지 않는다.

사용: .venv\\Scripts\\python.exe scripts\\run_local_ingest.py [ingest 인자들...]
예:   .venv\\Scripts\\python.exe scripts\\run_local_ingest.py --clean --skip-opensearch
"""
import os
import sys

# .env의 RAG_DATABASE_URL(네온)을 무력화한다(dotenv는 이미 설정된 키를 안 덮음).
# 예전엔 이 빈 값으로 DATABASE_* 폴백을 유도했으나, 폴백이 제거돼(RPA-262) config 로드 뒤
# .env의 DATABASE_*(로컬 앱 DB)로 명시적 로컬 DSN을 만들어 RAG_DATABASE_URL에 주입한다.
os.environ["RAG_DATABASE_URL"] = ""
# OpenSearch도 .env는 Bonsai(팀 공유)를 보므로 로컬로 강제 — run_local.py와 동일한 차단
# (--skip-opensearch를 빠뜨려도 공유 인덱스에 쓰지 않게)
os.environ["OPENSEARCH_HOST"] = "http://127.0.0.1:9200"
os.environ["OPENSEARCH_USERNAME"] = ""
os.environ["OPENSEARCH_PASSWORD"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag import config  # noqa: E402  (env 고정 후 임포트 → load_dotenv가 DATABASE_* 채움)

# 명시적 로컬 DSN 주입 — 예전 폴백과 동일한 실효 DSN(같은 기본값·같은 .env DATABASE_* 반영).
os.environ["RAG_DATABASE_URL"] = (
    f"host={os.getenv('DATABASE_HOST') or '127.0.0.1'} "
    f"port={os.getenv('DATABASE_PORT') or '5432'} "
    f"dbname={os.getenv('DATABASE_NAME') or 'a360'} "
    f"user={os.getenv('DATABASE_USERNAME') or 'a360_admin'} "
    f"password={os.getenv('DATABASE_PASSWORD') or 'a360_local_password'}"
)

dsn = config.database_dsn()
if "127.0.0.1" not in dsn and "localhost" not in dsn:
    sys.exit(f"[중단] 로컬 러너인데 실효 DSN이 로컬이 아닙니다: {dsn}")

sys.argv = ["pipeline", "ingest", *sys.argv[1:]]
from app.rag.pipeline import main  # noqa: E402

main()
