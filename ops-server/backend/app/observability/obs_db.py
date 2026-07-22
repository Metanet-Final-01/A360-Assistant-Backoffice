"""관측 DB 직접 조회 — 백엔드 admin API를 거치지 않는 읽기 경로.

## 왜 직접 읽나

원래는 백오피스가 백엔드 admin API를 거쳐 관측 데이터를 읽었다(A안, 2026-07-11 결정).
성능 실측상 차이가 없었고(싱가포르 왕복 ~280ms가 본체, API 홉은 1% 미만), 크레덴셜을
백엔드 한 곳에만 두는 이점이 있었다.

그러나 배포를 앞두고 뒤집혔다 — **관측 시스템이 관측 대상에 의존**하게 만들었기 때문이다
(APM 안티패턴). 백엔드가 죽으면 "왜 죽었는지" 보려는 과거 데이터까지 못 본다. 게다가
"장애 중엔 로컬 사본으로 본다"던 폴백은 실제로 작동하지 않았다(collect 실패 시 곧바로
return해 사본 조회까지 건너뛴다). 읽기 전용 롤을 쓰면 원래 A안 논거 5개 중 2개
(크레덴셜 유출 면적·실수 DELETE)도 성립하지 않는다.

→ **읽기는 관측 DB 직접(읽기 전용 롤), 쓰기는 admin API 유지**(설정 변경은 검증·권한·
   감사가 필요하므로). 이 모듈은 그 '읽기' 절반이다.

## 대가 (알고 감수한다)

- **스키마 결합**: 백엔드는 ORM(models)을 쓰지만 여기선 raw SQL이라, 컬럼이 바뀌면
  양쪽을 같이 고쳐야 한다. 그래서 반환 형태를 백엔드 admin API와 **동일하게** 맞춰
  호출부(collector·views)는 무변경으로 둔다 — 갈아끼우기만 하면 되게.
- **접근 감사 상실**: admin API 경유일 땐 조회가 백엔드 request_metrics에 남았다.
  직접 조회는 그 기록이 없다 — 필요하면 이 계층에서 자체 로깅을 붙인다.

## 조용한 폴백 금지 (중요)

크레덴셜이 없을 때 **백엔드 경유로 슬쩍 되돌아가지 않는다.** 백엔드에서 이미 두 번,
조용한 폴백이 사고를 숨겼다: OPENSEARCH_HOST 빈 값 → localhost 폴백으로 BM25가 무음
사망했고, RAG_DATABASE_URL 미주입 → 앱 DB 폴백으로 검색이 반쪽이 됐다. 둘 다 지표는
정상이었다. 여기서도 폴백하면 "직접 읽는 줄 알았는데 실은 백엔드에 의존 중"인 상태를
아무도 모른다. 그래서 미설정이면 ObservabilityDBUnavailable을 올린다.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 읽기 전용 롤 크레덴셜 — CFN이 Secrets Manager에서 주입한다(콘솔 수동 주입 금지).
# 백엔드의 OBSERVABILITY_DATABASE_URL과 **다른 롤**이다: 이쪽은 SELECT 권한만 가진다.
_ENV_KEY = "A360_OBSERVABILITY_DATABASE_URL"

# 조회 상한 — 화면·수집기가 실수로 전체 스캔을 걸지 않게. **admin API의 le=값과 종류별로
# 맞춘다**: 하나로 뭉뚱그리면 백오피스가 백엔드보다 적게/많이 가져와 계약이 어긋난다.
_MAX_LIMIT = 500  # audit_logs (백엔드 le=500)
_MAX_LIMIT_METRICS = 2000  # request_metrics · rag_events (백엔드 le=2000)
_MAX_LIMIT_TURN_EVENTS = 1000  # turn_events (백엔드 le=1000)
# llm-usage 집계의 breakdown 상한 — user/session 축은 행 수가 사용자·세션 수만큼 늘어난다.
_MAX_BREAKDOWN = 200
_STATEMENT_TIMEOUT_MS = 10_000

# llm-usage/stats의 group_by → 컬럼. 백엔드 _GROUP_COLS와 같은 매핑이어야 한다.
# **화이트리스트가 곧 방어다** — group_by는 SQL에 식별자로 들어가므로 바인드가 불가능하고,
# 사전에 없는 값은 여기서 막지 않으면 그대로 SQL에 박힌다.
_GROUP_COLS = {
    "component": "component",
    "model": "model",
    "user": "user_id",
    "session": "session_id",
}


class ObservabilityDBUnavailable(RuntimeError):
    """관측 DB 직접 조회를 쓸 수 없다 — 미설정이거나 연결 실패.

    호출부는 이걸 **삼키고 백엔드 경유로 되돌아가면 안 된다**. 화면에 "직접 조회 불가"를
    드러내야 운영이 구성 오류를 인지한다(위 '조용한 폴백 금지' 참고).
    """


def configured() -> bool:
    """직접 조회가 구성돼 있나 — 화면이 상태 배너를 띄울 때 쓴다."""
    return bool((os.getenv(_ENV_KEY) or "").strip())


def _dsn() -> str:
    dsn = (os.getenv(_ENV_KEY) or "").strip()
    if not dsn:
        raise ObservabilityDBUnavailable(
            f"{_ENV_KEY}가 설정되지 않았습니다. 관측 DB 읽기 전용 크레덴셜을 주입하세요 "
            "(백엔드 경유로 폴백하지 않습니다 — 구성 오류를 숨기지 않기 위함)."
        )
    # SQLAlchemy 형식(postgresql+psycopg://)을 그대로 넘기면 psycopg가 스킴을 못 읽는다.
    # 백엔드 관측 URL을 복붙하는 실수를 방어한다.
    if dsn.startswith("postgresql+"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


@contextmanager
def _cursor():
    """읽기 전용 조회용 커서. 매 호출 새 연결 — 수집은 주기 폴링이라 풀이 불필요하다.

    statement_timeout을 걸어 대시보드가 무거운 쿼리로 관측 DB를 붙잡지 않게 한다.

    ## 읽기 전용을 **연결 속성**으로 거는 이유 (실측으로 고친 자리)

    처음엔 커서에서 `SET default_transaction_read_only = on`을 실행했다. 그런데 그 설정은
    **다음 트랜잭션**의 기본값이라, 앞선 문장(statement_timeout) 때문에 이미 열린
    트랜잭션에는 적용되지 않는다. 실제로 그 상태에서 `CREATE TEMP TABLE`이 **통과했다** —
    "롤이 잘못 발급돼도 한 겹 더 막는다"고 적어둔 방어가 실은 아무것도 막지 않았다.
    가드가 걸렸다고 주장하는 자리와 동작이 실제로 읽는 자리가 달랐던 셈이다.

    psycopg의 `conn.read_only`는 트랜잭션이 열리기 전에 설정해야 하므로, 첫 문장을
    실행하기 **전에** 건다.
    """
    import psycopg

    try:
        conn = psycopg.connect(_dsn(), connect_timeout=10)
    except ObservabilityDBUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — 연결 실패도 '직접 조회 불가'로 통일해 올린다
        # 사용자에게 보이는 메시지는 최소로 두되(DSN·크레덴셜이 새면 안 된다), 원인은
        # 로그에 남긴다 — 안 그러면 "직접 조회 불가"만 보이고 왜인지는 아무도 모른다.
        logger.warning("관측 DB 연결 실패: %s", type(e).__name__, exc_info=True)
        raise ObservabilityDBUnavailable(f"관측 DB 연결 실패: {type(e).__name__}") from e
    try:
        conn.read_only = True  # 트랜잭션이 열리기 전에 — 위 docstring 참고
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
            yield cur
    except psycopg.Error as e:
        # 스키마·권한 오류(UndefinedTable, InsufficientPrivilege 등)가 raw로 새면 호출부는
        # 500을 낸다. 읽기 전용 롤을 새로 발급받는 구성에서 권한 부족은 흔한 시나리오라,
        # 이 모듈의 단일 오류 계약으로 모아 503으로 드러낸다.
        # psycopg.Error로 좁힌다 — 우리 로직 버그(KeyError 등)까지 'DB 불가'로 둔갑시키면
        # 원인을 숨기게 된다.
        logger.warning("관측 DB 조회 실패: %s", type(e).__name__, exc_info=True)
        raise ObservabilityDBUnavailable(f"관측 DB 조회 실패: {type(e).__name__}") from e
    finally:
        # close 실패가 원래 예외를 덮으면 호출부는 503 대신 예상 못한 500을 보고,
        # 진단에 필요한 예외 체인도 잃는다. 닫기 실패는 로그로만 남긴다.
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — 정리 실패로 조회 결과·원인을 잃지 않는다
            logger.warning("관측 DB 연결 종료 실패", exc_info=True)


def _clamp(limit: int, maximum: int = _MAX_LIMIT) -> int:
    return max(1, min(int(limit), maximum))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _as_uuid(value: str, field: str) -> str:
    """uuid 컬럼에 넣을 값을 검증한다.

    `user_id`·`session_id`는 DB에서 uuid 타입이라, 형식이 틀린 문자열을 그대로 넘기면
    psycopg가 InvalidTextRepresentation을 낸다. 그걸 잡지 않으면 **입력 형식 오류가
    "관측 DB 조회 실패"(503)로 둔갑해** 운영이 DB 장애를 의심하게 된다 — 실제로 화면에서
    user_id 칸에 아무 값이나 넣으면 그렇게 됐다. 여기서 걸러 400으로 내보낸다.
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise ValueError(f"{field} 형식이 올바르지 않습니다(UUID여야 합니다).") from e


