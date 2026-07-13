"""rag-server DB 연결 분리 테스트 (RPA-143, 백엔드 RPA-132 미러).

RAG_DATABASE_URL이 있으면 전용 공유 DB(Neon)로, 없으면 기존 DATABASE_*로 폴백한다.
rag-server는 raw psycopg라 libpq URL만 받으므로 SQLAlchemy 접미사(+psycopg)는 벗긴다.
"""

import app.rag.config as config


def test_dsn_falls_back_to_database_star_when_unset(monkeypatch):
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_HOST", "db.example")
    monkeypatch.setenv("DATABASE_NAME", "a360")
    dsn = config.database_dsn()
    assert dsn.startswith("host=")  # 키워드 형식(=폴백)
    assert "host=db.example" in dsn and "dbname=a360" in dsn


def test_dsn_prefers_rag_database_url(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://u:p@ep-x-pooler.neon.tech/neondb?sslmode=require")
    monkeypatch.setenv("DATABASE_HOST", "localhost")  # 무시돼야 함
    assert config.database_dsn() == "postgresql://u:p@ep-x-pooler.neon.tech/neondb?sslmode=require"


def test_dsn_strips_sqlalchemy_driver_suffix(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require")
    assert config.database_dsn() == "postgresql://u:p@ep-x.neon.tech/db?sslmode=require"
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql+psycopg2://u:p@h/db")
    assert config.database_dsn() == "postgresql://u:p@h/db"


def test_empty_rag_database_url_falls_back(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_HOST", "fallback.host")
    dsn = config.database_dsn()
    assert dsn.startswith("host=") and "fallback.host" in dsn
