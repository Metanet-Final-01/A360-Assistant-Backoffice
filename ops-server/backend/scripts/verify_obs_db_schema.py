"""obs_db의 raw SQL을 **실제 관측 DB 스키마**에 걸어보는 스모크.

## 왜 별도로 있나

obs_db는 백엔드 ORM이 아니라 raw SQL이라, 컬럼명 한 글자만 틀려도 죽는다. 그런데
단위 테스트는 커서를 가짜로 갈아끼우므로 **문법·컬럼명 오류를 원리적으로 못 잡는다** —
초록불이 "쿼리가 맞다"는 뜻이 아니다. 그래서 실제 DB에 한 번 거는 이 스크립트가 있다.
크레덴셜이 필요해 CI에서는 못 돌리므로, 스키마를 건드리는 변경 때 수동으로 돌린다.

## 실행

    A360_OBSERVABILITY_DATABASE_URL=... python scripts/verify_obs_db_schema.py

크레덴셜은 출력하지 않는다. 성공 시 종료코드 0, 하나라도 실패하면 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability import obs_db  # noqa: E402

# 형식만 맞는 더미 — 세션 경로의 서브쿼리 문법을 확인하는 게 목적이라 0행이어도 성공이다.
_DUMMY_SESSION = "11111111-2222-3333-4444-555555555555"

CHECKS = [
    ("probe", lambda: obs_db.probe()),
    ("audit_logs", lambda: obs_db.fetch_audit_logs(limit=3)),
    ("audit_logs(since)", lambda: obs_db.fetch_audit_logs(since="2026-01-01T00:00:00Z", limit=3)),
    ("request_metrics", lambda: obs_db.fetch_request_metrics(limit=3, path="/api")),
    (
        "request_metrics(since)",
        lambda: obs_db.fetch_request_metrics(since="2026-01-01T00:00:00Z", limit=3),
    ),
    ("rag_events", lambda: obs_db.fetch_rag_events(limit=3)),
    ("turn_events", lambda: obs_db.fetch_turn_events(limit=3)),
    # 세션 지정 경로는 서브쿼리라 SQL 모양이 다르다 — 여기서만 검증된다.
    ("turn_events(session)", lambda: obs_db.fetch_turn_events(session_id=_DUMMY_SESSION, limit=3)),
    ("llm_usage_stats", lambda: obs_db.fetch_llm_usage_stats(days=30, group_by="component")),
    (
        "llm_usage_stats(session)",
        lambda: obs_db.fetch_llm_usage_stats(days=30, group_by="session"),
    ),
    ("metrics_daily", lambda: obs_db.fetch_metrics_daily(days=7)),
    ("usage_daily", lambda: obs_db.fetch_usage_daily(days=30)),
    # 사건 추적은 축마다 SQL 모양이 달라(요청 축은 = any(...), 대화 축은 uuid 캐스팅)
    # 셋을 따로 건다 — 한 축만 확인하면 나머지 축의 문법 오류를 못 잡는다.
    ("trace(request_id)", lambda: obs_db.trace_by(request_id="verify-probe")),
    ("trace(user_id)", lambda: obs_db.trace_by(user_id=_DUMMY_SESSION)),
    ("trace(session_id)", lambda: obs_db.trace_by(session_id=_DUMMY_SESSION)),
]


def _row_count(out: dict) -> int:
    for key in ("logs", "rows", "events", "breakdown"):
        if key in out:
            return len(out[key])
    if "matched_request_ids" in out:  # trace — 종류별 합계
        return sum(len(v) for v in out.values() if isinstance(v, list))
    return int(out.get("audit_logs_rows", 0))


def _check_writes_are_blocked() -> bool:
    """쓰기가 실제로 막히는지 — 가드가 '있다고 주장하는 것'과 '실제로 막는 것'은 다르다.

    처음 구현은 커서에서 `SET default_transaction_read_only = on`을 실행했는데, 그 설정은
    다음 트랜잭션의 기본값이라 이미 열린 트랜잭션엔 적용되지 않았고 CREATE TEMP TABLE이
    그대로 통과했다. 단위 테스트로는 잡히지 않는 종류라 여기서 실물로 확인한다.
    TEMP 테이블이라 혹 성공하더라도 세션 한정이고 DB에 남지 않는다.
    """
    try:
        with obs_db._cursor() as cur:
            cur.execute("create temp table _obs_readonly_probe (x int)")
    except Exception:  # noqa: BLE001 — 무엇으로 막히든 '막혔다'가 확인하려는 바다
        print(f"  OK   {'writes blocked':26s} (read-only 강제됨)")
        return True
    print(f"  FAIL {'writes blocked':26s} 쓰기가 통과했다 — read-only 가드가 무효다")
    return False


def main() -> int:
    if not obs_db.configured():
        print("A360_OBSERVABILITY_DATABASE_URL이 없습니다 — 관측 DB 크레덴셜을 주입하고 실행하세요.")
        return 2

    failed = 0
    for name, fn in CHECKS:
        try:
            print(f"  OK   {name:26s} rows={_row_count(fn())}")
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 계약 위반으로 보고한다
            failed += 1
            print(f"  FAIL {name:26s} {type(e).__name__}: {e}")

    if not _check_writes_are_blocked():
        failed += 1

    print("FAILED" if failed else "ALL OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
