"""조회 엔드포인트가 **관측 DB를 직접** 읽는지 검증한다.

## 왜 이 파일이 새로 필요했나

읽기 경로를 사본(JSONL) → 관측 DB 직접으로 전부 갈아끼웠는데 기존 테스트 50개가 하나도
깨지지 않았다. 이 경로를 검증하는 테스트가 애초에 없었다는 뜻이고, 그 초록불은 이 변경에
대해 아무 말도 하지 않는다. 그래서 계약을 여기에 못 박는다.

## 무엇을 못 박나

1. **사본이 아니라 DB를 읽는다** — 사본은 컨테이너에서 재시작마다 사라진다. 화면이 사본을
   읽던 구조에서는 배포 후 "수집 버튼을 누르기 전까지 빈 화면"이었다.
2. **미구성이면 503, 사본으로 조용히 되돌아가지 않는다** — 폴백하면 "직접 읽는 줄 알았는데
   실은 옛 사본을 보고 있는" 상태를 아무도 모른다.
3. **프론트가 보내는 파라미터 이름이 그대로 먹는다**(path_contains 등) — 화면 무변경 전제.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.observability import obs_db


class ObservabilityReadPathTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    # --- 1. 사본이 아니라 DB를 읽는다 ---

    @patch("app.main.obs_db.fetch_audit_logs")
    @patch("app.main.obs_log_store.load_audit_logs")
    def test_audit_logs_reads_db_not_local_copy(self, load_copy, fetch_db):
        fetch_db.return_value = {"logs": [{"request_id": "r-1"}]}

        response = self.client.get("/observability/audit-logs", params={"limit": 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"request_id": "r-1"}])
        load_copy.assert_not_called()  # 사본은 건드리지 않는다

    @patch("app.main.obs_db.fetch_turn_events")
    @patch("app.main.obs_log_store.load_turn_events")
    def test_turn_events_reads_db_not_local_copy(self, load_copy, fetch_db):
        fetch_db.return_value = {"events": [{"seq": 1}]}

        response = self.client.get("/observability/turn-events")

        self.assertEqual(response.json(), [{"seq": 1}])
        load_copy.assert_not_called()

    @patch("app.main.obs_db.fetch_rag_events")
    @patch("app.main.obs_log_store.load_rag_events")
    def test_rag_events_reads_db_and_forwards_event_filter(self, load_copy, fetch_db):
        fetch_db.return_value = {"events": []}

        self.client.get("/observability/rag-events", params={"event": "hybrid_search"})

        load_copy.assert_not_called()
        # event는 백엔드 admin API엔 없고 사본 조회가 주던 축이다 — 직접 조회에서도 살아야
        # 화면이 그대로 동작한다.
        self.assertEqual(fetch_db.call_args.kwargs["event"], "hybrid_search")

    # --- 2. 프론트 파라미터 이름이 그대로 먹는다 ---

    @patch("app.main.obs_db.fetch_request_metrics")
    def test_path_contains_maps_to_db_path_filter(self, fetch_db):
        """프론트는 path_contains로 보내고 obs_db는 path로 받는다 — 여기서 이름이 어긋나면
        필터가 조용히 무시돼 화면이 전량을 보여준다(틀린 화면인데 에러가 안 난다)."""
        fetch_db.return_value = {"rows": []}

        self.client.get(
            "/observability/request-metrics", params={"path_contains": "/api/sessions"}
        )

        self.assertEqual(fetch_db.call_args.kwargs["path"], "/api/sessions")

    @patch("app.main.obs_db.fetch_metrics_daily")
    def test_metrics_daily_defaults_to_widest_window(self, fetch_db):
        """days는 새로 생긴 인자라 프론트가 보내지 않는다. 기본값이 좁으면 기존 화면이
        보던 데이터가 소리 없이 줄어든다."""
        fetch_db.return_value = {"rows": []}

        self.client.get("/observability/metrics-daily")

        self.assertEqual(fetch_db.call_args.kwargs["days"], 90)

    @patch("app.main.obs_db.fetch_usage_daily")
    def test_usage_daily_defaults_to_widest_window(self, fetch_db):
        fetch_db.return_value = {"rows": []}

        self.client.get("/observability/usage-daily")

        self.assertEqual(fetch_db.call_args.kwargs["days"], 365)

    # --- 3. 조용한 폴백 금지 ---

    @patch("app.main.obs_db.fetch_audit_logs")
    @patch("app.main.obs_log_store.load_audit_logs")
    def test_unavailable_db_returns_503_instead_of_serving_stale_copy(self, load_copy, fetch_db):
        """503을 내야 운영이 구성 오류를 본다. 사본을 대신 내주면 화면은 멀쩡해 보이는데
        데이터는 옛것이다 — 백엔드에서 조용한 폴백이 장애를 숨긴 사례가 이미 둘 있었다."""
        fetch_db.side_effect = obs_db.ObservabilityDBUnavailable("미설정")

        response = self.client.get("/observability/audit-logs")

        self.assertEqual(response.status_code, 503)
        load_copy.assert_not_called()

    @patch("app.main.obs_db.fetch_turn_events")
    def test_malformed_session_id_is_400_not_500(self, fetch_db):
        """백엔드의 400 INVALID_ID에 대응 — 형식 오류가 서버 오류로 둔갑하지 않는다."""
        fetch_db.side_effect = ValueError("session_id 형식이 올바르지 않습니다.")

        response = self.client.get(
            "/observability/turn-events", params={"session_id": "not-a-uuid"}
        )

        self.assertEqual(response.status_code, 400)

    # --- 4. 화면이 상태를 알 수 있어야 한다 ---

    def test_status_exposes_direct_read_configuration(self):
        """미구성이면 화면이 배너를 띄워야 한다 — 503만 보고 원인을 짐작하게 두지 않는다."""
        response = self.client.get("/observability/status")

        self.assertIn("obs_db_configured", response.json())


if __name__ == "__main__":
    unittest.main()
