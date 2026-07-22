"""관측 DB 직접 조회 계층 테스트.

핵심은 두 가지다:
1. **조용한 폴백 금지** — 크레덴셜이 없으면 백엔드 경유로 되돌아가지 않고 명시적으로 실패한다.
   백엔드에서 조용한 폴백이 사고를 숨긴 사례가 이미 둘 있었다(OPENSEARCH_HOST 빈 값,
   RAG_DATABASE_URL 미주입). 둘 다 지표는 정상이었고 아무도 몰랐다.
2. **반환 형태가 백엔드 admin API와 동일** — 그래야 collector·views를 안 고치고 갈아끼운다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability import obs_db  # noqa: E402


class _FakeCursor:
    """psycopg 커서 흉내 — 실행된 SQL/파라미터를 붙잡아 계약을 검증한다."""

    def __init__(self, rows):
        self._rows = rows
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_cursor(monkeypatch, rows):
    cur = _FakeCursor(rows)

    class _Ctx:
        def __enter__(self):
            return cur

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(obs_db, "_cursor", lambda: _Ctx())
    return cur


# --- 1. 조용한 폴백 금지 ---

def test_missing_credential_raises_instead_of_falling_back(monkeypatch):
    """크레덴셜 미설정이면 예외 — 백엔드 경유로 조용히 되돌아가지 않는다."""
    monkeypatch.delenv("A360_OBSERVABILITY_DATABASE_URL", raising=False)
    assert obs_db.configured() is False
    with pytest.raises(obs_db.ObservabilityDBUnavailable) as e:
        obs_db._dsn()
    assert "폴백하지 않습니다" in str(e.value)  # 의도가 메시지에 남아 있어야 한다


def test_blank_credential_is_also_unavailable(monkeypatch):
    """공백만 있는 값도 미설정으로 본다 — .env에 키만 남은 흔한 상태."""
    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "   ")
    assert obs_db.configured() is False
    with pytest.raises(obs_db.ObservabilityDBUnavailable):
        obs_db._dsn()


def test_sqlalchemy_style_url_is_normalized(monkeypatch):
    """백엔드 관측 URL(postgresql+psycopg://)을 복붙해도 psycopg가 읽게 정규화한다."""
    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    assert obs_db._dsn().startswith("postgresql://")
    assert "+psycopg" not in obs_db._dsn()


# --- 2. 백엔드 admin API와 동일한 반환 형태 ---

def test_audit_logs_shape_matches_backend_contract(monkeypatch):
    """{"logs": [...]} 형태와 필드명이 백엔드 /api/admin/audit-logs와 같아야
    collector·views를 고치지 않고 갈아끼울 수 있다."""
    from datetime import datetime, timezone

    ts = datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)
    _install_cursor(monkeypatch, [("req-1", "u-1", "POST", "/api/x", 200, 12, ts)])

    out = obs_db.fetch_audit_logs(limit=10)
    assert set(out) == {"logs"}
    row = out["logs"][0]
    assert row == {
        "request_id": "req-1",
        "user_id": "u-1",
        "method": "POST",
        "path": "/api/x",
        "status_code": 200,
        "latency_ms": 12,
        "created_at": "2026-07-22T01:02:03+00:00",
    }


def test_since_switches_to_ascending_cursor_order(monkeypatch):
    """since가 있으면 오름차순 — 최신순이면 limit에 걸렸을 때 증분 수집이 중간을 빠뜨린다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(since="2026-07-22T00:00:00Z", limit=10)
    sql = [s for s, _ in cur.executed if "from audit_logs" in s][0]
    assert "created_at asc" in sql and "created_at > %s" in sql

    cur2 = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(limit=10)  # since 없음 → 화면 조회용 최신순
    sql2 = [s for s, _ in cur2.executed if "from audit_logs" in s][0]
    assert "created_at desc" in sql2


def test_ordering_has_a_tiebreaker(monkeypatch):
    """created_at만으로 정렬하면 같은 timestamp 행들의 순서가 비결정적이고, limit 경계에서
    반환 집합 자체가 새로고침마다 흔들린다. 두 테이블 모두 id를 tie-breaker로 둔다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(limit=10)
    assert "created_at desc, id desc" in _sql_of(cur, "audit_logs")

    cur2 = _install_cursor(monkeypatch, [])
    obs_db.fetch_request_metrics(limit=10)
    assert "created_at desc, id desc" in _sql_of(cur2, "request_metrics")


