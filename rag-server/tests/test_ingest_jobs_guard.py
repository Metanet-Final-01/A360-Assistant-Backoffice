from app import ingest_jobs


def test_restore_runs_when_local_artifact_missing(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)

    calls = []

    class Artifacts:
        @staticmethod
        def restore_latest_if_missing():
            calls.append("restore")
            return {"ok": True, "restored": True, "key": "rag-ingest-artifacts/latest/rag-server-data.tar.gz"}

    monkeypatch.setitem(ingest_jobs.sys.modules, "app.artifacts", Artifacts)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="eventbridge-sqs")

    assert calls == ["restore"]
    assert result["restored"] is True


def test_restore_skips_scheduled_ingest_when_s3_artifact_is_absent(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setenv("RAG_ARTIFACT_RESTORE_WAIT_SECONDS", "0")
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)

    class Artifacts:
        @staticmethod
        def restore_latest_if_missing():
            return {"ok": False, "restored": False, "reason": "artifact-not-found"}

    monkeypatch.setitem(ingest_jobs.sys.modules, "app.artifacts", Artifacts)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="eventbridge-sqs")

    assert result["restored"] is False
    assert result["reason"] == "artifact-not-found"
    assert result["skip"] is True


def test_restore_retries_scheduled_ingest_restore_errors(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setenv("RAG_ARTIFACT_RESTORE_WAIT_SECONDS", "2")
    monkeypatch.setenv("RAG_ARTIFACT_RESTORE_POLL_SECONDS", "1")
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)
    monkeypatch.setattr(ingest_jobs.time, "sleep", lambda _seconds: None)

    calls = []

    class Artifacts:
        @staticmethod
        def restore_latest_if_missing():
            calls.append("restore")
            if len(calls) == 1:
                raise RuntimeError("transient")
            return {"ok": True, "restored": True, "key": "rag-ingest-artifacts/latest/rag-server-data.tar.gz"}

    monkeypatch.setitem(ingest_jobs.sys.modules, "app.artifacts", Artifacts)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="eventbridge-sqs")

    assert calls == ["restore", "restore"]
    assert result["restored"] is True


def test_restore_allows_manual_first_ingest_when_s3_artifact_is_absent(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)

    class Artifacts:
        @staticmethod
        def restore_latest_if_missing():
            return {"ok": False, "restored": False, "reason": "artifact-not-found"}

    monkeypatch.setitem(ingest_jobs.sys.modules, "app.artifacts", Artifacts)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="ops")

    assert result["restored"] is False
    assert result["reason"] == "artifact-not-found"


def test_restore_skips_explicit_clean_rebuild(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=True, requested_by="ops")

    assert result["reason"] == "explicit-rebuild"


def test_restore_skips_explicit_override(monkeypatch):
    monkeypatch.setenv("RAG_ALLOW_COLD_REBUILD", "true")
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: False)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="ops")

    assert result["reason"] == "explicit-rebuild"


def test_restore_skips_when_local_artifact_exists(monkeypatch):
    monkeypatch.delenv("RAG_ALLOW_COLD_REBUILD", raising=False)
    monkeypatch.setattr(ingest_jobs, "_local_rag_documents_exists", lambda: True)

    result = ingest_jobs._restore_artifacts_before_ingest(clean=False, requested_by="ops")

    assert result["reason"] == "local-present"
