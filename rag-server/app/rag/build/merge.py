"""청킹 헬퍼 — rag_documents 한 건을 chunk_size 경계로 쪼개는 `_split_document`와
소스타입별 청크 값을 고르는 `chunk_params_for`. build-llm 빌더(merge_llm)가 청킹 단계에서 쓴다.

원래 이 파일은 v1 규칙 빌더 `build_rag_documents`(docs.jsonl·packages.json·bots.jsonl →
action_schema/package_overview/doc_page/bot_example 병합)와 그 헬퍼를 담았으나, LLM 구조화
추출(build-llm)로 파이프라인이 일원화되면서(refactor/remove-build-v2) 규칙 빌더·봇 요약·
JAR 병합 로직을 걷어냈다. 남은 것은 소스타입 무관하게 쓰이는 청킹 유틸뿐이다.

chunk_size를 넘는 문서는 여러 row로 쪼개지며 `parent_id`(원 문서 id)와 `chunk_index`(0부터)를
갖는다. 안 쪼개진 문서도 스키마 일관성을 위해 `parent_id=id`, `chunk_index=0`을 갖는다.
"""

import hashlib

from .chunk import chunk_text


def _doc_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# doc_page는 산문(문단/문장 위주), 나머지는 빌더가 조립한 "라벨: 값" 정형 텍스트
_CHUNK_STRATEGY_BY_SOURCE_TYPE = {"doc_page": "prose"}
_DEFAULT_CHUNK_STRATEGY = "structured"

# 이보다 짧은 선두 조각(대개 breadcrumb/헤더 줄만 남은 경우)은 다음 청크에 합쳐
# 저품질 단독 청크가 생기지 않게 한다.
_MIN_LEADING_CHUNK_CHARS = 150


def chunk_params_for(source_type: str, default_size: int, default_overlap: int) -> tuple[int, int]:
    """소스 타입별 청킹 값을 고른다. 표에 없는 타입은 호출자가 준 값을 그대로 쓴다.

    타입별로 나눈 이유는 config.CHUNK_PARAMS_BY_SOURCE_TYPE 주석 참고 — 산문(doc_page)과
    정형+ko본문(action_schema)은 길이 분포도 경계 손실의 대가도 달라, 한 값으로 맞추면
    한쪽이 손해다. 실제 적용값은 metadata.chunk_size/chunk_overlap에 그대로 기록되므로
    나중에 "이 row가 어떤 설정에서 나왔는지" 추적할 수 있다.
    """
    from .. import config

    return config.CHUNK_PARAMS_BY_SOURCE_TYPE.get(source_type, (default_size, default_overlap))


# 후속 청크(chunk_index>0)에 다시 붙일 식별 머리로 채택할 줄. content 선두에서 찾는다.
# action_schema는 "패키지:/액션:", doc_page는 breadcrumb 줄이 그 역할을 한다.
_HEAD_LINE_PREFIXES = ("패키지:", "액션:", "트리거:")
_HEAD_MAX_LINES = 3


def _continuation_head(doc: dict) -> str:
    """후속 청크 앞에 붙일 식별 머리.

    후속 청크는 파라미터 목록 **중간부터** 시작해서, 그것만 검색에 걸리면 LLM이 "이 액션의
    파라미터는 이게 전부"라고 읽는다(실측: chunk_index>0 48행 전부 `액션:` 줄 없음).
    문서 제목만 붙이던 기존 규약은 v1의 doc_page(breadcrumb+본문) 전제에서 나온 것이라,
    "패키지:/액션:/파라미터:" 정형인 v2 content에는 식별 정보가 모자란다.

    content 선두에서 식별 줄을 그대로 재사용한다 — 새로 조립하지 않으므로 표기가 갈릴 일이 없다.
    식별 줄이 없으면(doc_page 등) 제목으로 폴백한다.
    """
    lines = (doc.get("content") or "").splitlines()
    picked = [ln for ln in lines[:_HEAD_MAX_LINES] if ln.startswith(_HEAD_LINE_PREFIXES)]
    if not picked:
        return f"{doc.get('title', '')}\n"
    return "\n".join(picked) + "\n"


def _split_document(doc: dict, chunk_size: int | None, chunk_overlap: int) -> list[dict]:
    if chunk_size is None:
        # chunk_size=None: 청킹 없이 원본 길이 그대로 (현재 호출자는 항상 값을 넘김 — 방어적 분기)
        return [{**doc, "parent_id": doc["id"], "chunk_index": 0}]

    strategy = _CHUNK_STRATEGY_BY_SOURCE_TYPE.get(doc["source_type"], _DEFAULT_CHUNK_STRATEGY)
    chunk_size, chunk_overlap = chunk_params_for(doc["source_type"], chunk_size, chunk_overlap)
    parts = chunk_text(doc["content"], chunk_size, chunk_overlap, strategy=strategy)

    if len(parts) > 1 and len(parts[0]) < _MIN_LEADING_CHUNK_CHARS:
        parts = [parts[0] + "\n\n" + parts[1]] + parts[2:]

    # 어떤 설정으로 쪼개졌는지 metadata에 남겨서, 나중에 다른 chunk_size/전략을 실험할 때
    # "이 row가 어떤 실행에서 나온 건지" 추적할 수 있게 한다.
    def _with_chunk_meta(base_doc: dict, chunk_count: int) -> dict:
        return {
            **base_doc,
            "metadata": {
                **base_doc.get("metadata", {}),
                "chunk_strategy": strategy,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "chunk_count": chunk_count,
            },
        }

    if len(parts) <= 1:
        merged = _with_chunk_meta(doc, chunk_count=1)
        return [{**merged, "parent_id": doc["id"], "chunk_index": 0, "content": parts[0] if parts else doc["content"]}]

    # 청크가 여러 개면 각 청크 맨 앞에 식별 머리를 붙여 문맥을 유지한다 —
    # 그러지 않으면 뒤쪽 청크들은 어느 문서 소속인지 알 길이 없는 본문 조각만 남는다.
    head = _continuation_head(doc)
    return [
        {
            **_with_chunk_meta(doc, chunk_count=len(parts)),
            "id": _doc_id(doc["id"], str(index)),
            "parent_id": doc["id"],
            "chunk_index": index,
            "content": (
                f"{doc['title']}\n\n{part}" if index == 0
                else f"{head}(이어짐: {index + 1}/{len(parts)} 조각)\n\n{part}"
            ),
        }
        for index, part in enumerate(parts)
    ]