def test_limit_is_clamped_to_max(monkeypatch):
    """상한을 넘겨도 전체 스캔이 되지 않게 잘린다(백엔드 le=500과 동일)."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(limit=99999)
    _, params = [x for x in cur.executed if "from audit_logs" in x[0]][0]
    assert params[-1] == 500


_UID = "11111111-2222-3333-4444-555555555555"


def test_filters_are_parameterized_not_interpolated(monkeypatch):
    """필터는 바인드 파라미터로 — 문자열 보간이면 SQL 인젝션 면이 생긴다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(method="post", status_code=500, user_id=_UID, limit=5)
    sql, params = [x for x in cur.executed if "from audit_logs" in x[0]][0]
    assert "%s" in sql and _UID not in sql            # 값이 SQL에 박히지 않았다
    assert "POST" in params and 500 in params and _UID in params  # 값은 파라미터로


def test_malformed_user_id_is_input_error_not_db_failure(monkeypatch):
    """user_id는 DB에서 uuid 타입이다. 형식이 틀린 값을 그대로 넘기면 psycopg가
    InvalidTextRepresentation을 내고, 그게 '관측 DB 조회 실패'(503)로 둔갑해 운영이
    DB 장애를 의심하게 된다 — 실제로 화면 user_id 칸에 아무 값이나 넣으면 그랬다.
    입력 오류는 입력 오류로 보고해야 한다(400)."""
    _install_cursor(monkeypatch, [])
    with pytest.raises(ValueError):
        obs_db.fetch_audit_logs(user_id="abc")
    with pytest.raises(ValueError):
        obs_db.trace_by(user_id="abc")


def test_uuid_columns_are_cast_explicitly(monkeypatch):
    """uuid 컬럼 비교에 캐스팅이 빠지면 텍스트로 넘어가 타입 불일치가 난다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(user_id=_UID)
    sql = [s for s, _ in cur.executed if "from audit_logs" in s][0]
    assert "user_id = %s::uuid" in sql


# --- 2-b. 나머지 5종도 같은 계약을 지킨다 ---

def _sql_of(cur, table):
    return [s for s, _ in cur.executed if f"from {table}" in s][0]


def _params_of(cur, table):
    return [p for s, p in cur.executed if f"from {table}" in s][0]


def test_request_metrics_shape_and_cursor_rule(monkeypatch):
    """id를 포함해야 수집기가 중복 제거를 한다. since면 오름차순(백엔드와 같은 커서 규칙)."""
    from datetime import datetime, timezone

    ts = datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)
    cur = _install_cursor(monkeypatch, [(7, "req-1", "u-1", "GET", "/api/x", 200, 12, ts)])
    out = obs_db.fetch_request_metrics(limit=10)
    assert set(out) == {"rows"}
    assert out["rows"][0] == {
        "id": 7,
        "request_id": "req-1",
        "user_id": "u-1",
        "method": "GET",
        "path": "/api/x",
        "status_code": 200,
        "latency_ms": 12,
        "created_at": "2026-07-22T01:02:03+00:00",
    }
    assert "created_at desc" in _sql_of(cur, "request_metrics")

    cur2 = _install_cursor(monkeypatch, [])
    obs_db.fetch_request_metrics(since="2026-07-22T00:00:00Z")
    assert "created_at asc" in _sql_of(cur2, "request_metrics")


def test_metrics_limit_uses_its_own_ceiling_not_audit_ceiling(monkeypatch):
    """상한을 하나로 뭉뚱그리면 백오피스가 백엔드(le=2000)보다 적게 가져와 계약이 어긋난다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_request_metrics(limit=99999)
    assert _params_of(cur, "request_metrics")[-1] == 2000

    cur2 = _install_cursor(monkeypatch, [])
    obs_db.fetch_turn_events(limit=99999)
    assert _params_of(cur2, "turn_events")[-1] == 1000


