"""남은 사본 3종(사건 추적·RAG 요청 로그·비용 리포트)이 관측 DB를 직접 읽는지 검증한다 (RPA-256).

사본(JSONL)은 컨테이너 파일시스템이라 배포에서 재시작마다 사라진다. 특히 사건 추적은
**장애 원인을 좇는 화면**이라, 정확히 관측이 필요한 순간에 비어 있는 게 문제였다.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.observability import obs_db


class TraceReadPathTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.obs_db.trace_by")
    @patch("app.main.obs_log_store.trace_by")
    def test_trace_reads_db_not_local_copy(self, copy_trace, db_trace):
        db_trace.return_value = {"matched_request_ids": ["r-1"], "audit_logs": []}

        response = self.client.get("/observability/trace", params={"request_id": "r-1"})

        self.assertEqual(response.status_code, 200)
        copy_trace.assert_not_called()
        self.assertEqual(db_trace.call_args.kwargs["request_id"], "r-1")

    def test_trace_still_requires_at_least_one_axis(self):
        """축이 하나도 없으면 전량 조회가 되어선 안 된다 — 기존 계약 유지."""
        self.assertEqual(self.client.get("/observability/trace").status_code, 400)

    @patch("app.main.obs_db.trace_by")
    def test_trace_malformed_session_id_is_400(self, db_trace):
        db_trace.side_effect = ValueError("session_id 형식이 올바르지 않습니다.")

        response = self.client.get("/observability/trace", params={"session_id": "nope"})

        self.assertEqual(response.status_code, 400)

    @patch("app.main.obs_db.trace_by")
    @patch("app.main.obs_log_store.trace_by")
    def test_trace_unavailable_db_is_503_not_stale_copy(self, copy_trace, db_trace):
        """503을 내야 운영이 구성 오류를 본다. 사본을 대신 내주면 화면은 멀쩡해 보이는데
        데이터는 옛것이다 — 장애 조사 중에는 특히 위험하다."""
        db_trace.side_effect = obs_db.ObservabilityDBUnavailable("미설정")

        response = self.client.get("/observability/trace", params={"request_id": "r-1"})

        self.assertEqual(response.status_code, 503)
        copy_trace.assert_not_called()


class RagLogsReadPathTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.obs_db.fetch_rag_events")
    @patch("app.main.obs_log_store.load_rag_logs")
    def test_rag_logs_reads_http_request_events_from_db(self, load_copy, fetch_db):
        """rag-logs가 파일에서 긁어오던 데이터는 이미 rag_events에 event='http_request'로
        중앙화돼 있었다(RPA-128). 사본을 따로 쌓을 이유가 없다."""
        fetch_db.return_value = {"events": [{"id": 1, "event": "http_request"}]}

        response = self.client.get("/observability/rag-logs", params={"limit": 5})

        self.assertEqual(response.json(), [{"id": 1, "event": "http_request"}])
        load_copy.assert_not_called()
        self.assertEqual(fetch_db.call_args.kwargs["event"], "http_request")

    @patch("app.main.obs_db.fetch_rag_events")
    def test_out_of_range_limit_is_rejected_not_silently_clamped(self, fetch_db):
        """범위를 벗어난 limit이 통과되면 obs_db에서만 조용히 잘려, 호출자는 "요청한 만큼
        받았다"고 오해한다. 같은 파일의 다른 observability GET들은 Query(ge/le)로 범위를
        명시하고 있어 계약도 어긋났다."""
        response = self.client.get("/observability/rag-logs", params={"limit": 999999})

        self.assertEqual(response.status_code, 422)
        fetch_db.assert_not_called()

    @patch("app.main.obs_db.fetch_rag_events")
    def test_path_contains_is_rejected_not_silently_ignored(self, fetch_db):
        """rag_events에는 경로 컬럼이 없다. 그냥 무시하면 필터를 줬는데 전량이 돌아와
        화면은 멀쩡해 보이는데 결과가 틀린다 — 에러조차 나지 않는다."""
        response = self.client.get(
            "/observability/rag-logs", params={"path_contains": "/api/rag"}
        )

        self.assertEqual(response.status_code, 400)
        fetch_db.assert_not_called()


class LlmUsageStatsReadPathTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.obs_db.fetch_llm_usage_stats")
    def test_stats_are_computed_directly_without_collect(self, fetch_db):
        """수집 → 사본 저장 → 최신 1건 조회 3단이었는데, 화면이 쓰는 건 '지금 집계' 한 건뿐이라
        직접 조회 한 번으로 줄였다."""
        fetch_db.return_value = {"period_days": 7, "group_by": "user", "total": {}, "breakdown": []}

        response = self.client.get(
            "/observability/llm-usage/stats",
            params={"days": 7, "group_by": "user", "order_by": "cost"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group_by"], "user")
        # order_by가 서버까지 전달돼야 한다 — 여기서 끊기면 화면은 비용 상위를 요청했는데
        # 서버는 호출 수 상위를 잘라 보내고, 비용 1위가 조용히 빠진다.
        self.assertEqual(
            fetch_db.call_args.kwargs, {"days": 7, "group_by": "user", "order_by": "cost"}
        )

    @patch("app.main.obs_db.fetch_llm_usage_stats")
    def test_invalid_group_by_is_400(self, fetch_db):
        """group_by는 식별자라 바인드가 불가능하다 — 화이트리스트 밖은 400으로 막힌다."""
        fetch_db.side_effect = ValueError("group_by는 ... 중 하나여야 합니다")

        response = self.client.get(
            "/observability/llm-usage/stats", params={"group_by": "drop table"}
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
