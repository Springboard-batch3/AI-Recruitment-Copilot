import logging
from typing import Any, Dict, Optional
import httpx

from dashboard.config import DashboardConfig, get_config
from dashboard.models import (
    DashboardInterviewsData,
    DashboardJobsData,
    DashboardMatchesData,
    DashboardOverviewData,
    DashboardPipelineData,
    DashboardReadinessData,
)

logger = logging.getLogger("streamlit_dashboard.api_client")


class DashboardAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class DashboardAPIClient:
    """
    HTTP client for communicating exclusively with FastAPI dashboard endpoints.
    Never stores bearer token in client instance state; token is passed per request.
    Handles connection errors, timeouts, 401 session expiry, 403 forbidden, and converts to safe UI errors.
    """

    def __init__(self, config: Optional[DashboardConfig] = None):
        self.config = config or get_config()
        self.base_url = self.config.api_base_url.rstrip("/")
        self.timeout = httpx.Timeout(self.config.request_timeout_seconds, connect=5.0)

    def exchange_ticket(self, raw_ticket: str) -> Dict[str, Any]:
        """Exchanges single-use launch ticket for short-lived dashboard bearer token."""
        url = f"{self.base_url}/api/v1/dashboard/session/exchange"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json={"ticket": raw_ticket})
                if res.status_code == 200:
                    return res.json()
                elif res.status_code in {401, 403}:
                    detail = res.json().get("detail", {})
                    msg = detail.get("message", "Ticket is invalid, expired, or already consumed.")
                    code = detail.get("code", "INVALID_TICKET")
                    raise DashboardAPIError(msg, status_code=res.status_code, code=code)
                else:
                    raise DashboardAPIError("Failed to authenticate dashboard session with backend.", status_code=res.status_code)
        except httpx.ConnectError:
            raise DashboardAPIError("Cannot connect to local backend API at " + self.base_url + ". Ensure FastAPI is running.")
        except httpx.TimeoutException:
            raise DashboardAPIError("Backend session verification timed out. Please try launching again.")
        except DashboardAPIError:
            raise
        except Exception as e:
            raise DashboardAPIError(f"Unexpected connection failure: {str(e)}")

    def _get(self, endpoint: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/dashboard/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, headers=headers, params=clean_params)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 401:
                    detail = res.json().get("detail", {})
                    msg = detail.get("message", "Dashboard session has expired. Please return to workspace.")
                    code = detail.get("code", "SESSION_EXPIRED")
                    raise DashboardAPIError(msg, status_code=401, code=code)
                elif res.status_code == 403:
                    raise DashboardAPIError("Access forbidden for this analytics view.", status_code=403, code="FORBIDDEN")
                elif res.status_code == 404:
                    raise DashboardAPIError("Requested analytics resource not found.", status_code=404, code="NOT_FOUND")
                elif res.status_code == 422:
                    raise DashboardAPIError("Invalid analytics filters applied.", status_code=422, code="INVALID_FILTERS")
                else:
                    raise DashboardAPIError("Backend analytics service temporarily unavailable.", status_code=res.status_code)
        except httpx.ConnectError:
            raise DashboardAPIError("Backend service unreachable. Please ensure the backend is running.")
        except httpx.TimeoutException:
            raise DashboardAPIError("Analytics request timed out. Please retry.")
        except DashboardAPIError:
            raise
        except Exception as e:
            raise DashboardAPIError(f"Unable to load analytics data: {str(e)}")

    def get_overview(self, token: str) -> DashboardOverviewData:
        data = self._get("overview", token=token)
        return DashboardOverviewData.model_validate(data)

    def get_pipeline(self, token: str, job_id: Optional[str] = None, department: Optional[str] = None) -> DashboardPipelineData:
        params = {"job_id": job_id, "department": department}
        data = self._get("pipeline", token=token, params=params)
        return DashboardPipelineData.model_validate(data)

    def get_jobs(self, token: str, department: Optional[str] = None) -> DashboardJobsData:
        params = {"department": department}
        data = self._get("jobs", token=token, params=params)
        return DashboardJobsData.model_validate(data)

    def get_matches(
        self,
        token: str,
        job_id: Optional[str] = None,
        confidence_tier: Optional[str] = None,
        has_must_have_gaps: Optional[bool] = None,
    ) -> DashboardMatchesData:
        params = {
            "job_id": job_id,
            "confidence_tier": confidence_tier,
            "has_must_have_gaps": has_must_have_gaps,
        }
        data = self._get("matches", token=token, params=params)
        return DashboardMatchesData.model_validate(data)

    def get_interviews(
        self,
        token: str,
        job_id: Optional[str] = None,
        interview_mode: Optional[str] = None,
        session_status: Optional[str] = None,
    ) -> DashboardInterviewsData:
        params = {
            "job_id": job_id,
            "interview_mode": interview_mode,
            "session_status": session_status,
        }
        data = self._get("interviews", token=token, params=params)
        return DashboardInterviewsData.model_validate(data)

    def get_readiness(self, token: str) -> DashboardReadinessData:
        data = self._get("readiness", token=token)
        return DashboardReadinessData.model_validate(data)