def test_path_filter_is_partial_match_by_parameter(monkeypatch):
    """백엔드 .contains()와 같은 부분일치. 와일드카드는 파라미터에 넣어 SQL에 %를 안 박는다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_request_metrics(path="/api/sessions")
    sql, params = [x for x in cur.executed if "from request_metrics" in x[0]][0]
    assert "path like %s" in sql and "/api/sessions" not in sql
    assert "%/api/sessions%" in params


def test_rag_events_shape(monkeypatch):
    from datetime import datetime, timezone

    ts = datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)
    # detail은 DB에서 Text 컬럼이라 **문자열**로 온다(dict가 아니다). 백엔드가 적재 시점에
    # json.dumps + 마스킹을 끝내 넣기 때문 — 조회 시점 마스킹이 아니므로 직접 조회가
    # 마스킹을 우회하지 않는다(그래서 이 경로가 안전하다).
    cur = _install_cursor(
        monkeypatch, [(3, "req-1", "search", "hybrid_search", "ok", 42.0, '{"k": 1}', ts)]
    )
    out = obs_db.fetch_rag_events(limit=5, request_id="req-1")
    assert set(out) == {"events"}
    assert out["events"][0] == {
        "id": 3,
        "request_id": "req-1",
        "event": "search",
        "function": "hybrid_search",
        "status": "ok",
        "duration_ms": 42.0,
        "detail": '{"k": 1}',
        "created_at": "2026-07-22T01:02:03+00:00",
    }
    sql, params = [x for x in cur.executed if "from rag_events" in x[0]][0]
    assert "created_at desc, id desc" in sql
    assert "req-1" in params and "req-1" not in sql


def test_turn_events_picks_recent_by_time_then_orders_by_turn_progress(monkeypatch):
    """request_id는 uuid4라 시간과 무관하다 — (request_id, seq)에 바로 limit을 걸면 최신 턴이
    아니라 사전순으로 앞선 임의의 턴이 잘린다(백엔드가 CodeRabbit 지적으로 고친 지점).
    시간축으로 먼저 자르고, 화면 순서는 바깥에서 맞춘 형태여야 한다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_turn_events(session_id="11111111-2222-3333-4444-555555555555", limit=10)
    sql = _sql_of(cur, "turn_events")
    inner = sql.index("created_at desc, id desc")
    outer = sql.index("order by request_id, seq")
    assert inner < outer, "시간축 절단이 안쪽, 턴 순서 정렬이 바깥이어야 한다"


def test_turn_events_rejects_malformed_session_id(monkeypatch):
    """백엔드의 400 INVALID_ID에 대응 — 형식 오류가 조용히 전체 조회로 새지 않는다."""
    _install_cursor(monkeypatch, [])
    with pytest.raises(ValueError):
        obs_db.fetch_turn_events(session_id="not-a-uuid")


def test_llm_usage_group_by_is_whitelisted(monkeypatch):
    """group_by는 식별자라 바인드가 불가능하다 — 화이트리스트가 유일한 방어다."""
    _install_cursor(monkeypatch, [])
    with pytest.raises(ValueError):
        obs_db.fetch_llm_usage_stats(group_by="component; drop table llm_usage --")


class _ScriptedCursor(_FakeCursor):
    """집계와 breakdown이 별개 쿼리라, fetchone/fetchall에 다른 결과를 주는 커서."""

    def __init__(self, total_row, breakdown_rows):
        super().__init__(breakdown_rows)
        self._total_row = total_row

    def fetchone(self):
        return self._total_row


def _install_scripted(monkeypatch, total_row, breakdown_rows):
    cur = _ScriptedCursor(total_row, breakdown_rows)

    class _Ctx:
        def __enter__(self):
            return cur

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(obs_db, "_cursor", lambda: _Ctx())
    return cur


