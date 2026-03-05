"""API test: POST /preprocess returns normalized and validation_result."""

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent.resolve()
# Load router from this service by path so we never resolve "routers" from another service
_preprocess_py = _service_root / "routers" / "preprocess.py"
_spec = importlib.util.spec_from_file_location(
    "routers.preprocess",
    _preprocess_py,
    submodule_search_locations=[str(_service_root)],
)
_preprocess = importlib.util.module_from_spec(_spec)
# So that "from normalize import ..." and "from validator import ..." inside preprocess.py resolve
sys.path.insert(0, str(_service_root))
_spec.loader.exec_module(_preprocess)
preprocess_router = _preprocess.router

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
