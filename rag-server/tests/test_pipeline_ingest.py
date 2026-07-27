import json
import sys
import types
from types import SimpleNamespace

import pytest

from app.rag import pipeline
from app.rag.store import db, opensearch_client


class _Conn:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _write_docs(tmp_path, monkeypatch, docs):
    jsonl = tmp_path / "rag_documents.jsonl"
    jsonl.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs), encoding="utf-8")
    monkeypatch.setattr(pipeline.config, "RAG_DOCUMENTS_JSONL", jsonl)
    return jsonl


def _patch_store(
    monkeypatch,
    *,
    hashes=None,
    orphans=(),
    overlap=None,
    embedding=(0.1, 0.2),
):
    """cmd_ingest가 건드리는 저장소 계층을 전부 가짜로 바꾼다 (DB/OpenSearch 접속 없음).

    반환 dict의 "calls"는 호출 순서를 그대로 기록한다 — "고아 삭제가 색인보다 먼저"처럼
    순서 자체가 계약인 항목을 검증하기 위해서다.
    """
    captured = {"calls": []}

    # 접속 대상 게이트를 로컬로 고정 — .env의 원격 DSN 때문에 --clean 테스트가 sys.exit되지 않도록.
    monkeypatch.setattr(pipeline.config, "database_dsn", lambda: "host=127.0.0.1 dbname=t")
    monkeypatch.setattr(pipeline.config, "OPENSEARCH_HOST", "http://127.0.0.1:9200")

    monkeypatch.setattr(db, "connect", lambda: (captured["calls"].append("connect"), _Conn())[1])
    monkeypatch.setattr(db, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(db, "get_content_hashes", lambda conn, ids: dict(hashes or {}))
    monkeypatch.setattr(
        db,
        "corpus_overlap_stats",
        lambda conn, parent_ids: dict(
            overlap or {"total_rows": 0, "total_parents": 0, "unseen_parents": 0}
        ),
    )

    def fake_clear_all(conn, commit=True):
        captured["calls"].append("pg_clear_all")
        captured["clear_all_commit"] = commit

    def fake_delete_orphans(conn, keep_ids, parent_ids):
        captured["calls"].append("pg_delete_orphans")
        captured["keep_ids"] = keep_ids
        captured["parent_ids"] = parent_ids
        return list(orphans)

    def fake_upsert(conn, documents, embeddings):
        captured["calls"].append("pg_upsert")
        captured["upsert_ids"] = [d["id"] for d in documents]
        captured["embedding_slots"] = embeddings
        return len(documents)

    monkeypatch.setattr(db, "clear_all", fake_clear_all)
    monkeypatch.setattr(db, "delete_orphans", fake_delete_orphans)
    monkeypatch.setattr(db, "upsert_documents", fake_upsert)

    fake_embed_module = types.ModuleType("app.rag.retrieval.embed")

    def fake_embed_texts(contents, on_progress=None):
        captured["embedded_contents"] = contents
        return [list(embedding) for _ in contents]

    fake_embed_module.embed_texts = fake_embed_texts
    monkeypatch.setitem(sys.modules, "app.rag.retrieval.embed", fake_embed_module)

    # opensearch_client는 `from .store import opensearch_client`로 로드되므로 sys.modules를
    # 갈아끼워도 패키지 속성이 우선한다 — 모듈 함수를 직접 교체한다.
    def fake_delete_index(client):
        captured["calls"].append("os_delete_index")

    def fake_bulk_index(client, documents):
        captured["calls"].append("os_bulk_index")
        captured["opensearch_ids"] = [d["id"] for d in documents]
        return len(documents)

    def fake_delete_by_ids(client, ids):
        captured["calls"].append("os_delete_by_ids")
        captured["os_deleted_ids"] = list(ids)
        return len(ids)

    monkeypatch.setattr(opensearch_client, "connect", lambda: object())
    monkeypatch.setattr(opensearch_client, "ensure_index", lambda client: None)
    monkeypatch.setattr(opensearch_client, "delete_index", fake_delete_index)
    monkeypatch.setattr(opensearch_client, "bulk_index", fake_bulk_index)
    monkeypatch.setattr(opensearch_client, "delete_by_ids", fake_delete_by_ids)

    return captured


def _args(**kwargs):
    base = {"clean": False, "skip_embedding": False, "skip_opensearch": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


DOCS = [
    {
        "id": "doc-unchanged",
        "source_type": "doc_page",
        "title": "Updated title",
        "content": "same content",
        "metadata": {"fresh": True},
    },
    {
        "id": "doc-new",
        "source_type": "doc_page",
        "title": "New",
        "content": "new content",
        "metadata": {},
    },
]


def test_ingest_skips_only_embedding_for_unchanged_content(monkeypatch, tmp_path):
    _write_docs(tmp_path, monkeypatch, DOCS)
    captured = _patch_store(
        monkeypatch, hashes={"doc-unchanged": db.content_hash("same content")}
    )

    pipeline.cmd_ingest(_args())

    assert captured["embedded_contents"] == ["new content"]
    assert captured["upsert_ids"] == ["doc-unchanged", "doc-new"]
    assert captured["opensearch_ids"] == ["doc-unchanged", "doc-new"]
    assert captured["embedding_slots"][0] is None
    assert captured["embedding_slots"][1] == [0.1, 0.2]


def test_ingest_deletes_orphans_in_both_stores_before_indexing(monkeypatch, tmp_path):
    """비-clean 적재는 고아를 pgvector에서 지우고 같은 id를 색인에서도 지운다.
    삭제가 bulk_index보다 먼저 도는 것까지 검증한다 — 색인이 실패하면 뒤에 있는 삭제는
    아예 실행되지 않아 PG에 없는 행이 BM25에만 남기 때문이다."""
    _write_docs(tmp_path, monkeypatch, DOCS)
    captured = _patch_store(monkeypatch, orphans=["old-chunk-3", "old-chunk-4"])

    pipeline.cmd_ingest(_args())

    assert captured["keep_ids"] == ["doc-unchanged", "doc-new"]
    # parent_id가 없는 문서는 자기 id가 parent다 (merge 규약)
    assert captured["parent_ids"] == ["doc-new", "doc-unchanged"]
    assert captured["os_deleted_ids"] == ["old-chunk-3", "old-chunk-4"]
    assert captured["calls"].index("os_delete_by_ids") < captured["calls"].index("os_bulk_index")


def test_clean_with_empty_documents_aborts_without_touching_either_store(monkeypatch, tmp_path):
    """R1 회귀 방지: 빈 산출물에 --clean이 들어오면 어느 저장소도 건드리지 않고 중단한다.
    (PG TRUNCATE만 건너뛰고 OpenSearch 색인은 지워지던 발산 경로)"""
    _write_docs(tmp_path, monkeypatch, [])
    captured = _patch_store(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        pipeline.cmd_ingest(_args(clean=True))

    assert "0건" in str(exc.value)
    assert captured["calls"] == []  # connect조차 하지 않는다


def test_empty_documents_without_clean_warns_and_indexes_nothing(monkeypatch, tmp_path, capsys):
    _write_docs(tmp_path, monkeypatch, [])
    captured = _patch_store(monkeypatch)

    pipeline.cmd_ingest(_args())

    out = capsys.readouterr().out
    assert "비어 있습니다" in out
    assert "pg_clear_all" not in captured["calls"]
    assert "pg_delete_orphans" not in captured["calls"]


def test_warns_when_build_supersedes_corpus_with_new_id_scheme(monkeypatch, tmp_path, capsys):
    """M3: v1 코퍼스(8,355행) 위에 산식이 바뀐 v2 산출물을 비-clean 적재하면 겹치는 parent가
    거의 없어 delete_orphans가 옛 행을 하나도 못 지운다 — 조용히 지나가지 않고 경고해야 한다."""
    _write_docs(tmp_path, monkeypatch, DOCS)
    captured = _patch_store(
        monkeypatch,
        overlap={"total_rows": 8355, "total_parents": 130, "unseen_parents": 130},
    )

    pipeline.cmd_ingest(_args())

    out = capsys.readouterr().out
    assert "--clean" in out
    assert "8355행" in out
    # 경고일 뿐 — 적재 자체는 계속되고, 삭제 범위를 임의로 넓히지 않는다
    assert captured["upsert_ids"] == ["doc-unchanged", "doc-new"]


def test_no_supersede_warning_on_normal_partial_reingest(monkeypatch, tmp_path, capsys):
    """부분 재적재(--source docs 등)는 산식이 같아 빌드 parent가 대부분 DB에 이미 있다 —
    이 경우엔 경고가 뜨면 안 된다(경고 피로 방지)."""
    _write_docs(tmp_path, monkeypatch, DOCS)
    _patch_store(
        monkeypatch,
        overlap={"total_rows": 20, "total_parents": 3, "unseen_parents": 1},
    )

    pipeline.cmd_ingest(_args())

    assert "`--clean`이 필요할 수 있습니다" not in capsys.readouterr().out


def test_skip_opensearch_warns_that_orphans_survive_in_index(monkeypatch, tmp_path, capsys):
    _write_docs(tmp_path, monkeypatch, DOCS)
    captured = _patch_store(monkeypatch, orphans=["old-chunk-9"])

    pipeline.cmd_ingest(_args(skip_opensearch=True))

    out = capsys.readouterr().out
    assert "--skip-opensearch" in out
    assert "old-chunk-9" in out
    assert "os_bulk_index" not in captured["calls"]


# ── 저장소 계층 단위 테스트 (여전히 DB/OpenSearch 접속 없음) ────────────────────


class _RecordingCursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sink.append(("execute", sql, params))

    def executemany(self, sql, rows):
        self.sink.append(("executemany", sql, list(rows)))


class _RecordingConn:
    def __init__(self):
        self.sink = []
        self.commits = 0

    def cursor(self):
        return _RecordingCursor(self.sink)

    def commit(self):
        self.commits += 1


def test_delete_by_ids_counts_already_missing_documents(monkeypatch):
    """R5: 404는 '이미 목표 상태'라 실패가 아니다 — 반환값에 성공으로 세지 않으면
    '고아 삭제 0개'라는 거짓 로그가 남는다."""
    seen = {}

    def fake_bulk(client, actions, **kwargs):
        seen.update(kwargs)
        return 1, [
            {"delete": {"_id": "gone", "status": 404}},
            {"delete": {"_id": "boom", "status": 500}},
        ]

    monkeypatch.setattr(opensearch_client, "bulk", fake_bulk)
    deleted = opensearch_client.delete_by_ids(object(), ["ok", "gone", "boom"])

    assert seen["ignore_status"] == (404,)
    assert deleted == 2  # 실제 삭제 1 + 이미 없음 1


def test_bulk_index_raises_when_failed_ids_cannot_be_retried(monkeypatch):
    """R5: 실패 id를 문서로 복원 못 하면 재시도 목록이 조용히 짧아지고, 다음 라운드가
    성공하면 '전부 성공'으로 보고된다 — PG와 발산한 채 성공 로그만 남는 경로."""
    monkeypatch.setattr(opensearch_client.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        opensearch_client,
        "bulk",
        lambda client, actions, **kwargs: (0, [{"index": {"_id": None, "status": 429}}]),
    )

    with pytest.raises(RuntimeError, match="복원하지 못했"):
        opensearch_client.bulk_index(
            object(), [{"id": "a", "source_type": "x", "title": "t", "content": "c"}], retries=3
        )