def _utcnow() -> datetime:
    """UTC 기준 현재 — llm_usage.created_at이 timestamptz라 tz-aware여야 비교가 맞다."""
    return datetime.now(timezone.utc)


def _today() -> date:
    """롤업 테이블의 day 경계. 백엔드가 date.today()(서버 로컬)를 쓰므로 같게 맞춘다 —
    여기만 UTC로 바꾸면 같은 화면이 백엔드 경유일 때와 하루 어긋난다."""
    return date.today()


def probe() -> dict:
    """직접 조회가 실제로 되는지 확인 — health가 '도달성'만 보고 속지 않게 (RPA-249 교훈).

    백엔드에서 OpenSearch가 '도달 ok'인데 색인이 비어 검색이 반쪽이던 일이 있었다.
    여기선 실제 SELECT를 한 번 돌려 권한·스키마까지 확인한다.

    확인하려는 건 "붙어서 읽히나"이지 행 수가 아니다. `count(*)`는 관측 DB가 커질수록
    풀스캔이 되어(일 8천 행씩 쌓인다) statement_timeout에 걸릴 수 있다 — 프로브가
    무거워서 실패하면 "DB가 죽었다"로 오판하게 된다. 한 행만 읽어 같은 것을 확인한다.
    """
    with _cursor() as cur:
        cur.execute("select 1 from audit_logs limit 1")
        row = cur.fetchone()
    return {"ok": True, "has_rows": row is not None}


