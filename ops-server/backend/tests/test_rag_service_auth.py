from unittest.mock import Mock, patch

import pytest

from app.eval.ragas_eval.validation_log import record_attempt
from app.scheduler.scheduler import trigger_rag_ingest


def test_validation_log_passes_the_rag_service_token(monkeypatch):
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "service-token")

    with patch("app.eval.ragas_eval.validation_log.requests.post") as post:
        post.return_value = Mock()
        record_attempt(doc_id="doc-1", doc_title=None, question=None, outcome="success")

    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer service-token"}


def test_validation_log_skips_the_protected_call_without_a_token(monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)

    with patch("app.eval.ragas_eval.validation_log.requests.post") as post:
        record_attempt(doc_id="doc-1", doc_title=None, question=None, outcome="success")

    post.assert_not_called()


def test_manual_ingest_passes_the_rag_service_token(monkeypatch):
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "service-token")
    response = Mock()
    response.json.return_value = {"status": "started"}

    with patch("app.scheduler.scheduler.httpx.post", return_value=response) as post:
        assert trigger_rag_ingest() == {"status": "started"}

    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer service-token"}


def test_manual_ingest_fails_closed_without_a_token(monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="RAG_SERVICE_TOKEN is required"):
        trigger_rag_ingest()
