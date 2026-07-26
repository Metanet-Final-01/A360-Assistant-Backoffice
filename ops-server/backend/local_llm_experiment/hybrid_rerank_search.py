"""로컬 eval 후보 테이블(rag_documents_eval_{name}) 전용 hybrid(+rerank) 검색.

프로덕션 app/rag/retrieval/hybrid_search.py의 RRF 융합 공식(reciprocal_rank_fusion)과
Voyage rerank 호출 방식을 그대로 재구현하되, 테이블/인덱스 이름을 파라미터로 받아서
임의의 실험 후보 테이블(char 기준 cs{N}_ov{M}, token 기준 tok{N}_ov0)에 붙을 수 있게 한다.
프로덕션 db.search()/opensearch_client.keyword_search()는 rag_documents 테이블/인덱스에
하드코딩돼 있어 그대로는 재사용할 수 없어서, 검색 단위(단위=parent_id 대표 청크)만
이 eval 하네스의 기존 관례(run_local_model_combo.py의 vector_search())와 맞춘 표준
재구현이다. retrieval 단위를 청크가 아니라 parent_id로 맞추는 이유: 골드셋
reference_document_ids가 parent_id 기준이라 기존 vector-only/EDA 스크립트 전부가
이 단위로 채점해왔다 - 하이브리드만 청크 단위로 하면 비교가 깨진다.

BM25는 build_opensearch_index.py로 미리 만들어둔 candidate별 로컬 OpenSearch 인덱스
(a360-opensearch, localhost:9200)를 사용한다.
"""
import os

import httpx
from opensearchpy import OpenSearch

OPENSEARCH_HOST = "http://localhost:9200"
_RERANK_URL = "https://api.voyageai.com/v1/rerank"
RERANK_MODEL = "rerank-2.5-lite"


def get_opensearch_client() -> OpenSearch:
    return OpenSearch(hosts=[OPENSEARCH_HOST], use_ssl=False, verify_certs=False, http_compress=True, timeout=30)


def reciprocal_rank_fusion(rank_lists: list[list[str]], k: int, weights: list[float] | None = None) -> dict[str, float]:
    """app/rag/retrieval/hybrid_search.py::reciprocal_rank_fusion과 동일 공식.
    score(d) = sum over branches containing d of weight_branch / (k + rank_in_branch(d))."""
    if weights is None:
        weights = [1.0] * len(rank_lists)
    scores: dict[str, float] = {}
    for ids, weight in zip(rank_lists, weights):
        for index, doc_id in enumerate(ids):
            rank = index + 1
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return scores


def vector_branch(cursor, table_name: str, query_vec, pool_size: int):
    """pgvector 코사인 유사도 top-pool_size 청크 → parent_id 기준으로 첫 등장만 남겨 순위 리스트로 만든다.
    meta에 score(1-cosine_distance, 프로덕션 db.py 공식과 동일)도 같이 담아 정규화 융합(min_max+
    arithmetic_mean) 실험에서 재사용할 수 있게 한다 - 기존 title/content만 쓰는 호출부는 영향 없음."""
    cursor.execute(
        f"SELECT parent_id, title, content, 1 - (embedding <=> %s::vector) AS score "
        f"FROM {table_name} ORDER BY embedding <=> %s::vector LIMIT %s",
        (query_vec, query_vec, pool_size),
    )
    ids, meta = [], {}
    for parent_id, title, content, score in cursor.fetchall():
        if parent_id not in meta:
            ids.append(parent_id)
            meta[parent_id] = {"title": title, "content": content, "score": float(score)}
    return ids, meta


def bm25_branch(os_client: OpenSearch, index_name: str, query_text: str, pool_size: int):
    """OpenSearch multi_match(title^2, content) top-pool_size 청크 → parent_id 기준 첫 등장만."""
    body = {
        "size": pool_size,
        "query": {"multi_match": {"query": query_text, "fields": ["title^2", "content"], "type": "best_fields"}},
    }
    try:
        resp = os_client.search(index=index_name, body=body)
    except Exception as e:
        return [], {}, str(e)
    ids, meta = [], {}
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        parent_id = src["parent_id"]
        if parent_id not in meta:
            ids.append(parent_id)
            meta[parent_id] = {"title": src.get("title", ""), "content": src["content"], "score": hit["_score"]}
    return ids, meta, None


