"""OpenSearch BM25 키워드 검색. pgvector와 별도로 rag_documents를 동일 id로 색인한다."""

import time

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

from .. import config
from ..observability import log_call, log_event

_INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                "korean_cjk_bigram": {"type": "cjk_bigram"},
                "english_stop": {"type": "stop", "stopwords": "_english_"},
            },
            "analyzer": {
                "korean_cjk": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["cjk_width", "lowercase", "korean_cjk_bigram", "english_stop"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "package_name": {"type": "keyword"},
            "action_name": {"type": "keyword"},
            # 스키마 출처/신뢰 등급(jar=검증됨 / llm_agent=문서 파싱 미검증). BM25 후보 단계에서도
            # 신뢰 등급으로 필터할 수 있게 keyword로 색인한다(권위 있는 값은 pgvector metadata에도 있음).
            "schema_source": {"type": "keyword"},
            "locale": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "korean_cjk", "fields": {"raw": {"type": "keyword"}}},
            "url": {"type": "keyword", "index": False},
            "content": {"type": "text", "analyzer": "korean_cjk"},
            "parent_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
        }
    },
}


def connect() -> OpenSearch:
    kwargs = {"hosts": [config.OPENSEARCH_HOST], "http_compress": True, "timeout": 30}
    if config.OPENSEARCH_HOST.startswith("https"):
        kwargs.update(use_ssl=True, verify_certs=True)
    if config.OPENSEARCH_USERNAME:
        kwargs["http_auth"] = (config.OPENSEARCH_USERNAME, config.OPENSEARCH_PASSWORD)
    return OpenSearch(**kwargs)


def ensure_index(client: OpenSearch) -> None:
    if not client.indices.exists(index=config.OPENSEARCH_INDEX):
        client.indices.create(index=config.OPENSEARCH_INDEX, body=_INDEX_BODY)


def delete_index(client: OpenSearch) -> None:
    """색인 전체 삭제. bulk_index는 옛 문서를 지우지 않는 순수 색인(op_type=index)이라,
    RAG 구조를 크게 바꿔 재적재할 때는 명시적으로 지우고 ensure_index로 새로 만들어야
    한다 — 자동 호출되지 않고 `ingest --clean`에서만 실행된다."""
    if client.indices.exists(index=config.OPENSEARCH_INDEX):
        client.indices.delete(index=config.OPENSEARCH_INDEX)


def _partition_errors(errors: list, ignore_statuses: tuple[int, ...] = ()) -> tuple[list[str], int]:
    """bulk()가 돌려준 실패 항목을 (진짜 실패 id, 무시한 상태코드 건수)로 나눈다.
    항목은 {op: {_id, status, error}} 형태.

    무시 건수를 따로 세는 이유: opensearch-py는 ignore_status를 줘도 success 카운터엔 넣지
    않는다(ok는 2xx일 때만 True). 삭제의 404처럼 "이미 목표 상태"인 건은 호출부가 반환값에
    더해야 집계가 실제와 맞는다."""
    failed: list[str] = []
    ignored = 0
    for item in errors:
        for info in (item or {}).values():
            if not isinstance(info, dict):
                continue
            if info.get("status") in ignore_statuses:
                ignored += 1
            else:
                failed.append(info.get("_id"))
    return failed, ignored