def fetch_audit_logs(
    limit: int = 500,
    method: str | None = None,
    status_code: int | None = None,
    user_id: str | None = None,
    since: str | None = None,
) -> dict:
    """감사 로그 — 백엔드 `GET /api/admin/audit-logs`와 **동일한 반환 형태**.

    since가 있으면 그 이후를 **오름차순**으로 준다(증분 커서). 최신순이면 limit에 걸렸을 때
    중간이 유실되기 때문 — 백엔드 계약과 같은 이유·같은 동작이다.
    """
    where: list[str] = []
    params: list[Any] = []
    if since:
        where.append("created_at > %s")
        params.append(since)
    if method:
        where.append("method = %s")
        params.append(method.upper())
    if status_code is not None:
        where.append("status_code = %s")
        params.append(int(status_code))
    if user_id:
        where.append("user_id = %s::uuid")
        params.append(_as_uuid(user_id, "user_id"))

    order = "created_at asc, id asc" if since else "created_at desc"
    sql = (
        "select request_id, user_id, method, path, status_code, latency_ms, created_at "
        "from audit_logs "
        + (f"where {' and '.join(where)} " if where else "")
        + f"order by {order} limit %s"
    )
    params.append(_clamp(limit))
    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "logs": [
            {
                "request_id": r[0],
                "user_id": str(r[1]) if r[1] else None,
                "method": r[2],
                "path": r[3],
                "status_code": r[4],
                "latency_ms": r[5],
                "created_at": _iso(r[6]),
            }
            for r in rows
        ]
    }