def voyage_rerank(query: str, documents: list[str], top_k: int) -> list[dict]:
    """Voyage rerank API 직접 호출 - app/rag/retrieval/rerank.py::rerank()와 동일 계약:
    반환은 relevance_score 내림차순 [{"index": 입력 documents의 0-based 위치, "relevance_score": float}, ...]."""
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY 환경변수가 필요합니다 (rerank)")
    if not documents:
        return []
    resp = httpx.post(
        _RERANK_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": RERANK_MODEL, "query": query, "documents": documents, "top_k": min(top_k, len(documents))},
        timeout=60.0,
    )
    resp.raise_for_status()
    results = resp.json()["data"]
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results


def hybrid_rerank_search(
    *, cursor, os_client: OpenSearch, table_name: str, index_name: str,
    query_text: str, query_vec, final_top_k: int,
    candidate_pool_size: int = 50, rerank_candidates: int = 20, rrf_k: int = 60,
    vector_weight: float = 1.0, bm25_weight: float = 1.0, mode: str = "hybrid_rerank",
):
    """mode: "vector"(비교용 폴백) / "hybrid"(RRF만) / "hybrid_rerank"(RRF+Voyage, 기본).
    반환: (retrieved_parent_ids, content_by_parent_id, diagnostics) - diagnostics는
    dense_rank/bm25_rank/rrf_score/rerank_score를 parent_id별로 담아 로그에 남기기 위함."""
    vector_ids, vector_meta = vector_branch(cursor, table_name, query_vec, candidate_pool_size if mode != "vector" else final_top_k)

    if mode == "vector":
        ids = vector_ids[:final_top_k]
        return ids, {i: vector_meta[i]["content"] for i in ids}, {i: {"retrieval_source": "vector"} for i in ids}

    bm25_ids, bm25_meta, bm25_error = bm25_branch(os_client, index_name, query_text, candidate_pool_size)

    rrf_scores = reciprocal_rank_fusion([vector_ids, bm25_ids], k=rrf_k, weights=[vector_weight, bm25_weight])
    dense_rank = {d: i + 1 for i, d in enumerate(vector_ids)}
    bm25_rank = {d: i + 1 for i, d in enumerate(bm25_ids)}

    meta_lookup = {**bm25_meta, **vector_meta}  # vector 쪽 우선 (프로덕션 관례와 동일)
    fused_ids = sorted(rrf_scores.keys(), key=lambda d: (-rrf_scores[d], d))
    fused_ids = [d for d in fused_ids if d in meta_lookup][:rerank_candidates]

    def _source(d):
        if d in dense_rank and d in bm25_rank:
            return "hybrid_both"
        return "hybrid_dense_only" if d in dense_rank else "hybrid_bm25_only"

    diagnostics = {
        d: {
            "dense_rank": dense_rank.get(d), "bm25_rank": bm25_rank.get(d),
            "rrf_score": rrf_scores[d], "retrieval_source": _source(d),
            "bm25_available": bm25_error is None, **({"bm25_error": bm25_error} if bm25_error else {}),
        }
        for d in fused_ids
    }

    if mode == "hybrid" or not fused_ids:
        ids = fused_ids[:final_top_k]
        return ids, {i: meta_lookup[i]["content"] for i in ids}, {i: diagnostics[i] for i in ids}

    rerank_inputs = [f"{meta_lookup[d]['title']}\n\n{meta_lookup[d]['content']}" for d in fused_ids]
    try:
        reranked = voyage_rerank(query_text, rerank_inputs, top_k=min(final_top_k, len(fused_ids)))
    except (RuntimeError, httpx.HTTPError) as e:
        ids = fused_ids[:final_top_k]
        for i in ids:
            diagnostics[i]["reranked"] = False
            diagnostics[i]["rerank_fallback_reason"] = str(e)
        return ids, {i: meta_lookup[i]["content"] for i in ids}, {i: diagnostics[i] for i in ids}

    final_ids = [fused_ids[item["index"]] for item in reranked]
    final_diagnostics = {}
    for item in reranked:
        d = fused_ids[item["index"]]
        final_diagnostics[d] = {**diagnostics[d], "reranked": True, "rerank_score": item["relevance_score"]}
    return final_ids, {i: meta_lookup[i]["content"] for i in final_ids}, final_diagnostics