def test_llm_usage_total_is_the_whole_period_not_the_visible_rows(monkeypatch):
    """**합계는 잘린 내역의 합이 아니라 전체 집계다.**

    breakdown에 상한을 두면서 total을 그 합으로 두면 "이번 달 비용"이 조용히 축소돼
    보인다 — 숫자를 근거로 쓰는 화면에서 가장 나쁜 오류다. 전체는 30콜인데 화면에
    보이는 2행의 합은 3콜뿐인 상황을 만들어, total이 전체를 유지하는지 본다.
    """
    _install_scripted(
        monkeypatch,
        total_row=(30, 130, 60, 0.05, 7),  # calls, in, out, cost, group_count
        breakdown_rows=[("agent", 2, 10, 5, 0.001), ("rag", 1, 3, 1, 0.0005)],
    )

    out = obs_db.fetch_llm_usage_stats(days=7, group_by="component")

    assert out["period_days"] == 7 and out["group_by"] == "component"
    assert out["total"] == {
        "calls": 30,
        "input_tokens": 130,
        "output_tokens": 60,
        "cost_usd": 0.05,
    }
    assert sum(b["calls"] for b in out["breakdown"]) == 3  # 보이는 건 일부뿐
    assert out["breakdown"][0]["key"] == "agent"


def test_truncation_is_disclosed_not_hidden(monkeypatch):
    """합계는 전체인데 표는 일부다 — 말하지 않으면 "표를 더하면 합계"라고 오해한다."""
    _install_scripted(monkeypatch, (30, 130, 60, 0.05, 7), [("agent", 2, 10, 5, 0.001)])
    assert obs_db.fetch_llm_usage_stats(group_by="session")["breakdown_truncated"] is True

    _install_scripted(monkeypatch, (3, 13, 6, 0.0015, 1), [("agent", 3, 13, 6, 0.0015)])
    assert obs_db.fetch_llm_usage_stats(group_by="component")["breakdown_truncated"] is False


def test_group_count_includes_the_null_group(monkeypatch):
    """`count(distinct col)`은 NULL을 세지 않지만 GROUP BY는 NULL을 **한 그룹으로** 만든다.

    그냥 두면 group_count가 breakdown보다 작아져 truncated 판정이 뒤집히고(실제로는
    잘렸는데 "다 보여줬다"고 표시된다), 전체 그룹 수도 축소돼 보인다. 실 DB의 user 축에
    NULL 그룹이 실제로 있다 — 보정 전 47, GROUP BY 기준 실제 48.
    """
    cur = _install_scripted(monkeypatch, (0, 0, 0, 0.0, 0), [])
    obs_db.fetch_llm_usage_stats(group_by="user")
    sql = [s for s, _ in cur.executed if "count(distinct" in s][0]
    assert "filter (where user_id is null)" in sql


def test_truncation_criterion_is_chosen_by_the_caller(monkeypatch):
    """**무엇의 상위 N인지**는 화면이 정해야 한다.

    비용 리포트는 '가장 비싼' 축을 보는 화면인데 호출 수로 자르면, 호출은 적지만 비싼
    세션이 응답에서 통째로 빠진다. 화면은 받은 것만 정렬하므로 그 사실조차 드러나지
    않는다 — 비용 1위가 목록에 없는데 아무도 모른다.
    """
    cur = _install_scripted(monkeypatch, (0, 0, 0, 0.0, 0), [])
    obs_db.fetch_llm_usage_stats(group_by="session", order_by="cost")
    sql = [s for s, _ in cur.executed if "group by" in s][0]
    assert "order by coalesce(sum(cost_usd), 0.0) desc" in sql

    cur2 = _install_scripted(monkeypatch, (0, 0, 0, 0.0, 0), [])
    obs_db.fetch_llm_usage_stats(group_by="session")  # 기본은 백엔드와 같은 calls 기준
    assert "order by count(*) desc" in [s for s, _ in cur2.executed if "group by" in s][0]


def test_order_by_is_whitelisted(monkeypatch):
    """정렬식은 SQL에 그대로 들어가 바인드가 불가능하다 — group_by와 같은 방어가 필요하다."""
    _install_scripted(monkeypatch, (0, 0, 0, 0.0, 0), [])
    with pytest.raises(ValueError):
        obs_db.fetch_llm_usage_stats(order_by="cost_usd; drop table llm_usage --")