def fetch_request_metrics(
    since: str | None = None,
    limit: int = 500,
    method: str | None = None,
    path: str | None = None,
) -> dict:
    """raw 요청 메트릭 — 백엔드 `GET /api/admin/request-metrics`와 동일한 반환 형태.

    audit-logs와 같은 커서 규칙(since면 오름차순)이고, id는 수집기 중복 제거용이라
    반드시 포함한다.
    """
    where: list[str] = []
    params: list[Any] = []
    if since:
        where.append("created_at > %s")
        params.append(since)
    if method:
        where.append("method = %s")
        params.append(method.upper())
    if path:
        # 백엔드 .contains()와 동일한 부분일치. 값은 파라미터로 넘겨 SQL에 %를 박지 않는다.
        where.append("path like %s")
        params.append(f"%{path}%")

    order = "created_at asc, id asc" if since else "created_at desc, id desc"
    sql = (
        "select id, request_id, user_id, method, path, status_code, latency_ms, created_at "
        "from request_metrics "
        + (f"where {' and '.join(where)} " if where else "")
        + f"order by {order} limit %s"
    )
    params.append(_clamp(limit, _MAX_LIMIT_METRICS))
    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "rows": [
            {
                "id": r[0],
                "request_id": r[1],
                "user_id": str(r[2]) if r[2] else None,
                "method": r[3],
                "path": r[4],
                "status_code": r[5],
                "latency_ms": r[6],
                "created_at": _iso(r[7]),
            }
            for r in rows
        ]
    }


def fetch_rag_events(
    limit: int = 200,
    request_id: str | None = None,
    event: str | None = None,
) -> dict:
    """RAG 호출 이벤트 — 백엔드 `GET /api/admin/rag-events`와 동일한 반환 형태.

    event는 백엔드 admin API에 없는 필터다 — 사본 조회(log_store.load_rag_events)가
    제공하던 축이라, 직접 조회로 갈아끼워도 화면이 그대로 동작하게 여기서 받는다.
    """
    where: list[str] = []
    params: list[Any] = []
    if request_id:
        where.append("request_id = %s")
        params.append(request_id)
    if event:
        where.append("event = %s")
        params.append(event)

    sql = (
        "select id, request_id, event, function, status, duration_ms, detail, created_at "
        "from rag_events "
        + (f"where {' and '.join(where)} " if where else "")
        + "order by created_at desc, id desc limit %s"
    )
    params.append(_clamp(limit, _MAX_LIMIT_METRICS))
    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "events": [
            {
                "id": r[0],
                "request_id": r[1],
                "event": r[2],
                "function": r[3],
                "status": r[4],
                "duration_ms": r[5],
                "detail": r[6],
                "created_at": _iso(r[7]),
            }
            for r in rows
        ]
    }


