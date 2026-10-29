import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardConfig:
    api_base_url: str = os.getenv("DASHBOARD_API_BASE_URL", "http://localhost:8000")
    react_app_public_url: str = os.getenv("REACT_APP_PUBLIC_URL", "http://localhost:5173")
    request_timeout_seconds: float = float(os.getenv("DASHBOARD_REQUEST_TIMEOUT_SECONDS", "10.0"))


def get_config() -> DashboardConfig:
    return DashboardConfig()