def test_breakdown_is_capped(monkeypatch):
    """user/session 축은 행 수가 사용자·세션 수만큼 늘어난다(실측 300+). 상한이 없으면
    응답 크기와 렌더링 비용이 선형으로 커진다."""
    cur = _install_scripted(monkeypatch, (0, 0, 0, 0.0, 0), [])
    obs_db.fetch_llm_usage_stats(group_by="session", limit=99999)
    sql, params = [x for x in cur.executed if "group by" in x[0]][0]
    assert "limit %s" in sql and params[-1] == 200


def test_daily_rollups_shape(monkeypatch):
    from datetime import date

    cur = _install_cursor(
        monkeypatch, [(date(2026, 7, 22), "GET", "/api/x", 10, 1, 0, 5, 9, 6.0, 20)]
    )
    out = obs_db.fetch_metrics_daily(days=7)
    assert out["rows"][0] == {
        "day": "2026-07-22",
        "method": "GET",
        "path": "/api/x",
        "calls": 10,
        "err_4xx": 1,
        "err_5xx": 0,
        "p50_ms": 5,
        "p95_ms": 9,
        "avg_ms": 6.0,
        "max_ms": 20,
    }
    assert "order by day desc, calls desc" in _sql_of(cur, "metrics_daily")

    cur2 = _install_cursor(
        monkeypatch, [(date(2026, 7, 22), "agent", "chat", "gpt-x", 3, 10, 5, 0.0012345678)]
    )
    out2 = obs_db.fetch_usage_daily(days=30, component="agent")
    assert out2["rows"][0] == {
        "day": "2026-07-22",
        "component": "agent",
        "purpose": "chat",
        "model": "gpt-x",
        "calls": 3,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.001235,
    }
    assert "agent" in _params_of(cur2, "usage_daily")


# --- 3. probe: 도달성이 아니라 실제 조회를 본다 ---

def test_probe_runs_real_query_not_just_connect(monkeypatch):
    """health가 '연결됨'만 보고 속지 않게, 실제 SELECT까지 돌린다 (RPA-249 교훈)."""
    cur = _install_cursor(monkeypatch, [(1,)])
    out = obs_db.probe()
    assert out == {"ok": True, "has_rows": True}
    assert any("from audit_logs" in s for s, _ in cur.executed)


def test_probe_does_not_scan_the_whole_table(monkeypatch):
    """확인하려는 건 '붙어서 읽히나'이지 행 수가 아니다. count(*)는 관측 DB가 커질수록
    풀스캔이 되어 statement_timeout에 걸리고, 그러면 프로브가 무거워서 실패한 것을
    'DB가 죽었다'로 오판하게 된다."""
    cur = _install_cursor(monkeypatch, [(1,)])
    obs_db.probe()
    sql = [s for s, _ in cur.executed if "audit_logs" in s][0]
    assert "count(*)" not in sql
    assert "limit 1" in sql


class _FakeConn:
    """실행 순서를 기록하는 연결 흉내 — read_only를 언제 걸었는지가 핵심이라 순서를 남긴다."""

    def __init__(self, events, execute_error=None):
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "_execute_error", execute_error)

    def __setattr__(self, key, value):
        self.events.append((key, value))

    def cursor(self):
        conn = self

        class _C:
            def execute(self, sql, params=None):
                conn.events.append(("execute", sql))
                if conn._execute_error is not None:
                    raise conn._execute_error

            def fetchall(self):
                return []

            def fetchone(self):
                return (0,)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _C()

    def close(self):
        self.events.append(("close", None))


