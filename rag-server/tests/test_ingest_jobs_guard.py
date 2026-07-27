import pytest

from app import ingest_jobs


def test_guard_blocks_cold_rebuild_when_s3_latest_exists(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)
    monkeypatch.setattr(ingest_jobs, "_s3_latest_artifact_exists", lambda: True)

    with pytest.raises(RuntimeError, match="Refusing RAG ingest cold rebuild"):
        ingest_jobs._guard_against_cold_rebuild(clean=False, requested_by="eventbridge-sqs")


def test_guard_allows_explicit_clean_rebuild(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)
    monkeypatch.setattr(ingest_jobs, "_s3_latest_artifact_exists", lambda: True)

    ingest_jobs._guard_against_cold_rebuild(clean=True, requested_by="ops")


def test_guard_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("RAG_ALLOW_COLD_REBUILD", "true")
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)
    monkeypatch.setattr(ingest_jobs, "_s3_latest_artifact_exists", lambda: True)

    ingest_jobs._guard_against_cold_rebuild(clean=False, requested_by="ops")


def test_guard_allows_first_ingest_when_no_s3_latest(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)
    monkeypatch.setattr(ingest_jobs, "_s3_latest_artifact_exists", lambda: False)

    ingest_jobs._guard_against_cold_rebuild(clean=False, requested_by="ops")
