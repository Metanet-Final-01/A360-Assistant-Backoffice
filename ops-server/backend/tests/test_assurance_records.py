import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.observability import backend_client


class AssuranceRecordsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.backend_client.fetch_assurance_records")
    def test_list_forwards_only_read_filters(self, fetch):
        fetch.return_value = {"receipts": [], "next_cursor": None}

        response = self.client.get(
            "/assurance/records",
            params={
                "limit": 25,
                "harness": "output",
                "decision": "unassured",
                "since": "2026-07-01T00:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"receipts": [], "next_cursor": None})
        fetch.assert_called_once_with(
            limit=25,
            harness="output",
            decision="unassured",
            assurance_verdict=None,
            request_id=None,
            session_id=None,
            since="2026-07-01T00:00:00+00:00",
            cursor=None,
        )

    @patch("app.main.backend_client.fetch_assurance_record_detail")
    def test_detail_rejects_non_digest_before_backend_call(self, fetch):
        response = self.client.get("/assurance/records/not-a-digest")

        self.assertEqual(response.status_code, 400)
        fetch.assert_not_called()

    @patch("app.main.backend_client.fetch_assurance_record_detail")
    def test_detail_preserves_backend_not_found(self, fetch):
        fetch.side_effect = backend_client.BackendResponseError(404, "record not found")
        digest = "sha256:" + "a" * 64

        response = self.client.get(f"/assurance/records/{digest}")

        self.assertEqual(response.status_code, 404)

    @patch("app.main.backend_client.fetch_assurance_records")
    def test_list_preserves_backend_rate_limit(self, fetch):
        fetch.side_effect = backend_client.BackendResponseError(429, "rate limited")

        response = self.client.get("/assurance/records")

        self.assertEqual(response.status_code, 429)

    @patch("app.main.backend_client.fetch_assurance_records")
    def test_list_masks_backend_server_error_as_bad_gateway(self, fetch):
        fetch.side_effect = backend_client.BackendResponseError(500, "Backend server error")

        response = self.client.get("/assurance/records")

        self.assertEqual(response.status_code, 502)

    def test_assurance_routes_are_read_only(self):
        methods = {
            method
            for route in app.routes
            if getattr(route, "path", "").startswith("/assurance/records")
            for method in getattr(route, "methods", set())
        }

        self.assertEqual(methods, {"GET"})


class AssuranceBackendClientTest(unittest.TestCase):
    @patch("app.observability.backend_client._authed_get")
    def test_list_omits_unset_parameters(self, authed_get):
        authed_get.return_value = {"receipts": [], "next_cursor": None}

        backend_client.fetch_assurance_records(limit=50, harness="output", cursor="next")

        authed_get.assert_called_once_with(
            "/api/admin/assurance-receipts",
            {"limit": 50, "harness": "output", "cursor": "next"},
        )


if __name__ == "__main__":
    unittest.main()
