import pytest
from fastapi.testclient import TestClient


def test_live(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ready() -> bool:
        return True

    monkeypatch.setattr("app.api.routes.health.check_db_ready", _ready)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version(client: TestClient) -> None:
    response = client.get("/api/v1/system/version")
    assert response.status_code == 200
    assert response.json()["name"] == "ChatRely Backend"

