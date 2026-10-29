from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@patch("app.main.check_database_connection", return_value=True)
def test_health_check_connected(mock_check_db):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "ai-hiring-backend",
        "database": "connected",
    }
    mock_check_db.assert_called_once()


@patch("app.main.check_database_connection", return_value=False)
def test_health_check_disconnected(mock_check_db):
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "service": "ai-hiring-backend",
        "database": "disconnected",
    }
    mock_check_db.assert_called_once()
