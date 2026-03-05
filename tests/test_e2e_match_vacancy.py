# noqa: D100
import os
import time
import requests
import pytest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST_DIR = os.path.join(_root, "tests")
_TEST_DOCUMENT_PATH = os.path.join(_TEST_DIR, "data", "Куренков Антон Андреевич Резюме.txt")

BASE_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8001").rstrip("/")
HTTP_TIMEOUT = 300
INGESTION_POLL_TIMEOUT = int(os.getenv("INGESTION_POLL_TIMEOUT", "300"))
INGESTION_POLL_INTERVAL = 2


def _upload_document(base_url: str, owner_id: str) -> str:
    with open(_TEST_DOCUMENT_PATH, "rb") as f:
        content = f.read()
    filename = os.path.basename(_TEST_DOCUMENT_PATH)
    r = requests.post(
        f"{base_url}/documents/upload",
        files={"file": (filename, content, "text/plain")},
        data={"owner_id": owner_id},
        timeout=HTTP_TIMEOUT,
    )
    assert r.status_code == 202, f"Upload expected 202, got {r.status_code}: {r.text}"
    data = r.json()
    assert "job_id" in data, f"Upload response must contain job_id: {data}"
    return data["job_id"]


def _poll_ingestion_ready(base_url: str, job_id: str) -> None:
    deadline = time.monotonic() + INGESTION_POLL_TIMEOUT
    while time.monotonic() < deadline:
        r = requests.get(
            f"{base_url}/documents/tasks/{job_id}/status",
            timeout=HTTP_TIMEOUT,
        )
        assert r.status_code == 200, f"Task status expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            return
        if status == "failed":
            msg = data.get("error_message", "unknown")
            pytest.fail(f"Ingestion job failed: {msg}")
        time.sleep(INGESTION_POLL_INTERVAL)
    pytest.fail(f"Ingestion did not complete within {INGESTION_POLL_TIMEOUT}s")


def _match_vacancy(base_url: str, owner_id: str, vacancy_text: str, summary: bool = False) -> list:
    r = requests.post(
        f"{base_url}/match-vacancy",
        json={"owner_id": owner_id, "vacancy_text": vacancy_text, "summary": summary},
        timeout=HTTP_TIMEOUT,
    )
    assert r.status_code == 200, f"match-vacancy expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, list), f"match-vacancy must return list, got {type(data)}"
    return data


def _cleanup(base_url: str, owner_id: str) -> None:
    try:
        requests.delete(
            f"{base_url}/documents/by-owner/{owner_id}",
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        pass


@pytest.fixture(scope="module")
def e2e_owner_id(request):
    request.module._initial_testsfailed = request.session.testsfailed
    owner_id = f"test_e2e_match_{int(time.time())}"
    try:
        yield owner_id
    finally:
        if request.session.testsfailed == getattr(request.module, "_initial_testsfailed", -1):
            _cleanup(BASE_URL, owner_id)


@pytest.fixture(scope="module")
def e2e_document_ready(e2e_owner_id):
    """Upload document once per module and wait for ingestion. Yields owner_id."""
    job_id = _upload_document(BASE_URL, e2e_owner_id)
    _poll_ingestion_ready(BASE_URL, job_id)
    yield e2e_owner_id


def test_e2e_match_vacancy_returns_one_candidate_with_snippets(e2e_document_ready):
    """FLOW-03: After upload, match-vacancy returns one result with relevance_score and matched_snippets."""
    owner_id = e2e_document_ready

    results = _match_vacancy(
        BASE_URL,
        owner_id,
        "Python backend developer, experience with AI and LLM",
        summary=False,
    )
    assert len(results) == 1, f"Expected one candidate result, got {len(results)}: {results}"
    item = results[0]
    assert item.get("owner_id") == owner_id, f"owner_id mismatch: {item}"
    assert "relevance_score" in item, f"Missing relevance_score: {item}"
    assert isinstance(item["relevance_score"], (int, float)), f"relevance_score must be number: {item}"
    assert "matched_snippets" in item, f"Missing matched_snippets: {item}"
    assert isinstance(item["matched_snippets"], list), f"matched_snippets must be list: {item}"
    assert len(item["matched_snippets"]) > 0, f"matched_snippets must be non-empty for known doc: {item}"


def test_e2e_match_vacancy_with_summary(e2e_document_ready):
    """FLOW-03: match-vacancy with summary=true returns summary in result."""
    owner_id = e2e_document_ready

    results = _match_vacancy(
        BASE_URL,
        owner_id,
        "Python backend developer, experience with AI",
        summary=True,
    )
    assert len(results) == 1, f"Expected one candidate result, got {len(results)}: {results}"
    item = results[0]
    assert item.get("owner_id") == owner_id, f"owner_id mismatch: {item}"
    assert "summary" in item, f"Missing summary when summary=true: {item}"
    assert isinstance(item["summary"], str) and len(item["summary"].strip()) > 0, (
        f"summary must be non-empty string: {item.get('summary')}"
    )


def test_e2e_match_vacancy_unknown_owner_returns_empty(e2e_owner_id):
    """FLOW-03: match-vacancy for owner with no documents returns empty list."""
    owner_id = e2e_owner_id
    other_owner = f"nonexistent_owner_{int(time.time())}"

    results = _match_vacancy(
        BASE_URL,
        other_owner,
        "Python developer",
        summary=False,
    )
    assert results == [], f"Expected empty list for unknown owner, got: {results}"


def test_e2e_match_vacancy_validation_empty_vacancy_returns_422_with_detail():
    """Validation: empty vacancy_text returns 422 with detail[] so bot can parse and show msg."""
    r = requests.post(
        f"{BASE_URL}/match-vacancy",
        json={"owner_id": "o", "vacancy_text": "", "summary": False},
        timeout=HTTP_TIMEOUT,
    )
    assert r.status_code == 422, f"Expected 422 for empty vacancy_text, got {r.status_code}: {r.text}"
    data = r.json()
    assert "detail" in data, f"Response must contain detail for bot parsing: {data}"
    detail = data["detail"]
    assert isinstance(detail, list), f"detail must be list: {type(detail)}"
    assert len(detail) >= 1, f"detail must have at least one item: {detail}"
    assert "msg" in detail[0], f"First detail item must have msg for bot: {detail[0]}"
