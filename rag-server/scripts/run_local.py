# -*- coding: utf-8 -*-
"""로컬 전용 파이프라인 러너 — 어떤 서브커맨드든 원격(네온) 접속을 원천 차단하고 실행한다.

.env의 RAG_DATABASE_URL(네온)·OBSERVABILITY_DATABASE_URL(네온 관측)을 config 임포트 전에
빈 값으로 고정해(load_dotenv는 기존 env를 덮지 않음) 모든 DB 폴백이 로컬(127.0.0.1 도커)로만
가게 하고, 실효 DSN이 로컬이 아니면 즉시 중단한다. .env 파일은 수정하지 않는다.
(배경: 2026-07-18 네온 --clean 사고 — 회의록/2026-07-18-네온-clean-사고-보고.md)

사용: .venv\\Scripts\\python.exe scripts\\run_local.py <subcommand> [args...]
예:   .venv\\Scripts\\python.exe scripts\\run_local.py build-llm --dump-dir ..\\khub-dump --model gpt-5-mini
      .venv\\Scripts\\python.exe scripts\\run_local.py ingest --clean --skip-opensearch
"""
import os
import sys

# .env의 RAG_DATABASE_URL(네온)을 무력화하고, config 로드 뒤 DATABASE_*로 명시적 로컬 DSN을
# 만들어 다시 주입한다 — 폴백 제거(RPA-262)에 맞춘 방식. OBSERVABILITY는 빈 값이면 관측 기록을
# 건너뛰므로(앱/RAG DB로 안 씀) 빈 채로 둔다 = 로컬은 사용량 기록 없음.
os.environ["RAG_DATABASE_URL"] = ""
os.environ["OBSERVABILITY_DATABASE_URL"] = ""
# OpenSearch도 .env는 Bonsai(팀 공유)를 보므로 로컬 컨테이너로 강제 — 공유 색인 오염 방지
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
print(f"[run_local] 로컬 DB 확인 ({dsn.split('password=')[0].strip()})")

sys.argv = ["pipeline", *sys.argv[1:]]
from app.rag.pipeline import main  # noqa: E402

main()
