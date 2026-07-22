"""chunk_size 실험 등에서 쓴 LLM 비용을 공용 관측 DB의 llm_usage 테이블에 남긴다.

테이블은 A360-Assistant-Backend가 소유·생성하고(다른 관측 테이블과 동일 관례),
rag-server의 llm.py::record_usage와 같은 방식으로 psycopg 원시 INSERT만 한다.
component="ragas_chunk_experiment"로 태그해서 실제 운영 트래픽(agent/rag_embed 등)과
섞이지 않고 나중에 component 기준으로 걸러서 볼 수 있게 한다. 기록 실패가 실험 자체를
막으면 안 되므로 예외는 삼킨다.
"""

import logging
import os

logger = logging.getLogger(__name__)

COMPONENT = "ragas_chunk_experiment"


def _observability_dsn() -> str | None:
    url = os.getenv("OBSERVABILITY_DATABASE_URL", "").strip()
    if not url:
        return None
    return (
        url.replace("postgresql+psycopg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )


def record_usage(
    *,
    purpose: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
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
                    INSERT INTO llm_usage
                        (actor_type, component, purpose, model, input_tokens, output_tokens, cost_usd)
                    VALUES ('system', %s, %s, %s, %s, %s, %s)
                    """,
                    (COMPONENT, purpose, model, input_tokens, output_tokens, cost_usd),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — 기록 실패가 실험을 막으면 안 된다
        logger.warning("chunk 실험 LLM 사용량 기록 실패 (실험은 정상 진행): %s", e)
