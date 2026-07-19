"""RAGAS 골드셋 작성 화면의 근거(reference_contexts) 검증 시도를 관측 DB에 기록한다.

관측 DB 쓰기는 rag-server 적재 파이프라인에만 허용한다는 정책에 따라, 여기서는
관측 DB에 직접 연결하지 않는다 — rag-server의 POST /observability/ragas-validation-
attempts를 호출해서 기록을 위임한다(CodeRabbit #42 지적 반영, 이전엔 psycopg로
직접 INSERT했음). rag-server가 안 떠 있거나 기록에 실패해도 골드셋 저장 자체를
막으면 안 되므로 예외는 삼킨다.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8200").rstrip("/")
_TIMEOUT_SECONDS = 5


def record_attempt(
    *,
    doc_id: str,
    doc_title: str | None,
    question: str | None,
    outcome: str,
    failed_snippets: str | None = None,
) -> None:
    try:
        response = requests.post(
            f"{_RAG_SERVER_URL}/observability/ragas-validation-attempts",
            json={
                "doc_id": doc_id,
                "doc_title": doc_title,
                "question": question,
                "outcome": outcome,
                "failed_snippets": failed_snippets,
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as e:  # noqa: BLE001 — 기록 실패가 저장을 막으면 안 된다
        logger.warning("RAGAS 검증 시도 기록 실패 (저장은 정상 진행): %s", e)
