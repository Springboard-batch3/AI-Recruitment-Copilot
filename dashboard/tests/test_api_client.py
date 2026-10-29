import unittest
from unittest.mock import MagicMock, patch

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.config import DashboardConfig


class TestDashboardAPIClient(unittest.TestCase):
    def setUp(self):
        self.config = DashboardConfig(
            api_base_url="http://mock-api:8000",
            request_timeout_seconds=5.0,
        )
        self.client = DashboardAPIClient(config=self.config)

    @patch("httpx.Client.post")
    def test_exchange_ticket_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "mock-token-xyz",
            "token_type": "bearer",
            "expires_in_seconds": 1800,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "role": "RECRUITER",
            "organization_id": "00000000-0000-0000-0000-000000000002",
        }
        mock_post.return_value = mock_resp

        res = self.client.exchange_ticket("raw-ticket-123")
        self.assertEqual(res["access_token"], "mock-token-xyz")
        self.assertEqual(res["role"], "RECRUITER")

    @patch("httpx.Client.post")
    def test_exchange_ticket_consumed_or_invalid_raises_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "detail": {"code": "TICKET_CONSUMED", "message": "Ticket has already been consumed."}
        }
        mock_post.return_value = mock_resp

        with self.assertRaises(DashboardAPIError) as ctx:
            self.client.exchange_ticket("used-ticket")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.code, "TICKET_CONSUMED")
        self.assertIn("already been consumed", ctx.exception.message)

    @patch("httpx.Client.get")
    def test_get_overview_sends_bearer_header(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "metadata": {
                "generated_at": "2026-09-29T20:00:00Z",
                "organization_name": "Test Org",
                "data_status": "authoritative",
                "active_filters": {},
            },
            "published_jobs": 3,
            "draft_ready_jobs": 1,
            "submitted_applications": 12,
            "awaiting_review": 4,
            "interview_ready": 2,
            "active_interview_releases": 2,
            "completed_official_interviews": 2,
            "evaluations_available": 1,
        }
        mock_get.return_value = mock_resp

        data = self.client.get_overview(token="test-jwt-token")
        self.assertEqual(data.published_jobs, 3)
        self.assertEqual(data.submitted_applications, 12)

        # Verify Authorization header was sent
        args, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-jwt-token")

    @patch("httpx.Client.get")
    def test_get_401_expired_session_handling(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "detail": {"code": "TOKEN_EXPIRED", "message": "Dashboard session has expired."}
        }
        mock_get.return_value = mock_resp

        with self.assertRaises(DashboardAPIError) as ctx:
            self.client.get_overview(token="expired-token")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.code, "TOKEN_EXPIRED")


if __name__ == "__main__":
    unittest.main()
