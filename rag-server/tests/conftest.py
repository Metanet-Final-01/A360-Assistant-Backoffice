"""rag-server 테스트 공통 픽스처.

RPA-262로 database_dsn()이 RAG_DATABASE_URL을 필수로 요구한다(DATABASE_* 폴백 제거). 두 위험을
한 번에 막는다:
  - 개발자 .env에 RAG_DATABASE_URL(공유 Neon)이 있으면 config의 load_dotenv가 로드해 테스트가
    공유 DB를 때릴 수 있다.
  - CI엔 .env가 없어 database_dsn()이 아예 raise한다(DB를 건드리는 기존 테스트가 전부 깨진다).

두 경우 모두 테스트 전용 로컬 URL을 명시 주입해 해결한다 — 백엔드 RPA-260 conftest와 동일 방식.
포트 :1은 의도적으로 접속 불가 — database_dsn()이 값을 "반환"(raise 안 함)하게 하되, 실제로
커넥션을 여는 테스트는 공유 인프라가 아니라 로컬로 fail-fast하게 한다(모킹 누락 방어).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_rag_database_url(monkeypatch):
    # ⚠️ delenv가 아니라 명시 URL — app.rag.config가 import 시점에 load_dotenv()를 부르므로
    #    지워도 .env에서 되살아난다. RAG_DATABASE_URL 미설정 동작을 검증하는 테스트
    #    (test_config·test_startup_dsn_check)는 이 픽스처 뒤에 자기 delenv/setenv로 덮는다.
    monkeypatch.setenv(
        "RAG_DATABASE_URL",
        "postgresql://a360_admin:a360_local_password@127.0.0.1:1/a360_rag",
    )