def fetch_turn_events(session_id: str | None = None, limit: int = 200) -> dict:
    """에이전트 턴 타임라인 — 백엔드 `GET /api/admin/turn-events`와 동일한 반환 형태.

    session_id를 주면 **시간축으로 최근 limit건을 먼저 고른 뒤** 화면 순서(request_id, seq)로
    정렬한다. request_id는 uuid4라 시간과 무관해서, (request_id, seq)에 바로 limit을 걸면
    최신 턴이 아니라 사전순으로 앞선 임의의 턴이 잘린다 — 백엔드가 CodeRabbit 지적으로
    고친 지점이고, 여기서 순진하게 옮기면 그 버그를 되살린다.

    잘못된 session_id는 ValueError를 올린다(백엔드의 400 INVALID_ID에 대응).
    """
    n = _clamp(limit, _MAX_LIMIT_TURN_EVENTS)
    cols = (
        "session_id, request_id, seq, kind, stage, message, detail, elapsed_ms, created_at"
    )
    if session_id:
        sid = _as_uuid(session_id, "session_id")
        sql = (
            f"select {cols} from ("
            f"  select id, {cols} from turn_events where session_id = %s::uuid"
            "   order by created_at desc, id desc limit %s"
            ") recent order by request_id, seq"
        )
        params: list[Any] = [sid, n]
    else:
        sql = f"select {cols} from turn_events order by created_at desc limit %s"
        params = [n]

    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "events": [
            {
                "session_id": str(r[0]) if r[0] else None,
                "request_id": r[1],
                "seq": r[2],
                "kind": r[3],
                "stage": r[4],
                "message": r[5],
                "detail": r[6],
                "elapsed_ms": r[7],
                "created_at": _iso(r[8]),
            }
            for r in rows
        ]
    }


def fetch_llm_usage_stats(
    days: int = 30, group_by: str = "component", limit: int = _MAX_BREAKDOWN
) -> dict:
    """LLM 사용량 집계 — 백엔드 `GET /api/admin/llm-usage/stats`와 같은 키를 반환한다.

    ## breakdown에 상한이 있는 이유

    group_by가 user/session이면 행 수가 사용자·세션 수만큼 늘어난다(실측: session 축이
    이미 300개를 넘었고 계속 는다). 화면은 이걸 표·차트로 전부 그리므로 상한이 없으면
    응답 크기와 렌더링 비용이 선형으로 커진다. 호출이 많은 순으로 상위 N개만 준다.

    ## total은 잘린 합이 아니다

    처음엔 total을 breakdown 합으로 계산했다. 거기에 상한을 얹으면 **"이번 달 비용"이
    조용히 축소돼 보인다** — 숫자를 근거로 쓰는 화면에서 가장 나쁜 종류의 오류다.
    그래서 total은 전체 집계를 따로 센다. "두 쿼리가 다른 시점을 봐서 어긋난다"는
    원래 우려는 여기선 성립하지 않는다: 읽기 전용 트랜잭션 안이라 두 쿼리가 **같은
    스냅샷**을 본다.
    """
    col = _GROUP_COLS.get(group_by)
    if col is None:
        raise ValueError(f"group_by는 {sorted(_GROUP_COLS)} 중 하나여야 합니다: {group_by!r}")
    days = max(1, min(int(days), 365))
    since = _utcnow() - timedelta(days=days)
    n = _clamp(limit, _MAX_BREAKDOWN)

    with _cursor() as cur:
        cur.execute(
            "select count(*), coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0), "
            f"coalesce(sum(cost_usd), 0.0), count(distinct {col}) "
            "from llm_usage where created_at >= %s",
            [since],
        )
        t_calls, t_in, t_out, t_cost, group_count = cur.fetchone()
        cur.execute(
            f"select {col} as key, count(*) as calls, "
            "coalesce(sum(input_tokens), 0) as input_tokens, "
            "coalesce(sum(output_tokens), 0) as output_tokens, "
            "coalesce(sum(cost_usd), 0.0) as cost_usd "
            "from llm_usage where created_at >= %s "
            f"group by {col} order by count(*) desc limit %s",
            [since, n],
        )
        rows = cur.fetchall()

    breakdown = [
        {
            "key": str(r[0]) if r[0] is not None else None,
            "calls": int(r[1]),
            "input_tokens": int(r[2]),
            "output_tokens": int(r[3]),
            "cost_usd": round(float(r[4] or 0.0), 6),
        }
        for r in rows
    ]
    total = {
        "calls": int(t_calls or 0),
        "input_tokens": int(t_in or 0),
        "output_tokens": int(t_out or 0),
        "cost_usd": round(float(t_cost or 0.0), 6),
    }
    return {
        "period_days": days,
        "group_by": group_by,
        "total": total,
        "breakdown": breakdown,
        # 잘렸다는 사실을 숨기지 않는다 — 화면이 "상위 N개만"을 표시할 수 있어야
        # 합계와 내역의 차이를 오해하지 않는다.
        "breakdown_truncated": int(group_count or 0) > len(breakdown),
        "group_count": int(group_count or 0),
    }


