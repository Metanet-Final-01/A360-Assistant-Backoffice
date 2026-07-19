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

os.environ["RAG_DATABASE_URL"] = ""  # config의 `if url:` 폴백 유도 (dotenv는 override 안 함)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag import config  # noqa: E402  (RAG_DATABASE_URL 고정 후에 임포트해야 함)

dsn = config.database_dsn()
if "127.0.0.1" not in dsn and "localhost" not in dsn:
    sys.exit(f"[중단] 로컬 러너인데 실효 DSN이 로컬이 아닙니다: {dsn}")

sys.argv = ["pipeline", "ingest", *sys.argv[1:]]
from app.rag.pipeline import main  # noqa: E402

main()
