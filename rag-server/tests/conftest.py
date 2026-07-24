"""rag-server 테스트 공통 픽스처 — 테스트가 공유 인프라를 절대 때리지 않게 격리한다.

개발자 .env에 공유 크레덴셜(Neon RAG DB·Neon 관측 DB·Bonsai OpenSearch)이 있으면 config가
import 시점에 load_dotenv로 로드한다. 그러면 모킹을 빠뜨린 테스트가:
  - 공유 RAG/관측 DB에 테스트 쓰레기를 쓰거나,
  - 공유 Bonsai 색인을 오염시키거나,
  - (RPA-262 폴백 제거로) RAG_DATABASE_URL 미설정 시 database_dsn()이 raise한다(CI엔 .env 없음).

세 벡터를 전부 로컬로 고정한다 — 백엔드 RPA-260/RPA-90 conftest와 동일 방어.
"""

import pytest

from app.rag import config


@pytest.fixture(autouse=True)
def _isolate_shared_infra(monkeypatch):
    # RAG store: database_dsn()이 참조 시점에 os.getenv로 읽으므로 env로 주입한다. 포트 :1은
    # 접속 불가 — 값은 "반환"(raise 안 함)하되 실제 커넥션은 공유가 아니라 로컬로 fail-fast.
    # RAG_DATABASE_URL 미설정 동작을 검증하는 테스트(test_config·test_startup)는 이 픽스처 뒤에
    # 자기 delenv/setenv로 덮는다.
    monkeypatch.setenv(
        "RAG_DATABASE_URL",
        "postgresql://a360_admin:a360_local_password@127.0.0.1:1/a360_rag",
    )
    # 관측 DB·OpenSearch는 config 상수(import 시점 고정)라 env 삭제/주입으로는 못 막는다 →
    # 속성 자체를 덮는다. OBSERVABILITY 빈 값 → _observability_dsn()이 None → record_usage가
    # 기록을 skip(공유 관측 DB 미접근). OPENSEARCH는 로컬로 저하(공유 Bonsai 색인 보호).
    monkeypatch.setattr(config, "OBSERVABILITY_DATABASE_URL", "")
    monkeypatch.setattr(config, "OPENSEARCH_HOST", "http://localhost:9200")
    monkeypatch.setattr(config, "OPENSEARCH_USERNAME", "")
    monkeypatch.setattr(config, "OPENSEARCH_PASSWORD", "")