def _request_ids_for_user(cur, user_id: str, limit: int) -> list[str]:
    """user_id로 관련 request_id를 찾는다.

    request_id/session_id는 사람이 외우기 어려운 opaque id라, 사람이 아는 값(user_id)으로
    먼저 요청들을 찾아 그 집합을 추적 대상으로 삼는다. 감사·성능 **두 곳을 모두** 본다 —
    한쪽에만 남는 요청이 있어서 하나만 보면 추적이 끊긴다.
    """
    cur.execute(
        "select request_id from ("
        "  select request_id, created_at from audit_logs"
        "   where user_id = %s::uuid and request_id is not null"
        "  union all"
        "  select request_id, created_at from request_metrics"
        "   where user_id = %s::uuid and request_id is not null"
        ") u group by request_id order by max(created_at) desc limit %s",
        [user_id, user_id, limit],
    )
    return [r[0] for r in cur.fetchall()]


def trace_by(
    request_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = 500,
) -> dict:
    """한 사건에 연결된 관측 레코드를 종류별로 모은다 (대시보드 #5).

    반환 **형태**는 사본 기반이던 `log_store.trace_by`와 같게 둔다. 다만 `rag_logs`의
    **내용물은 바뀐다**: 예전엔 RAG 서버 파일 로그를 그대로 담은 `{"raw": {...}}`였는데,
    같은 내용이 이미 관측 DB의 `rag_events`에 `event='http_request'`로 중앙화돼 있어
    그쪽을 쓴다(정형 컬럼). 화면도 함께 고쳤다.

    - request_id: HTTP 요청 1건 축 — 감사·성능·턴·RAG 전부 연결된다.
    - session_id: 대화 축 — turn_events만 직접 연결된다(나머지는 요청 축이라 세션 키가 없다).
    - user_id: 위 `_request_ids_for_user` 참고.

    사본 조회는 JSONL 전체를 읽어 파이썬에서 걸렀지만 여기선 WHERE로 넘긴다. 대신
    user_id 축은 request_id 집합이 커질 수 있어 limit으로 자른다 — 사본 시절엔 상한이
    없어 요청이 많은 사용자를 조회하면 화면이 통째로 느려질 수 있었다.
    """
    n = _clamp(limit, _MAX_LIMIT_METRICS)
    sid: str | None = _as_uuid(session_id, "session_id") if session_id else None
    uid: str | None = _as_uuid(user_id, "user_id") if user_id else None

    audit: list[tuple] = []
    metrics: list[tuple] = []
    rag_all: list[tuple] = []
    turns: list[tuple] = []

    with _cursor() as cur:
        request_ids: list[str] = [request_id] if request_id else []
        if uid:
            request_ids += [
                r for r in _request_ids_for_user(cur, uid, n) if r not in request_ids
            ]

        if request_ids:
            # = any(%s)는 목록을 배열 파라미터 하나로 넘긴다 — IN 절을 %s 개수만큼 만들면
            # request_id가 늘어날 때마다 SQL 문자열이 달라져 계획 재사용이 안 된다.
            #
            # 세 조회 모두 **최신 n건을 먼저 고른 뒤 표시 순서로 되돌린다.** 오름차순에
            # 바로 limit을 걸면, limit에 걸렸을 때 오래된 것만 남고 최신이 잘린다 —
            # 장애를 좇는 화면에서 정작 방금 일어난 일이 사라진다. user_id 축은
            # request_id가 여러 개라 실제로 걸린다. fetch_turn_events에서 이미 같은
            # 이유로 서브쿼리를 썼는데 여기엔 적용하지 않았던 자리다.
            cur.execute(
                "select request_id, user_id, method, path, status_code, latency_ms, created_at "
                "from (select * from audit_logs where request_id = any(%s) "
                "      order by created_at desc, id desc limit %s) recent "
                "order by created_at",
                [request_ids, n],
            )
            audit = cur.fetchall()
            cur.execute(
                "select id, request_id, user_id, method, path, status_code, latency_ms, created_at "
                "from (select * from request_metrics where request_id = any(%s) "
                "      order by created_at desc, id desc limit %s) recent "
                "order by created_at",
                [request_ids, n],
            )
            metrics = cur.fetchall()
            cur.execute(
                "select id, request_id, event, function, status, duration_ms, detail, created_at "
                "from (select * from rag_events where request_id = any(%s) "
                "      order by created_at desc, id desc limit %s) recent "
                "order by id",
                [request_ids, n],
            )
            rag_all = cur.fetchall()

        # 턴은 요청 축(request_id)과 대화 축(session_id) 둘 다로 붙는다.
        turn_where: list[str] = []
        turn_params: list[Any] = []
        if request_ids:
            turn_where.append("request_id = any(%s)")
            turn_params.append(request_ids)
        if sid:
            turn_where.append("session_id = %s::uuid")
            turn_params.append(sid)
        if turn_where:
            cur.execute(
                "select session_id, request_id, seq, kind, stage, message, detail, elapsed_ms, "
                "created_at from ("
                "  select * from turn_events "
                f" where {' or '.join(turn_where)} "
                "  order by created_at desc nulls last, id desc limit %s"
                ") recent "
                # created_at 우선 정렬 — request_id 문자열 순으로 묶으면 한 세션의 여러
                # 요청이 실제 발생 순서와 어긋난다(사본 구현에서 이미 고친 지점).
                "order by created_at nulls last, seq",
                [*turn_params, n],
            )
            turns = cur.fetchall()

    def _rag_row(r: tuple) -> dict:
        return {
            "id": r[0],
            "request_id": r[1],
            "event": r[2],
            "function": r[3],
            "status": r[4],
            "duration_ms": r[5],
            "detail": r[6],
            "created_at": _iso(r[7]),
        }

    return {
        "request_id": request_id,
        "session_id": session_id,
        "user_id": user_id,
        "matched_request_ids": sorted(request_ids),
        "audit_logs": [
            {
                "request_id": r[0],
                "user_id": str(r[1]) if r[1] else None,
                "method": r[2],
                "path": r[3],
                "status_code": r[4],
                "latency_ms": r[5],
                "created_at": _iso(r[6]),
            }
            for r in audit
        ],
        "request_metrics": [
            {
                "id": r[0],
                "request_id": r[1],
                "user_id": str(r[2]) if r[2] else None,
                "method": r[3],
                "path": r[4],
                "status_code": r[5],
                "latency_ms": r[6],
                "created_at": _iso(r[7]),
            }
            for r in metrics
        ],
        "turn_events": [
            {
                "session_id": str(r[0]) if r[0] else None,
                "request_id": r[1],
                "seq": r[2],
                "kind": r[3],
                "stage": r[4],
                "message": r[5],
                "detail": r[6],
                "elapsed_ms": r[7],
                "created_at": _iso(r[8]),
            }
            for r in turns
        ],
        # HTTP 요청 로그와 파이프라인 단계 로그는 성격이 달라 화면에서도 나눠 보여준다.
        # 한 번 조회한 결과를 나눌 뿐, 쿼리를 두 번 돌리지 않는다.
        "rag_logs": [_rag_row(r) for r in rag_all if r[2] == "http_request"],
        "rag_events": [_rag_row(r) for r in rag_all if r[2] != "http_request"],
    }


