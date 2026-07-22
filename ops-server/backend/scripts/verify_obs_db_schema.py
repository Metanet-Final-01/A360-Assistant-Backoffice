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
]


def _row_count(out: dict) -> int:
    for key in ("logs", "rows", "events", "breakdown"):
        if key in out:
            return len(out[key])
    if "has_rows" in out:  # probe는 행 수를 세지 않는다(probe docstring 참고)
        return 1 if out["has_rows"] else 0
    return int(out.get("audit_logs_rows", 0))


def _check_writes_are_blocked() -> bool:
    """쓰기가 실제로 막히는지 — 가드가 '있다고 주장하는 것'과 '실제로 막는 것'은 다르다.

    처음 구현은 커서에서 `SET default_transaction_read_only = on`을 실행했는데, 그 설정은
    다음 트랜잭션의 기본값이라 이미 열린 트랜잭션엔 적용되지 않았고 CREATE TEMP TABLE이
    그대로 통과했다. 단위 테스트로는 잡히지 않는 종류라 여기서 실물로 확인한다.

    프로브로는 `SELECT ... FOR UPDATE`를 쓴다. 읽기 전용 트랜잭션이 거부하는 문장이면서
    **아무것도 만들지 않기** 때문이다 — 처음엔 CREATE TEMP TABLE을 썼는데, 세션 한정이라
    흔적은 안 남아도 ops-server가 관측 DB에 생성 문장을 내는 모양새가 된다(Qodo 지적).
    `show transaction_read_only`로 설정값을 읽는 방법도 있지만, 그건 '선언'을 확인할 뿐
    실제로 막히는지를 확인하지 못한다 — 애초에 그 착각 때문에 생긴 버그였다.
    """
    try:
        with obs_db._cursor() as cur:
            cur.execute("select 1 from audit_logs limit 1 for update")
    except obs_db.ObservabilityDBUnavailable as e:
        # **아무 예외나 '막혔다'로 세면 안 된다.** 연결 실패·권한 부족·테이블 부재도
        # 예외이므로, 그걸 성공으로 치면 가드가 무효인 채로도 초록불이 뜬다 —
        # 이 스크립트가 확인하려던 바로 그 착각을 스크립트가 되풀이하는 꼴이다.
        # 읽기 전용 트랜잭션이 거부했을 때만 통과로 인정한다.
        if "ReadOnlySqlTransaction" not in str(e):
            print(f"  FAIL {'writes blocked':26s} 다른 이유로 실패했다({e}) — 차단 여부 확인 불가")
            return False
        print(f"  OK   {'writes blocked':26s} (read-only 강제됨)")
        return True
    except Exception as e:  # noqa: BLE001 — 예상 밖 실패도 '확인 불가'로 보고한다
        print(f"  FAIL {'writes blocked':26s} 예상 밖 오류({type(e).__name__}) — 차단 여부 확인 불가")
        return False
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