def _min_max_normalize(scores: dict) -> dict:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def normalized_fusion_rerank_search(
    *, cursor, os_client: OpenSearch, table_name: str, index_name: str,
    query_text: str, query_vec, final_top_k: int,
    fusion_pool_size: int, bm25_weight: float, vector_weight: float,
    rerank_candidates: int, mode: str = "hybrid_rerank",
):
    """OpenSearch 공식 normalization-processor 방식(min_max normalize + arithmetic_mean)의
    app-side 재구현 - fusion_comparison_eval.py(2단계 실험)에서 RRF보다 일관되게 더 좋았던
    승자 설정(normalized, pool=150, w_bm25=0.5~0.7)을 실제 생성+RAGAS 파이프라인에 연결하기
    위한 hybrid_rerank_search()의 자매 함수. 반환 계약은 hybrid_rerank_search()와 동일."""
    vector_ids, vector_meta = vector_branch(cursor, table_name, query_vec, fusion_pool_size if mode != "vector" else final_top_k)
    if mode == "vector":
        ids = vector_ids[:final_top_k]
        return ids, {i: vector_meta[i]["content"] for i in ids}, {i: {"retrieval_source": "vector"} for i in ids}

    bm25_ids, bm25_meta, bm25_error = bm25_branch(os_client, index_name, query_text, fusion_pool_size)

    v_norm = _min_max_normalize({d: vector_meta[d]["score"] for d in vector_ids})
    b_norm = _min_max_normalize({d: bm25_meta[d]["score"] for d in bm25_ids})
    dense_rank = {d: i + 1 for i, d in enumerate(vector_ids)}
    bm25_rank = {d: i + 1 for i, d in enumerate(bm25_ids)}
    meta_lookup = {**bm25_meta, **vector_meta}

    all_docs = set(v_norm) | set(b_norm)
    combined = {d: bm25_weight * b_norm.get(d, 0.0) + vector_weight * v_norm.get(d, 0.0) for d in all_docs}
    fused_ids = sorted(combined.keys(), key=lambda d: (-combined[d], d))[:rerank_candidates]

    def _source(d):
        if d in dense_rank and d in bm25_rank:
            return "hybrid_both"
        return "hybrid_dense_only" if d in dense_rank else "hybrid_bm25_only"

    diagnostics = {
        d: {
            "dense_rank": dense_rank.get(d), "bm25_rank": bm25_rank.get(d),
            "normalized_score": combined[d], "retrieval_source": _source(d),
            "bm25_available": bm25_error is None, **({"bm25_error": bm25_error} if bm25_error else {}),
        }
        for d in fused_ids
    }

    if mode == "hybrid" or not fused_ids:
        ids = fused_ids[:final_top_k]
        return ids, {i: meta_lookup[i]["content"] for i in ids}, {i: diagnostics[i] for i in ids}

    rerank_inputs = [f"{meta_lookup[d]['title']}\n\n{meta_lookup[d]['content']}" for d in fused_ids]
    try:
        reranked = voyage_rerank(query_text, rerank_inputs, top_k=min(final_top_k, len(fused_ids)))
    except (RuntimeError, httpx.HTTPError) as e:
        ids = fused_ids[:final_top_k]
        for i in ids:
            diagnostics[i]["reranked"] = False
            diagnostics[i]["rerank_fallback_reason"] = str(e)
        return ids, {i: meta_lookup[i]["content"] for i in ids}, {i: diagnostics[i] for i in ids}

    final_ids = [fused_ids[item["index"]] for item in reranked]
    final_diagnostics = {}
    for item in reranked:
        d = fused_ids[item["index"]]
        final_diagnostics[d] = {**diagnostics[d], "reranked": True, "rerank_score": item["relevance_score"]}
    return final_ids, {i: meta_lookup[i]["content"] for i in final_ids}, final_diagnostics
