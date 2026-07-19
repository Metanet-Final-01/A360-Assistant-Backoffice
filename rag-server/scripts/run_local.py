# -*- coding: utf-8 -*-
"""로컬 전용 파이프라인 러너 — 어떤 서브커맨드든 원격(네온) 접속을 원천 차단하고 실행한다.

.env의 RAG_DATABASE_URL(네온)·OBSERVABILITY_DATABASE_URL(네온 관측)을 config 임포트 전에
빈 값으로 고정해(load_dotenv는 기존 env를 덮지 않음) 모든 DB 폴백이 로컬(127.0.0.1 도커)로만
가게 하고, 실효 DSN이 로컬이 아니면 즉시 중단한다. .env 파일은 수정하지 않는다.
(배경: 2026-07-18 네온 --clean 사고 — 회의록/2026-07-18-네온-clean-사고-보고.md)

사용: .venv\\Scripts\\python.exe scripts\\run_local.py <subcommand> [args...]
예:   .venv\\Scripts\\python.exe scripts\\run_local.py build-v2 --dump-dir ..\\khub-dump --enrich
      .venv\\Scripts\\python.exe scripts\\run_local.py ingest --clean --skip-opensearch
"""
import os
import sys

os.environ["RAG_DATABASE_URL"] = ""
os.environ["OBSERVABILITY_DATABASE_URL"] = ""
# OpenSearch도 .env는 Bonsai(팀 공유)를 보므로 로컬 컨테이너로 강제 — 공유 색인 오염 방지
os.environ["OPENSEARCH_HOST"] = "http://127.0.0.1:9200"
os.environ["OPENSEARCH_USERNAME"] = ""
os.environ["OPENSEARCH_PASSWORD"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag import config  # noqa: E402  (env 고정 후 임포트)

dsn = config.database_dsn()
if "127.0.0.1" not in dsn and "localhost" not in dsn:
    sys.exit(f"[중단] 로컬 러너인데 실효 DSN이 로컬이 아닙니다: {dsn}")
print(f"[run_local] DB 폴백 확인: 로컬 ({dsn.split('password=')[0].strip()})")

sys.argv = ["pipeline", *sys.argv[1:]]
from app.rag.pipeline import main  # noqa: E402

main()
