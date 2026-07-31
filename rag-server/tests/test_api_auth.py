"""RAG ingest API service-token boundary (RPA-291)."""

from fastapi.testclient import TestClient

from app import ingest_jobs
from app.main import app


def test_health_is_public_but_ingest_status_requires_a_token(monkeypatch):
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "test-rag-token")
    monkeypatch.setattr(ingest_jobs, "list_jobs", lambda **_: [])

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/rag/ingest/status").status_code == 401
        assert client.get("/rag/ingest/status", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
        response = client.get("/rag/ingest/status", headers={"Authorization": "Bearer test-rag-token"})

    assert response.status_code == 200
    assert response.json()["running"] is False


def test_sensitive_routes_fail_closed_when_service_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)

    with TestClient(app) as client:
        response = client.get("/rag/ingest/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "RAG ingest authentication is not configured"
