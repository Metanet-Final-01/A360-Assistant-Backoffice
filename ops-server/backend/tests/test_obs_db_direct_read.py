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


def test_limit_is_clamped_to_max(monkeypatch):
    """상한을 넘겨도 전체 스캔이 되지 않게 잘린다(백엔드 le=500과 동일)."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(limit=99999)
    _, params = [x for x in cur.executed if "from audit_logs" in x[0]][0]
    assert params[-1] == 500


def test_filters_are_parameterized_not_interpolated(monkeypatch):
    """필터는 바인드 파라미터로 — 문자열 보간이면 SQL 인젝션 면이 생긴다."""
    cur = _install_cursor(monkeypatch, [])
    obs_db.fetch_audit_logs(method="post", status_code=500, user_id="u-9", limit=5)
    sql, params = [x for x in cur.executed if "from audit_logs" in x[0]][0]
    assert "%s" in sql and "u-9" not in sql          # 값이 SQL에 박히지 않았다
    assert "POST" in params and 500 in params and "u-9" in params  # 값은 파라미터로


# --- 3. probe: 도달성이 아니라 실제 조회를 본다 ---

def test_probe_runs_real_query_not_just_connect(monkeypatch):
    """health가 '연결됨'만 보고 속지 않게, 실제 SELECT까지 돌린다 (RPA-249 교훈)."""
    cur = _install_cursor(monkeypatch, [(42,)])
    out = obs_db.probe()
    assert out == {"ok": True, "audit_logs_rows": 42}
    assert any("select count(*) from audit_logs" in s for s, _ in cur.executed)


def test_cursor_forces_read_only_transaction(monkeypatch):
    """읽기 전용 롤이 전제지만, 롤이 잘못 발급돼도 쓰기가 나가지 않게 한 겹 더 막는다."""
    import types

    calls: list[str] = []

    class _C:
        def execute(self, sql, params=None):
            calls.append(sql)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _C()

        def close(self):
            pass

    monkeypatch.setenv("A360_OBSERVABILITY_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setitem(
        sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: _Conn())
    )
    with obs_db._cursor():
        pass
    assert any("default_transaction_read_only = on" in c for c in calls)
    assert any("statement_timeout" in c for c in calls)