def test_cursor_sets_read_only_before_any_statement(monkeypatch):
    """읽기 전용을 **첫 문장보다 먼저** 걸어야 실제로 막힌다.

    처음엔 커서에서 `SET default_transaction_read_only = on`을 실행했는데, 그 설정은
    '다음 트랜잭션'의 기본값이라 앞선 문장(statement_timeout)이 연 트랜잭션에는 적용되지
    않는다. 실제 DB에 걸어보니 그 상태에서 CREATE TEMP TABLE이 **통과했다** — 문서에는
    "한 겹 더 막는다"고 적혀 있었지만 아무것도 막지 않고 있었다.

    이전 테스트는 'SET 문이 실행됐다'만 확인해서 그 구멍을 그대로 통과시켰다(가짜 초록불).
    그래서 존재가 아니라 **순서**를 본다.
    """
    import psycopg

    events: list[tuple] = []
    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn(events))

    with obs_db._cursor():
        pass

    keys = [k for k, _ in events]
    assert ("read_only", True) in events, "읽기 전용을 연결 속성으로 걸지 않았다"
    assert keys.index("read_only") < keys.index("execute"), (
        "read_only가 첫 문장보다 뒤면 이미 열린 트랜잭션엔 적용되지 않는다"
    )
    # 한 조회가 쿼리를 두 번 이상 던지는 경우(집계+내역, 사건 추적)가 있다. PostgreSQL
    # 기본은 READ COMMITTED라 **읽기 전용이어도 문장마다 새 스냅샷**을 본다 —
    # "읽기 전용이니 같은 스냅샷"이라고 적어뒀던 게 사실이 아니었다.
    assert "isolation_level" in keys, "REPEATABLE READ로 묶지 않으면 합계와 내역이 어긋난다"
    assert keys.index("isolation_level") < keys.index("execute")
    assert any("statement_timeout" in sql for k, sql in events if k == "execute")


def test_query_errors_become_the_single_unavailable_contract(monkeypatch):
    """스키마·권한 오류가 raw psycopg 예외로 새면 호출부는 500을 낸다.

    읽기 전용 롤을 새로 발급받는 구성에서 권한 부족·테이블 부재는 흔한 시나리오다.
    이 모듈의 단일 오류 계약으로 모아야 화면이 503("직접 조회 불가")으로 드러낸다.
    """
    import psycopg

    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *a, **k: _FakeConn([], execute_error=psycopg.errors.UndefinedTable("없음")),
    )

    with pytest.raises(obs_db.ObservabilityDBUnavailable):
        with obs_db._cursor() as cur:
            cur.execute("select 1 from nope")


def test_logic_bugs_are_not_disguised_as_db_unavailable(monkeypatch):
    """psycopg.Error로 좁힌 이유 — 우리 로직 버그까지 'DB 불가'로 둔갑하면 원인을 숨긴다."""
    import psycopg

    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn([]))

    with pytest.raises(KeyError):
        with obs_db._cursor():
            raise KeyError("우리 쪽 버그")


def test_redaction_removes_credentials_from_any_message(monkeypatch):
    """로그는 CloudWatch로 나가 보존된다 — 한 번 새면 회수할 수 없다.

    현재 psycopg 실패 모드에서는 DSN이 예외 메시지에 들어가지 않는 것을 확인했지만
    (연결 실패·잘못된 옵션·형식 오류·포트 오류 4종), psycopg 버전이나 다른 예외 경로는
    우리 통제 밖이라 미리 막는다. **DSN이 통째로 섞인 최악의 경우**를 가정해 검증한다.
    """
    dsn = "postgresql://someuser:SuperSecret123@db.example.com:5432/obs"
    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", dsn)

    assert "SuperSecret123" not in obs_db._redact(f'connection failed for "{dsn}"')
    assert "SuperSecret123" not in obs_db._redact("auth failed (password=SuperSecret123)")
    # 크레덴셜이 없는 메시지는 그대로 둔다 — 진단 정보까지 지우면 로그가 쓸모없어진다.
    plain = "connection to server at 127.0.0.1 port 5432 failed"
    assert obs_db._redact(plain) == plain


def test_connection_failure_is_logged_without_leaking_dsn(monkeypatch, caplog):
    """메시지는 최소로(크레덴셜 유출 방지), 원인은 로그로 — 안 그러면 '조회 불가'만 보이고
    왜인지는 아무도 모른다."""
    import psycopg

    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql://user:secret@h/db")
    monkeypatch.setattr(psycopg, "connect", _boom)

    with caplog.at_level("WARNING"):
        with pytest.raises(obs_db.ObservabilityDBUnavailable) as e:
            with obs_db._cursor():
                pass

    assert "secret" not in str(e.value)  # 사용자에게 보이는 메시지엔 크레덴셜이 없다
    assert any("OSError" in r.getMessage() for r in caplog.records)
