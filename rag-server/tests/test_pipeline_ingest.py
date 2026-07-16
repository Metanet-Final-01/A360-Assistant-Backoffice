import json
import sys
import types
from types import SimpleNamespace

from app.rag import pipeline
from app.rag.store import db


class _Conn:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_ingest_skips_only_embedding_for_unchanged_content(monkeypatch, tmp_path):
    docs = [
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
    jsonl = tmp_path / "rag_documents.jsonl"
    jsonl.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs), encoding="utf-8")
    monkeypatch.setattr(pipeline.config, "RAG_DOCUMENTS_JSONL", jsonl)

    conn = _Conn()
    captured = {}
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(db, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(
        db,
        "get_content_hashes",
        lambda conn, ids: {"doc-unchanged": db.content_hash("same content")},
    )

    def fake_upsert(conn, documents, embeddings):
        captured["upsert_ids"] = [d["id"] for d in documents]
        captured["embedding_slots"] = embeddings
        return len(documents)

    monkeypatch.setattr(db, "upsert_documents", fake_upsert)

    fake_embed_module = types.ModuleType("app.rag.retrieval.embed")

    def fake_embed_texts(contents, on_progress=None):
        captured["embedded_contents"] = contents
        return [[0.1, 0.2]]

    fake_embed_module.embed_texts = fake_embed_texts
    monkeypatch.setitem(sys.modules, "app.rag.retrieval.embed", fake_embed_module)

    fake_os_module = types.ModuleType("app.rag.store.opensearch_client")
    fake_os_module.connect = lambda: object()
    fake_os_module.ensure_index = lambda client: None

    def fake_bulk_index(client, documents):
        captured["opensearch_ids"] = [d["id"] for d in documents]
        return len(documents)

    fake_os_module.bulk_index = fake_bulk_index
    monkeypatch.setitem(sys.modules, "app.rag.store.opensearch_client", fake_os_module)

    pipeline.cmd_ingest(SimpleNamespace(clean=False, skip_embedding=False, skip_opensearch=False))

    assert captured["embedded_contents"] == ["new content"]
    assert captured["upsert_ids"] == ["doc-unchanged", "doc-new"]
    assert captured["opensearch_ids"] == ["doc-unchanged", "doc-new"]
    assert captured["embedding_slots"][0] is None
    assert captured["embedding_slots"][1] == [0.1, 0.2]
    assert conn.commits == 1
