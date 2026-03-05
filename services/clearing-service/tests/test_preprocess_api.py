"""API test: POST /preprocess returns normalized and validation_result."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
# Force clearing-service root first so "routers" resolves to this service (not another one under /workspace)
_service_root_s = str(_service_root)
if _service_root_s in sys.path:
    sys.path.remove(_service_root_s)
sys.path.insert(0, _service_root_s)

from routers.preprocess import router as preprocess_router

_app = FastAPI()
_app.include_router(preprocess_router)
client = TestClient(_app)


def test_preprocess_ok():
    r = client.post("/preprocess", json={"text": "  Hello world  "})
    assert r.status_code == 200
    data = r.json()
    assert data["normalized"] == "Hello world"
    assert data["validation_result"] == "OK"
    assert data["reasons"] == []


def test_preprocess_block_profanity():
    r = client.post("/preprocess", json={"text": "what the fuck"})
    assert r.status_code == 200
    data = r.json()
    assert data["validation_result"] == "BLOCK"
    assert "profanity" in data["reasons"]


def test_preprocess_idempotent():
    r1 = client.post("/preprocess", json={"text": "  x  "})
    r2 = client.post("/preprocess", json={"text": r1.json()["normalized"]})
    assert r1.json()["normalized"] == r2.json()["normalized"]
