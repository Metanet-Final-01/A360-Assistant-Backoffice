"""rag-server DB 연결 분리 테스트 (RPA-143 → RPA-262).

RAG_DATABASE_URL만 사용한다 — 미설정/빈값이면 RagDatabaseConfigurationError로 기동을 거부해
RAG 코퍼스가 앱 DB에 조용히 섞이는 걸 막는다(백엔드 RPA-260과 동일 계약, DATABASE_* 폴백 제거).
rag-server는 raw psycopg라 libpq URL만 받으므로 SQLAlchemy 접미사(+psycopg)는 벗긴다.
"""

import pytest

import app.rag.config as config


def test_dsn_raises_when_unset(monkeypatch):
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_HOST", "db.example")  # 폴백 없음 — DATABASE_*는 무시돼야 함
    with pytest.raises(config.RagDatabaseConfigurationError):
        config.database_dsn()


def test_dsn_raises_when_empty_or_blank(monkeypatch):
    monkeypatch.setenv("DATABASE_HOST", "fallback.host")  # 폴백 없음
    for blank in ("", "   "):
        monkeypatch.setenv("RAG_DATABASE_URL", blank)
        with pytest.raises(config.RagDatabaseConfigurationError):
            config.database_dsn()


def test_dsn_prefers_rag_database_url(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://u:p@ep-x-pooler.neon.tech/neondb?sslmode=require")
    monkeypatch.setenv("DATABASE_HOST", "localhost")  # 무시돼야 함
    assert config.database_dsn() == "postgresql://u:p@ep-x-pooler.neon.tech/neondb?sslmode=require"


def test_dsn_strips_sqlalchemy_driver_suffix(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require")
    assert config.database_dsn() == "postgresql://u:p@ep-x.neon.tech/db?sslmode=require"
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql+psycopg2://u:p@h/db")
    assert config.database_dsn() == "postgresql://u:p@h/db"


def test_dsn_passes_through_libpq_keyword_format(monkeypatch):
    # 로컬 러너(run_local*.py)가 주입하는 키워드 포맷 — 드라이버 접미사 정규식에 안 걸려 그대로 통과.
    kw = "host=127.0.0.1 port=5432 dbname=a360 user=a360_admin password=a360_local_password"
    monkeypatch.setenv("RAG_DATABASE_URL", kw)
    assert config.database_dsn() == kw
