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
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# 읽기 전용 롤 크레덴셜 — CFN이 Secrets Manager에서 주입한다(콘솔 수동 주입 금지).
# 백엔드의 OBSERVABILITY_DATABASE_URL과 **다른 롤**이다: 이쪽은 SELECT 권한만 가진다.
_ENV_KEY = "A360_OBSERVABILITY_DATABASE_URL"

# 조회 상한 — 화면·수집기가 실수로 전체 스캔을 걸지 않게. admin API의 le=500과 맞춘다.
_MAX_LIMIT = 500
_STATEMENT_TIMEOUT_MS = 10_000


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
    """
    import psycopg

    try:
        conn = psycopg.connect(_dsn(), connect_timeout=10)
    except ObservabilityDBUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — 연결 실패도 '직접 조회 불가'로 통일해 올린다
        raise ObservabilityDBUnavailable(f"관측 DB 연결 실패: {type(e).__name__}") from e
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
            # 읽기 전용 롤이 기대지만, 롤이 잘못 발급돼도 쓰기가 나가지 않게 한 겹 더 막는다.
            cur.execute("SET default_transaction_read_only = on")
            yield cur
    finally:
        conn.close()


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def probe() -> dict:
    """직접 조회가 실제로 되는지 확인 — health가 '도달성'만 보고 속지 않게 (RPA-249 교훈).

    백엔드에서 OpenSearch가 '도달 ok'인데 색인이 비어 검색이 반쪽이던 일이 있었다.
    여기선 실제 SELECT를 한 번 돌려 권한·스키마까지 확인한다.
    """
    with _cursor() as cur:
        cur.execute("select count(*) from audit_logs")
        (n,) = cur.fetchone()
    return {"ok": True, "audit_logs_rows": int(n)}


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
        where.append("user_id = %s")
        params.append(user_id)

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
