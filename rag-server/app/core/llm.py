"""rag-server LLM 호출 진입점 — A360-Assistant-Backend/app/core/llm.py 이식본.

백오피스 rag-server는 백엔드와 **별도 서버**로 뜨고 DB만 공유한다(둘 다 최상위 패키지명이
`app`이라 백엔드 코드를 그대로 import할 수 없다). 그래서 문서 파싱 에이전트가 필요로 하는
부분 — chat()/usage_context()/record_usage()/cost_usd() — 만 그대로 이식했다.

토큰·비용·지연은 백엔드와 **동일한 llm_usage 테이블(공유 DB)**에 남긴다. 다만 rag-server는
SQLAlchemy/app.models가 없으므로, ORM 대신 이미 쓰고 있는 psycopg 원시 INSERT로 같은 컬럼을
채운다 — 백엔드가 남기는 row와 형태가 동일하다. 적재 파이프라인은 사용자 없이 도는
백그라운드라 기본 actor_type=system이다(임베딩 적재와 동일 귀속, retrieval/embed.py 참고).

사용량 귀속(누가/어디서)은 백엔드와 동일하게 ContextVar로 전파한다 — usage_context()로
(component/user/session)를 심어두면 그 안의 모든 기록이 자동 태깅된다.
"""

import contextvars
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from app.rag import config
from app.rag.observability import get_request_id

logger = logging.getLogger(__name__)

_client = None
# 지연 초기화 싱글턴 보호 — 문서 파서 run()이 ThreadPoolExecutor로 배치를 병렬 실행하므로
# 락이 없으면 여러 워커가 동시에 OpenAI() 인스턴스를 만든다(순차 호출 땐 무해했던 경쟁).
_client_lock = threading.Lock()


@dataclass(frozen=True)
class UsageContext:
    """현재 LLM 사용의 귀속 정보. 기본은 시스템(사용자 무관 — 적재 파이프라인)."""

    actor_type: str = "system"          # "user" | "system"
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    component: str = "other"            # vision | agent | rag_embed | rag_rerank | rag_parse | other


_usage_ctx: contextvars.ContextVar[UsageContext] = contextvars.ContextVar(
    "usage_ctx", default=UsageContext()
)


@contextmanager
def usage_context(
    *,
    component: str,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    actor_type: str | None = None,
):
    """이 블록 안의 모든 LLM 사용을 (component/user/session)로 귀속시킨다.

    actor_type을 안 주면 user_id 유무로 자동 판정한다 (user_id 있으면 user, 없으면 system).
    """
    resolved_actor = actor_type or ("user" if user_id is not None else "system")
    token = _usage_ctx.set(
        UsageContext(
            actor_type=resolved_actor,
            user_id=user_id,
            session_id=session_id,
            component=component,
        )
    )
    try:
        yield
    finally:
        _usage_ctx.reset(token)


def current_usage_context() -> UsageContext:
    return _usage_ctx.get()


# 보조 모델(임베딩·리랭커) 공식 단가 (USD per 1M tokens) — 백엔드 llm.py와 동일 테이블(RPA-97).
_AUX_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "rerank-2.5-lite": (0.02, 0.0),
    "rerank-2.5": (0.05, 0.0),
}