def fetch_metrics_daily(
    days: int = 7,
    method: str | None = None,
    path: str | None = None,
    limit: int | None = None,
) -> dict:
    """일별 요청 성능 롤업 — 백엔드 `GET /api/admin/metrics-daily`와 동일한 반환 형태.

    limit은 백엔드에 없는 인자다. 백엔드는 days만 걸고 전량을 내보내는데, 실제 관측 DB에서
    days=7이 이미 15,000행이 넘는다(일자×method×path 조합이라 금세 불어난다). 화면은
    limit을 주고, 기본값 None이면 백엔드와 똑같이 전량이라 계약은 그대로다.
    """
    days = max(1, min(int(days), 90))
    where = ["day >= %s"]
    params: list[Any] = [_today() - timedelta(days=days)]
    if method:
        where.append("method = %s")
        params.append(method.upper())
    if path:
        where.append("path like %s")
        params.append(f"%{path}%")

    sql = (
        "select day, method, path, calls, err_4xx, err_5xx, p50_ms, p95_ms, avg_ms, max_ms "
        "from metrics_daily "
        f"where {' and '.join(where)} "
        "order by day desc, calls desc"
    )
    if limit is not None:
        sql += " limit %s"
        params.append(_clamp(limit, _MAX_LIMIT_METRICS))
    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "rows": [
            {
                "day": _iso(r[0]),
                "method": r[1],
                "path": r[2],
                "calls": r[3],
                "err_4xx": r[4],
                "err_5xx": r[5],
                "p50_ms": r[6],
                "p95_ms": r[7],
                "avg_ms": r[8],
                "max_ms": r[9],
            }
            for r in rows
        ]
    }


