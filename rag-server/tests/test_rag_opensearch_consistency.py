"""RAG↔Bonsai(OpenSearch) 정합성 점검의 순수 비교 로직 테스트 (RPA-263).

compare()는 DB id 집합과 OS id 집합의 drift를 리포트로 만든다 — DB/OS 접속 없이 검증한다
(무거운 임포트는 main() 안에 지연시켜, 이 모듈 임포트만으로 compare를 테스트할 수 있다).
"""

from app.rag.scripts.check_rag_opensearch_consistency import (
    compare,
    fetch_db_ids,
    fetch_os_ids,
    parse_args,
)


class _FakeCursorCtx:
    def __init__(self, outer):
        self.outer = outer

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.outer.last_params = params  # last_id 커서가 넘어오는지 확인용

    def fetchall(self):
        return self.outer.pages.pop(0) if self.outer.pages else []


class _FakeConn:
    """id 커서 페이지네이션을 흉내내는 최소 conn — 미리 정해둔 페이지를 순서대로 돌려준다."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.last_params = None

    def cursor(self):
        return _FakeCursorCtx(self)


class _FakeIndices:
    def __init__(self, exists):
        self._exists = exists

    def exists(self, index):
        return self._exists


class _FakeOSClient:
    def __init__(self, exists):
        self.indices = _FakeIndices(exists)


def test_in_sync_when_sets_equal():
    r = compare({"a", "b", "c"}, {"a", "b", "c"}, {"jar": 2, "llm_agent": 1}, sample=20)
    assert r["in_sync"] is True
    assert r["db_total"] == 3 and r["os_total"] == 3 and r["in_both"] == 3
    assert r["db_only_count"] == 0 and r["os_only_count"] == 0
    assert r["db_only_sample"] == [] and r["os_only_sample"] == []
    assert r["db_by_source_type"] == {"jar": 2, "llm_agent": 1}


def test_db_only_drift_flags_missing_from_index():
    # DB엔 있는데 OS엔 없음 → BM25 검색 불가
    r = compare({"a", "b", "c"}, {"a"}, {}, sample=20)
    assert r["in_sync"] is False
    assert r["db_only_count"] == 2
    assert r["db_only_sample"] == ["b", "c"]  # 정렬됨
    assert r["os_only_count"] == 0
    assert r["in_both"] == 1


def test_os_only_drift_flags_orphans():
    # OS엔 있는데 DB엔 없음 → 죽은 hit(orphan)
    r = compare({"a"}, {"a", "x", "y"}, {"jar": 1}, sample=20)
    assert r["in_sync"] is False
    assert r["os_only_count"] == 2
    assert r["os_only_sample"] == ["x", "y"]
    assert r["db_only_count"] == 0


def test_both_directions_drift():
    r = compare({"a", "b"}, {"b", "c"}, {}, sample=20)
    assert r["in_sync"] is False
    assert r["db_only_sample"] == ["a"]
    assert r["os_only_sample"] == ["c"]
    assert r["in_both"] == 1


def test_sample_truncates_but_count_is_full():
    db_ids = {f"d{i:03d}" for i in range(50)}
    r = compare(db_ids, set(), {}, sample=5)
    assert r["db_only_count"] == 50            # 전체 개수는 그대로
    assert r["db_only_sample"] == ["d000", "d001", "d002", "d003", "d004"]  # 샘플만 잘림·정렬
    assert len(r["db_only_sample"]) == 5


def test_sample_zero_yields_empty_sample_but_keeps_count():
    r = compare({"a", "b"}, set(), {}, sample=0)
    assert r["db_only_count"] == 2
    assert r["db_only_sample"] == []


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.sample == 20 and ns.batch_size == 1000
    ns = parse_args(["--sample", "3", "--batch-size", "250"])
    assert ns.sample == 3 and ns.batch_size == 250


def test_fetch_db_ids_paginates_and_counts():
    conn = _FakeConn([
        [("a", "jar"), ("b", "jar")],
        [("c", "llm_agent")],
        # 빈 페이지에서 멈춘다
    ])
    ids, by_source = fetch_db_ids(conn, batch_size=2)
    assert ids == {"a", "b", "c"}
    assert by_source == {"jar": 2, "llm_agent": 1}
    assert conn.last_params == ("c", 2)  # 마지막 페이지의 last_id 커서가 넘어갔다


def test_fetch_os_ids_returns_none_when_index_missing():
    assert fetch_os_ids(_FakeOSClient(exists=False)) is None
