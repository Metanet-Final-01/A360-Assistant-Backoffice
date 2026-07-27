"""기동 시 필수 DSN 검증 (RPA-262, Qodo 리뷰 반영).

RAG_DATABASE_URL이 없으면 FastAPI 기동(lifespan)에서 바로 실패해야 한다 — 잘못된 배포가
"정상 기동·헬스 통과"로 보이지 않게 하는 fail-fast. database_dsn()은 URL 존재만 확인하므로
(접속하지 않음) DB 일시 다운으로 크래시하지는 않는다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag.config import RagDatabaseConfigurationError


def test_startup_fails_without_rag_database_url(monkeypatch):
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    with pytest.raises(RagDatabaseConfigurationError):
        with TestClient(app):  # 컨텍스트 진입 = lifespan startup 실행
            pass


def test_startup_ok_and_health_when_url_set(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://u:p@localhost:5432/a360_rag")
    with TestClient(app) as client:  # startup 통과
        assert client.get("/health").json() == {"status": "ok"}