def bulk_index(client: OpenSearch, documents: list[dict], retries: int = 5) -> int:
    """documents를 색인한다. 부분 실패(429/5xx)는 실패한 문서만 지수 백오프로 재시도하고,
    끝내 남으면 실패 id를 남기고 예외를 던진다 — PG 커밋 뒤에 도는 단계라 조용히 넘기면
    PG와 OpenSearch가 발산한 채로 "성공" 로그만 남는다(임베딩 쪽 재시도와 같은 정책)."""

    def _actions(docs: list[dict]):
        for doc in docs:
            yield {
                "_op_type": "index",
                "_index": config.OPENSEARCH_INDEX,
                "_id": doc["id"],
                "_source": {
                    "id": doc["id"],
                    "source_type": doc["source_type"],
                    "package_name": doc.get("package_name"),
                    "action_name": doc.get("action_name"),
                    "schema_source": (doc.get("metadata") or {}).get("schema_source"),
                    "locale": doc.get("locale"),
                    "title": doc["title"],
                    "url": doc.get("url"),
                    "content": doc["content"],
                    "parent_id": doc.get("parent_id", doc["id"]),
                    "chunk_index": doc.get("chunk_index", 0),
                },
            }

    by_id = {doc["id"]: doc for doc in documents}
    pending = list(documents)
    total_success = 0
    for attempt in range(retries):
        try:
            # raise_on_error=False: 예외로 뭉개면 어떤 문서가 실패했는지 알 수 없다 —
            # errors를 받아서 실패분만 다시 보낸다.
            success, errors = bulk(client, _actions(pending), raise_on_error=False)
        except Exception as exc:  # 연결/타임아웃 등 요청 전체 실패도 재시도 대상
            if attempt == retries - 1:
                raise
            log_event("opensearch_bulk_attempt", attempt=attempt + 1, retries=retries,
                      status="error", error_type=type(exc).__name__, error_message=str(exc))
            time.sleep(2**attempt)
            continue

        total_success += success
        failed, _ = _partition_errors(errors)
        if not failed:
            return total_success
        log_event("opensearch_bulk_attempt", attempt=attempt + 1, retries=retries,
                  status="partial_failure", failed=len(failed), failed_ids=failed[:20])
        print(f"  [OpenSearch] 색인 실패 {len(failed)}개 (시도 {attempt + 1}/{retries}): {failed[:10]}")
        if attempt == retries - 1:
            raise RuntimeError(f"OpenSearch 색인이 {len(failed)}개 문서에서 실패했습니다: {failed[:10]}")
        # 실패 id를 원본 문서로 되돌리지 못하면(응답의 _id가 비었거나 매핑 불가) 그 문서는
        # 조용히 재시도 대상에서 빠지고, 다음 라운드가 성공하면 "전부 성공"으로 보고된다 —
        # PG와 발산한 채 성공 로그만 남는 경로라 예외로 막는다.
        retry_docs = [by_id[i] for i in failed if i in by_id]
        if len(retry_docs) != len(failed):
            missing = [i for i in failed if i not in by_id]
            raise RuntimeError(
                f"OpenSearch 색인 실패 문서를 재시도 목록으로 복원하지 못했습니다: {missing[:10]}"
            )
        pending = retry_docs
        time.sleep(2**attempt)
    return total_success


def delete_by_ids(client: OpenSearch, ids: list[str]) -> int:
    """주어진 id를 색인에서 삭제한다 — pgvector에서 지운 고아 row를 OpenSearch에도 반영해
    두 저장소가 발산하지 않게 한다(bulk_index는 op_type=index라 옛 문서를 안 지운다).
    이미 없는 문서(404)는 목표 상태와 같으므로 실패로 치지 않고, 반환값에도 성공으로 센다 —
    빼면 "고아 삭제 0개"처럼 실제와 다른 과소 집계가 로그에 남는다."""
    if not ids:
        return 0
    actions = [{"_op_type": "delete", "_index": config.OPENSEARCH_INDEX, "_id": doc_id} for doc_id in ids]
    success, errors = bulk(client, actions, raise_on_error=False, ignore_status=(404,))
    failed, already_gone = _partition_errors(errors, ignore_statuses=(404,))
    if failed:
        log_event("opensearch_delete_failed", failed=len(failed), failed_ids=failed[:20])
        print(f"  [OpenSearch] 고아 삭제 실패 {len(failed)}개: {failed[:10]}")
    return success + already_gone


@log_call("bm25_search", capture_args=("query", "size"), capture_result=lambda r: {"count": len(r)})
def keyword_search(client: OpenSearch, query: str, size: int) -> list[dict]:
    body = {
        "size": size,
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "content"],
                "type": "best_fields",
            }
        },
    }
    resp = client.search(index=config.OPENSEARCH_INDEX, body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append({**src, "score": hit["_score"]})
    return results