def cost_usd(input_tokens: int, output_tokens: int, model: str | None = None) -> float | None:
    """비용(USD)을 모델별 단가로 계산한다 — 백엔드 llm.py와 동일 규칙.

    - 보조 모델(임베딩·리랭커)은 내장 공식 단가 테이블로.
    - 그 외(주 챗 모델 등)는 env 단가(LLM_INPUT/OUTPUT_COST_PER_1M).
    - 단가를 못 구하면 None.
    """
    if model:
        for prefix in sorted(_AUX_MODEL_PRICES, key=len, reverse=True):
            if model.startswith(prefix):
                in_price, out_price = _AUX_MODEL_PRICES[prefix]
                return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    try:
        in_price = float(os.environ["LLM_INPUT_COST_PER_1M"])
        out_price = float(os.environ["LLM_OUTPUT_COST_PER_1M"])
    except (KeyError, ValueError):
        return None
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def _get_client():
    global _client
    # double-checked locking: 빠른 경로는 락 없이 읽고, 미초기화일 때만 락 안에서 한 번 더
    # 확인해 단 하나의 인스턴스만 만든다(동시 워커 경쟁 방지).
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = (config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")).strip()
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다")
                from openai import OpenAI

                # max_retries: 429(TPM/RPM)에 SDK가 지수 백오프 + Retry-After로 재시도 → 동시
                # 파싱의 순간 한도 초과를 버리지 않고 흡수한다(기본 2는 부족).
                _client = OpenAI(api_key=api_key, max_retries=config.OPENAI_MAX_RETRIES)
    return _client


def chat(
    messages: list[dict],
    *,
    purpose: str,
    model: str | None = None,
    session_id: uuid.UUID | None = None,
    response_format: dict | None = None,
) -> str:
    """Chat Completions 호출 후 응답 텍스트를 반환하고 사용량을 기록한다 — 백엔드 chat()과 동일.

    귀속(user/component)은 usage_context()에서 읽는다. response_format은 OpenAI JSON mode /
    Structured Outputs dict를 그대로 패스스루한다. 반환은 str 그대로이며 JSON 파싱·검증은 호출부가 한다.
    """
    from openai import AuthenticationError, RateLimitError

    model = model or config.AGENT_PARSE_MODEL
    create_kwargs: dict = {"model": model, "messages": messages}
    if response_format is not None:
        create_kwargs["response_format"] = response_format
    started = time.monotonic()
    try:
        response = _get_client().chat.completions.create(**create_kwargs)
    except AuthenticationError as e:
        raise RuntimeError("OpenAI 인증 실패 — API 키를 확인하세요") from e
    except RateLimitError as e:
        raise RuntimeError("OpenAI 사용량 한도 초과 — 크레딧/요금제를 확인하세요") from e
    latency_ms = int((time.monotonic() - started) * 1000)

    usage = response.usage
    record_usage(
        purpose=purpose,
        model=model,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        latency_ms=latency_ms,
        session_id=session_id,
    )
    return response.choices[0].message.content or ""


def _observability_dsn() -> str | None:
    """사용량 기록 대상 DSN — OBSERVABILITY_DATABASE_URL만 사용한다.

    미설정이면 None을 돌려주고 호출측이 기록을 건너뛴다. 예전엔 앱/RAG DB(database_dsn)로
    폴백했으나, 그러면 관측 사용량이 RAG 코퍼스 DB로 조용히 섞인다 — 폴백을 제거했다
    (백엔드 RPA-260과 동일 계약). SQLAlchemy 스타일 URL(postgresql+psycopg://)이 오면
    libpq가 받는 형태(postgresql://)로 바꿔 psycopg에 넘긴다.
    """
    url = config.OBSERVABILITY_DATABASE_URL
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
    latency_ms: int | None = None,
    session_id: uuid.UUID | None = None,
    request_id: str | None = None,
    ctx: UsageContext | None = None,
) -> None:
    """공유 llm_usage 테이블에 한 건 기록한다 — 백엔드와 동일 컬럼(ORM 대신 psycopg 원시 INSERT).

    귀속 정보는 ctx(명시) 또는 현재 ContextVar에서 온다. 백엔드 record_usage와 마찬가지로
    기록 실패(DB 다운·테이블 부재 등)가 호출을 실패시키면 안 되므로 예외는 삼킨다.
    llm_usage 테이블 자체는 백엔드가 소유·마이그레이션하므로 여기서 만들지 않는다.

    request_id: 명시적으로 안 넘기면 현재 ContextVar(get_request_id())에서 가져온다.
    이전엔 이 함수가 request_id를 아예 안 남겨서(컬럼 자체가 INSERT에 없었음), 이
    테이블의 rag_embed/rag_rerank/rag_parse 행 대부분이 request_id NULL이었다 —
    ContextVar 자체는 이미 있었는데 여기 연결만 안 되어 있던 단순 누락.
    """
    ctx = ctx or current_usage_context()
    resolved_session = session_id if session_id is not None else ctx.session_id
    resolved_request_id = request_id if request_id is not None else get_request_id()
    dsn = _observability_dsn()
    if dsn is None:
        return  # OBSERVABILITY_DATABASE_URL 미설정 → 관측 기록 skip (앱/RAG DB로 폴백하지 않음)
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_usage
                        (session_id, actor_type, user_id, component, purpose, model,
                         input_tokens, output_tokens, cost_usd, latency_ms, request_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(resolved_session) if resolved_session else None,
                        ctx.actor_type,
                        str(ctx.user_id) if ctx.user_id else None,
                        ctx.component,
                        purpose,
                        model,
                        input_tokens,
                        output_tokens,
                        cost_usd(input_tokens, output_tokens, model),
                        latency_ms,
                        resolved_request_id,
                    ),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — 기록 실패가 호출을 실패시키면 안 된다 (백엔드와 동일)
        logger.warning("LLM 사용량 기록 실패 (호출은 정상): %s", e)


def record_ragas_validation_attempt(
    *,
    doc_id: str,
    doc_title: str | None,
    question: str | None,
    outcome: str,
    failed_snippets: str | None = None,
) -> None:
    """RAGAS 골드셋 작성 화면의 근거 검증 시도를 관측 DB(ragas_validation_attempts)에
    기록한다 — 관측 DB 쓰기는 rag-server 적재 경로에만 허용한다는 정책에 따라, ops-server는
    이 함수를 직접 호출하지 않고 HTTP로 요청해서(POST /observability/ragas-validation-
    attempts) 여기로 위임한다. 기록 실패가 골드셋 저장 자체를 막으면 안 되므로 예외는 삼킨다
    (record_usage와 동일 원칙)."""
    dsn = _observability_dsn()
    if dsn is None:
        return  # OBSERVABILITY_DATABASE_URL 미설정 → 관측 기록 skip (앱/RAG DB로 폴백하지 않음)
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
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
    except Exception as e:  # noqa: BLE001 — 기록 실패가 골드셋 저장을 막으면 안 된다
        logger.warning("RAGAS 검증 시도 기록 실패 (저장은 정상 진행): %s", e)
