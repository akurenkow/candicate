"""
E2E test for complete document ingestion flow (FLOW-02).

Tests the full pipeline: upload → translation → chunking → embedding → storage in PostgreSQL and Qdrant.

This test suite:
1. Creates isolated test databases/collections before tests
2. Uploads a test document through the full pipeline
3. Validates results in both PostgreSQL (documents, chunks) and Qdrant (vectors)
4. Cleans up test data after completion

Test file: tests/data/Куренков Антон Андреевич Резюме.txt

Usage:
    # Run all e2e tests
    pytest tests/test_e2e_ingestion_flow.py -v -s
    
    # Run specific test
    pytest tests/test_e2e_ingestion_flow.py::TestE2EIngestion::test_chunks_count -v -s
    
    # Keep test data for inspection (skip cleanup)
    pytest tests/test_e2e_ingestion_flow.py -v -s --keep-test-data

Environment variables required:
    DATABASE_URL or POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    QDRANT_URL
    EMBEDDING_SERVICE_URL
    TRANSLATION_SERVICE_URL
"""

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

# Add service root and project root to path for imports (shared.tracing, etc.)
_test_dir = Path(__file__).parent
_project_root = _test_dir.parent
_service_root = _project_root / "services" / "ingestion-service"
for _p in (_service_root, _project_root):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# Load .env before config so DATABASE_URL, QDRANT_URL etc. come from env
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv(_project_root / ".env")


def _test_database_url() -> str:
    """URL for tests: use POSTGRES_TEST_DB so tests never touch main DB."""
    test_db = os.getenv("POSTGRES_TEST_DB", "headcracker_test").strip()
    u = os.getenv("DATABASE_URL")
    if u and test_db:
        from urllib.parse import urlparse, urlunparse
        parsed = list(urlparse(u))
        parsed[2] = "/" + test_db
        return urlunparse(parsed)
    if u:
        return u
    host = os.getenv("POSTGRES_HOST", "localhost")
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=os.getenv("POSTGRES_USER", "assistant_user"),
        pw=quote_plus(os.getenv("POSTGRES_PASSWORD", "strongpassword")),
        host=host,
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=test_db,
    )


def _test_qdrant_url() -> str:
    """URL for tests: from .env or localhost:6335 (host port per docker-compose)."""
    return os.getenv("QDRANT_URL", "http://localhost:6335")


# Import after path setup
import importlib.util
import httpx

def _load_ingestion_module(name: str, subpath: str = ""):
    path = _service_root / (subpath if subpath else f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"ingestion_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load ingestion-service modules and register in sys.modules so pipeline imports resolve here
_spec_c = importlib.util.spec_from_file_location("ingestion_config", _service_root / "config.py")
_ingestion_config = importlib.util.module_from_spec(_spec_c)
_spec_c.loader.exec_module(_ingestion_config)
sys.modules["config"] = _ingestion_config
EMBEDDING_DIMENSION = _ingestion_config.EMBEDDING_DIMENSION

_load_ingestion_module("chunking")
_load_ingestion_module("db")
_load_ingestion_module("embedding_client")
_load_ingestion_module("translation_client")
_load_ingestion_module("parsers")

# semantic_resume package
_sr_init = _service_root / "semantic_resume" / "__init__.py"
_spec_sr = importlib.util.spec_from_file_location(
    "semantic_resume", _sr_init, submodule_search_locations=[str(_service_root / "semantic_resume")]
)
_sr_mod = importlib.util.module_from_spec(_spec_sr)
sys.modules["semantic_resume"] = _sr_mod
_spec_sr.loader.exec_module(_sr_mod)

_pipeline_spec = importlib.util.spec_from_file_location("ingestion_pipeline", _service_root / "pipeline.py")
_ingestion_pipeline = importlib.util.module_from_spec(_pipeline_spec)
_pipeline_spec.loader.exec_module(_ingestion_pipeline)
run_pipeline = _ingestion_pipeline.run_pipeline


# ============================================================================
# TEST CONFIGURATION
# ============================================================================

TEST_RESUME_PATH = _project_root / "tests" / "data" / "Куренков Антон Андреевич Резюме.txt"
TEST_COLLECTION_NAME = "test_documents_e2e"


@pytest.fixture(scope="module")
def e2e_owner_id(request):
    """One owner_id per module, timestamp-based (aligned with other e2e scripts)."""
    request.module._initial_testsfailed = request.session.testsfailed
    owner_id = f"test_e2e_ingestion_{int(time.time())}"
    yield owner_id


# ============================================================================
# PYTEST FIXTURES
# ============================================================================

@pytest.fixture(scope="module")
def event_loop():
    """Single loop for this module (shared by all tests in test_e2e_ingestion_flow)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


async def _ensure_chunks_metadata(conn):
    """Run migration for chunks.metadata (idempotent). conn is asyncpg connection."""
    await conn.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chunks' AND column_name = 'metadata'
            ) THEN
                ALTER TABLE chunks ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb;
                CREATE INDEX IF NOT EXISTS ix_chunks_metadata_sk
                    ON chunks USING gin ((metadata -> 'section_kind'));
                CREATE INDEX IF NOT EXISTS ix_chunks_metadata_cn
                    ON chunks USING gin ((metadata -> 'company_name'));
            END IF;
        END $$;
    """)


