"""Unit tests for api-gateway match-vacancy: 502/504 on downstream errors (diagnostic fix)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

_service_root = Path(__file__).resolve().parent.parent
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

from main import app

client = TestClient(app)


def test_match_vacancy_downstream_connect_error_returns_502_with_json():
    """When conversation-manager is unreachable, gateway returns 502 with parseable detail for bot."""
    body = {"owner_id": "o1", "vacancy_text": "Python developer", "summary": False}
    with patch("aio_pika.connect_robust", new_callable=AsyncMock, return_value=MagicMock()):
        with patch("main.httpx.AsyncClient") as mock_ac:
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_ac.return_value = mock_cm
            r = client.post("/match-vacancy", json=body)
    assert r.status_code == 502
    data = r.json()
    assert "detail" in data
    detail = data["detail"]
    assert isinstance(detail, list) and len(detail) >= 1
    assert "msg" in detail[0]
    assert "unavailable" in detail[0]["msg"].lower()


def test_match_vacancy_downstream_timeout_returns_504_with_json():
    """When conversation-manager times out, gateway returns 504 with parseable detail for bot."""
    body = {"owner_id": "o2", "vacancy_text": "Backend dev", "summary": True}
    with patch("aio_pika.connect_robust", new_callable=AsyncMock, return_value=MagicMock()):
        with patch("main.httpx.AsyncClient") as mock_ac:
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_ac.return_value = mock_cm
            r = client.post("/match-vacancy", json=body)
    assert r.status_code == 504
    data = r.json()
    assert "detail" in data
    detail = data["detail"]
    assert isinstance(detail, list) and len(detail) >= 1
    assert "msg" in detail[0]
    assert "timeout" in detail[0]["msg"].lower()