def fetch_usage_daily(
    days: int = 30,
    component: str | None = None,
    model: str | None = None,
    limit: int | None = None,
) -> dict:
    """일별 LLM 사용량 롤업 — 백엔드 `GET /api/admin/usage-daily`와 동일한 반환 형태.

    limit은 fetch_metrics_daily와 같은 이유로 추가한 인자다(기본 None이면 백엔드와 동일).
    """
    days = max(1, min(int(days), 365))
    where = ["day >= %s"]
    params: list[Any] = [_today() - timedelta(days=days)]
    if component:
        where.append("component = %s")
        params.append(component)
    if model:
        where.append("model = %s")
        params.append(model)

    sql = (
        "select day, component, purpose, model, calls, input_tokens, output_tokens, cost_usd "
        "from usage_daily "
        f"where {' and '.join(where)} "
        "order by day desc"
    )
    if limit is not None:
        sql += " limit %s"
        params.append(_clamp(limit, _MAX_LIMIT_METRICS))
    with _cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {
        "rows": [
            {
                "day": _iso(r[0]),
                "component": r[1],
                "purpose": r[2],
                "model": r[3],
                "calls": r[4],
                "input_tokens": r[5],
                "output_tokens": r[6],
                "cost_usd": round(float(r[7]), 6) if r[7] is not None else None,
            }
            for r in rows
        ]
    }
