"""RAGAS 골드셋 작성 화면의 근거(reference_contexts) 검증 시도를 관측 DB에 기록한다.

테이블(ragas_validation_attempts)은 A360-Assistant-Backend의 app/models.py가 소유·생성한다
(다른 관측 테이블과 동일한 관례) — 여기서는 rag-server의 llm.py::record_usage와 같은 방식으로
psycopg 원시 INSERT만 한다. 기록 실패가 골드셋 저장 자체를 막으면 안 되므로 예외는 삼킨다.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _observability_dsn() -> str | None:
    url = os.getenv("OBSERVABILITY_DATABASE_URL", "").strip()
    if not url:
        return None
    return (
        url.replace("postgresql+psycopg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )


def record_attempt(
    *,
    doc_id: str,
    doc_title: str | None,
    question: str | None,
    outcome: str,
    failed_snippets: str | None = None,
) -> None:
    dsn = _observability_dsn()
    if not dsn:
        return
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ragas_validation_attempts
                        (doc_id, doc_title, question, outcome, failed_snippets)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (doc_id, doc_title, question, outcome, failed_snippets),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — 기록 실패가 저장을 막으면 안 된다
        logger.warning("RAGAS 검증 시도 기록 실패 (저장은 정상 진행): %s", e)