@pytest.fixture(scope="module")
async def test_db_pool(event_loop):
    """Create test database connection pool (module scope, shared by all tests in this file)."""
    pool = await asyncpg.create_pool(
        _test_database_url(),
        min_size=1,
        max_size=5,
        command_timeout=60,
    )
    async with pool.acquire() as conn:
        await _ensure_chunks_metadata(conn)
    yield pool
    await pool.close()


@pytest.fixture(scope="module")
def test_qdrant_client():
    """Create Qdrant client for tests (module scope)."""
    client = QdrantClient(url=_test_qdrant_url(), check_compatibility=False)
    
    # Create test collection
    collections = client.get_collections().collections
    collection_names = {c.name for c in collections}
    
    if TEST_COLLECTION_NAME in collection_names:
        client.delete_collection(TEST_COLLECTION_NAME)
    
    client.create_collection(
        collection_name=TEST_COLLECTION_NAME,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )
    
    yield client
    
    # Cleanup: delete test collection (check via request fixture)
    # Note: cleanup is handled in uploaded_document fixture


@pytest.fixture(scope="module")
async def test_http_client():
    """Create HTTP client for service calls (module scope)."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        yield client


@pytest.fixture(scope="module")
async def uploaded_document(e2e_owner_id, test_db_pool, test_qdrant_client, test_http_client, request):
    """
    Upload test document once per module and return (document_id, original_content, chunk_count).
    Cleanup only on successful module run (no cleanup on failure or when --keep-test-data).
    """
    owner_id = e2e_owner_id
    external_id = f"{owner_id}_resume"
    if not TEST_RESUME_PATH.exists():
        pytest.skip(f"Test file not found: {TEST_RESUME_PATH}")
    with open(TEST_RESUME_PATH, "r", encoding="utf-8") as f:
        original_content = f.read()
    original_line_count = len(original_content.splitlines())
    import base64
    content_b64 = base64.b64encode(original_content.encode("utf-8")).decode("ascii")
    job_id = str(uuid4())
    trace_id = f"test_e2e_{job_id[:8]}"
    command = {
        "command": "upload_document",
        "job_id": job_id,
        "trace_id": trace_id,
        "owner_id": owner_id,
        "external_id": external_id,
        "filename": TEST_RESUME_PATH.name,
        "content_type": "text/plain",
        "content_base64": content_b64,
    }
    body = json.dumps(command).encode("utf-8")
    published_results = []
    async def mock_publish(result: dict):
        published_results.append(result)
    import config
    pipeline_mod = _ingestion_pipeline
    original_config = config.QDRANT_COLLECTION
    original_pipeline = getattr(pipeline_mod, "QDRANT_COLLECTION", config.QDRANT_COLLECTION)
    original_max_part = getattr(pipeline_mod, "MAX_DOCUMENT_PART_CHARS", None)
    config.QDRANT_COLLECTION = TEST_COLLECTION_NAME
    pipeline_mod.QDRANT_COLLECTION = TEST_COLLECTION_NAME
    pipeline_mod.MAX_DOCUMENT_PART_CHARS = None  # full-doc chunking for e2e (semantic + >50 chunks, project sections)
    try:
        success, document_id_str, error_message = await run_pipeline(
            body=body,
            pool=test_db_pool,
            qdrant=test_qdrant_client,
            http_client=test_http_client,
            publish_result_fn=mock_publish,
        )
        if not success:
            pytest.fail(f"Pipeline failed: {error_message}")
        document_id = UUID(document_id_str)
        async with test_db_pool.acquire() as conn:
            chunk_count = await conn.fetchval(
                "SELECT COUNT(*) FROM chunks WHERE document_id = $1",
                document_id,
            )
        yield {
            "document_id": document_id,
            "original_content": original_content,
            "original_line_count": original_line_count,
            "chunk_count": chunk_count,
            "job_id": job_id,
            "trace_id": trace_id,
        }
    finally:
        config.QDRANT_COLLECTION = original_config
        pipeline_mod.QDRANT_COLLECTION = original_pipeline
        if original_max_part is not None:
            pipeline_mod.MAX_DOCUMENT_PART_CHARS = original_max_part
        keep_data = request.config.getoption("--keep-test-data", default=False)
        module_ok = request.session.testsfailed == getattr(request.module, "_initial_testsfailed", -1)
        if not keep_data and module_ok:
            async with test_db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE owner_id = $1)",
                        owner_id,
                    )
                    await conn.execute(
                        "DELETE FROM documents WHERE owner_id = $1",
                        owner_id,
                    )
            try:
                test_qdrant_client.delete_collection(TEST_COLLECTION_NAME)
            except Exception:
                pass


def pytest_addoption(parser):
    """Add custom pytest command line options."""
    parser.addoption(
        "--keep-test-data",
        action="store_true",
        default=False,
        help="Keep test data in databases after test completion (for inspection)",
    )


# ============================================================================
# TEST CLASS
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.e2e
class TestE2EIngestion:
    """
    E2E tests for document ingestion flow.
    
    All tests share the same uploaded document (uploaded_document fixture).
    Each test validates a specific aspect of the ingestion result.
    """
    
    # ------------------------------------------------------------------------
    # PostgreSQL chunks table tests
    # ------------------------------------------------------------------------
    
    async def test_chunks_count_greater_than_50(self, uploaded_document):
        """Test: Document is split into more than 50 chunks."""
        chunk_count = uploaded_document["chunk_count"]
        assert chunk_count > 50, f"Expected > 50 chunks, got {chunk_count}"
    
    async def test_chunks_with_general_section(self, test_db_pool, uploaded_document):
        """Test: Chunks with section_kind='general' exist."""
        document_id = uploaded_document["document_id"]
        
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM chunks
                WHERE document_id = $1
                AND metadata @> '{"section_kind": "general"}'::jsonb
                """,
                document_id,
            )
        
        assert count > 0, f"Expected chunks with section_kind='general', found {count}"
    
    async def test_chunks_with_project_section(self, test_db_pool, uploaded_document):
        """Test: Chunks with section_kind='project' exist."""
        document_id = uploaded_document["document_id"]
        
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM chunks
                WHERE document_id = $1
                AND metadata @> '{"section_kind": "project"}'::jsonb
                """,
                document_id,
            )
        
        assert count > 0, f"Expected chunks with section_kind='project', found {count}"
    
    async def test_project_chunks_have_required_metadata(self, test_db_pool, uploaded_document):
        """
        Test: All project chunks have non-empty role, date_range, company_name, project_domain.
        """
        document_id = uploaded_document["document_id"]
        
        async with test_db_pool.acquire() as conn:
            # Get all project chunks
            rows = await conn.fetch(
                """
                SELECT id, metadata, text
                FROM chunks
                WHERE document_id = $1
                AND metadata @> '{"section_kind": "project"}'::jsonb
                """,
                document_id,
            )
        
        assert len(rows) > 0, "No project chunks found"
        
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        issues = []
        for row in rows:
            chunk_id = row["id"]
            metadata = _meta_dict(row["metadata"])
            text_preview = row["text"][:100]
            
            role = (metadata.get("role") or "").strip()
            date_range = (metadata.get("date_range") or "").strip()
            company_name = (metadata.get("company_name") or "").strip()
            project_domain = (metadata.get("project_domain") or "").strip()
            
            if not role:
                issues.append(f"Chunk {chunk_id}: role is empty. Text: {text_preview}...")
            if not date_range:
                issues.append(f"Chunk {chunk_id}: date_range is empty. Text: {text_preview}...")
            if not company_name:
                issues.append(f"Chunk {chunk_id}: company_name is empty. Text: {text_preview}...")
            if not project_domain:
                issues.append(f"Chunk {chunk_id}: project_domain is empty. Text: {text_preview}...")
        
        if issues:
            error_msg = f"Found {len(issues)} project chunks with missing metadata:\n" + "\n".join(issues)
            pytest.fail(error_msg)

    async def test_experience_chunks_have_required_metadata(self, test_db_pool, uploaded_document):
        """Test: All experience chunks (project or company) have non-empty role, project_domain, date_range, company_name."""
        document_id = uploaded_document["document_id"]
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, metadata, text
                FROM chunks
                WHERE document_id = $1
                AND (metadata @> '{"section_kind": "project"}'::jsonb OR metadata @> '{"section_kind": "company"}'::jsonb)
                """,
                document_id,
            )
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        issues = []
        for row in rows:
            meta = _meta_dict(row["metadata"])
            text_preview = row["text"][:80]
            if not (meta.get("role") or "").strip():
                issues.append(f"Chunk {row['id']}: role empty. Text: {text_preview}...")
            if not (meta.get("project_domain") or "").strip():
                issues.append(f"Chunk {row['id']}: project_domain empty. Text: {text_preview}...")
            if not (meta.get("date_range") or "").strip():
                issues.append(f"Chunk {row['id']}: date_range empty. Text: {text_preview}...")
            if not (meta.get("company_name") or "").strip():
                issues.append(f"Chunk {row['id']}: company_name empty. Text: {text_preview}...")
        if issues:
            pytest.fail(f"Experience chunks with missing role/project_domain/date_range/company_name:\n" + "\n".join(issues))

    async def test_experience_chunks_more_than_one_per_company(self, test_db_pool, uploaded_document):
        """Test: Each company has more than 1 experience chunk."""
        document_id = uploaded_document["document_id"]
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT metadata
                FROM chunks
                WHERE document_id = $1
                AND (metadata @> '{"section_kind": "project"}'::jsonb OR metadata @> '{"section_kind": "company"}'::jsonb)
                """,
                document_id,
            )
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        by_company = {}
        for row in rows:
            meta = _meta_dict(row["metadata"])
            cn = (meta.get("company_name") or "").strip()
            if cn:
                by_company[cn] = by_company.get(cn, 0) + 1
        bad = [c for c, n in by_company.items() if n <= 1]
        assert not bad, f"Companies with <= 1 experience chunk: {bad}"

    async def test_metadata_has_five_different_companies(self, test_db_pool, uploaded_document):
        """Test: Metadata contains 5 distinct company_name values."""
        document_id = uploaded_document["document_id"]
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT metadata FROM chunks WHERE document_id = $1",
                document_id,
            )
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        companies = set()
        for row in rows:
            meta = _meta_dict(row["metadata"])
            cn = (meta.get("company_name") or "").strip()
            if cn:
                companies.add(cn)
        assert len(companies) >= 5, f"Expected at least 5 distinct companies, got {len(companies)}: {companies}"

    async def test_no_company_chunks_with_empty_metadata(self, test_db_pool, uploaded_document):
        """Test: No chunks with section_kind='company' and empty company_name or date_range."""
        document_id = uploaded_document["document_id"]
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, metadata, text
                FROM chunks
                WHERE document_id = $1
                AND metadata @> '{"section_kind": "company"}'::jsonb
                """,
                document_id,
            )
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        bad = []
        for row in rows:
            meta = _meta_dict(row["metadata"])
            cn = (meta.get("company_name") or "").strip()
            dr = (meta.get("date_range") or "").strip()
            if not cn or not dr:
                bad.append(f"Chunk {row['id']}: company_name={repr(cn)}, date_range={repr(dr)}. Text: {row['text'][:60]}...")
        assert not bad, "Chunks with section_kind=company and empty metadata:\n" + "\n".join(bad)

    # ------------------------------------------------------------------------
    # PostgreSQL documents table tests
    # ------------------------------------------------------------------------
    
    async def test_document_record_exists(self, test_db_pool, uploaded_document):
        """Test: Document record exists in documents table."""
        document_id = uploaded_document["document_id"]
        
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, status FROM documents WHERE id = $1",
                document_id,
            )
        
        assert row is not None, f"Document {document_id} not found in documents table"
        assert row["status"] == "indexed", f"Expected status='indexed', got '{row['status']}'"
    
    async def test_document_content_matches_file(self, test_db_pool, uploaded_document):
        """Test: Document content field matches original file content (after translation)."""
        document_id = uploaded_document["document_id"]
        original_content = uploaded_document["original_content"]
        
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT content FROM documents WHERE id = $1",
                document_id,
            )
        
        assert row is not None, f"Document {document_id} not found"
        content = row["content"]
        
        # Content should be non-empty (it's translated version)
        assert content, "Document content is empty"
        
        # Content should be different from original (it's translated)
        # But should have similar structure (same number of lines approximately)
        content_lines = len(content.splitlines())
        original_lines = len(original_content.splitlines())
        
        # Allow 20% variance in line count (translation may add/remove lines)
        min_lines = int(original_lines * 0.8)
        max_lines = int(original_lines * 1.2)
        
        assert min_lines <= content_lines <= max_lines, (
            f"Content line count {content_lines} differs significantly from original {original_lines}"
        )
    
    async def test_document_original_content_line_count(self, test_db_pool, uploaded_document):
        """Test: Document original_content line count matches source file."""
        document_id = uploaded_document["document_id"]
        original_line_count = uploaded_document["original_line_count"]
        
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT original_content FROM documents WHERE id = $1",
                document_id,
            )
        
        assert row is not None, f"Document {document_id} not found"
        original_content = row["original_content"]
        
        assert original_content, "Document original_content is empty"
        
        stored_line_count = len(original_content.splitlines())
        assert stored_line_count == original_line_count, (
            f"original_content has {stored_line_count} lines, expected {original_line_count}"
        )
    
    # ------------------------------------------------------------------------
    # Qdrant vector database tests
    # ------------------------------------------------------------------------
    
    async def test_qdrant_chunk_count_matches_postgres(self, test_qdrant_client, test_db_pool, uploaded_document):
        """Test: Number of vectors in Qdrant matches number of chunks in PostgreSQL."""
        document_id = uploaded_document["document_id"]
        
        # Get PostgreSQL chunk count
        async with test_db_pool.acquire() as conn:
            pg_count = await conn.fetchval(
                "SELECT COUNT(*) FROM chunks WHERE document_id = $1",
                document_id,
            )
        
        # Get Qdrant point count for this document
        scroll_result = test_qdrant_client.scroll(
            collection_name=TEST_COLLECTION_NAME,
            scroll_filter={
                "must": [
                    {"key": "document_id", "match": {"value": str(document_id)}}
                ]
            },
            limit=10000,  # Large limit to get all points
            with_payload=False,
            with_vectors=False,
        )
        
        qdrant_points = scroll_result[0]
        qdrant_count = len(qdrant_points)
        
        assert qdrant_count == pg_count, (
            f"Qdrant has {qdrant_count} points, PostgreSQL has {pg_count} chunks"
        )
    
    async def test_qdrant_random_chunks_match_postgres(self, test_qdrant_client, test_db_pool, uploaded_document):
        """Test: Random 5 chunks in Qdrant match their PostgreSQL records by chunk_id."""
        document_id = uploaded_document["document_id"]
        
        # Get all chunk IDs from PostgreSQL
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, text, metadata FROM chunks WHERE document_id = $1",
                document_id,
            )
        
        assert len(rows) >= 5, f"Need at least 5 chunks, found {len(rows)}"
        def _meta_dict(m):
            return m if isinstance(m, dict) else json.loads(m) if m else {}
        # Create lookup dict (asyncpg may return metadata as JSON string)
        pg_chunks = {str(row["id"]): (row["text"], _meta_dict(row["metadata"])) for row in rows}
        
        # Select 5 random chunk IDs
        random_chunk_ids = random.sample(list(pg_chunks.keys()), 5)
        
        # Retrieve these points from Qdrant
        qdrant_points = test_qdrant_client.retrieve(
            collection_name=TEST_COLLECTION_NAME,
            ids=random_chunk_ids,
            with_payload=True,
            with_vectors=False,
        )
        
        assert len(qdrant_points) == 5, f"Expected 5 points from Qdrant, got {len(qdrant_points)}"
        
        # Compare each point with PostgreSQL
        mismatches = []
        for point in qdrant_points:
            chunk_id = point.payload.get("chunk_id")
            assert chunk_id in pg_chunks, f"chunk_id {chunk_id} not found in PostgreSQL"
            
            pg_text, pg_metadata = pg_chunks[chunk_id]
            
            # Note: Qdrant payload doesn't store text, only metadata
            # We validate that metadata fields match
            qdrant_company = point.payload.get("company_name") or ""
            qdrant_date_range = point.payload.get("date_range") or ""
            qdrant_section_kind = point.payload.get("section_kind") or ""
            pg_company = pg_metadata.get("company_name") or ""
            pg_date_range = pg_metadata.get("date_range") or ""
            pg_section_kind = pg_metadata.get("section_kind") or ""
            
            if qdrant_company != pg_company:
                mismatches.append(
                    f"chunk_id={chunk_id}: company_name mismatch: "
                    f"Qdrant='{qdrant_company}', PostgreSQL='{pg_company}'"
                )
            if qdrant_date_range != pg_date_range:
                mismatches.append(
                    f"chunk_id={chunk_id}: date_range mismatch: "
                    f"Qdrant='{qdrant_date_range}', PostgreSQL='{pg_date_range}'"
                )
            if qdrant_section_kind != pg_section_kind:
                mismatches.append(
                    f"chunk_id={chunk_id}: section_kind mismatch: "
                    f"Qdrant='{qdrant_section_kind}', PostgreSQL='{pg_section_kind}'"
                )
        
        if mismatches:
            error_msg = f"Found {len(mismatches)} metadata mismatches between Qdrant and PostgreSQL:\n"
            error_msg += "\n".join(mismatches)
            pytest.fail(error_msg)


# ============================================================================
# MAIN (for direct execution)
# ============================================================================

if __name__ == "__main__":
    # Run tests with pytest
    import subprocess
    
    result = subprocess.run(
        ["pytest", __file__, "-v", "-s"],
        cwd=str(_project_root),
    )
    sys.exit(result.returncode)
