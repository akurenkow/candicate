"""Ingestion service configuration from environment."""

import os
from urllib.parse import quote_plus

def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _build_database_url() -> str:
    """Build DATABASE_URL from parts so password can contain @ and other special chars."""
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=get_env("POSTGRES_USER", "assistant_user"),
        pw=quote_plus(get_env("POSTGRES_PASSWORD", "strongpassword")),
        host=get_env("POSTGRES_HOST", "postgres"),
        port=get_env("POSTGRES_PORT", "5432"),
        db=get_env("POSTGRES_DB", "assistant"),
    )


# Prefer explicit DATABASE_URL; if POSTGRES_HOST is set, build from parts (safe for passwords with @)
if get_env("POSTGRES_HOST"):
    DATABASE_URL = _build_database_url()
elif get_env("DATABASE_URL"):
    DATABASE_URL = get_env("DATABASE_URL")
else:
    DATABASE_URL = get_env("DATABASE_URL", "postgresql://assistant_user:strongpassword@postgres:5432/assistant")
QDRANT_URL = get_env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = get_env("QDRANT_COLLECTION", "documents")
# Must match embedding model: nomic-embed-text=768, snowflake-arctic-embed=1024
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
EMBEDDING_SERVICE_URL = get_env("EMBEDDING_SERVICE_URL", "http://localhost:8005")
TRANSLATION_SERVICE_URL = get_env("TRANSLATION_SERVICE_URL", "http://localhost:8008")
RABBITMQ_URL = get_env("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
UPLOAD_QUEUE = get_env("UPLOAD_QUEUE", "document_uploads")
RESULT_QUEUE = get_env("RESULT_QUEUE", "ingestion_results")
# Chunk limits for document splitting (translation/indexing); prefer OLLAMA_* names
OLLAMA_CHUNK_MAX_SIZE = int(os.getenv("OLLAMA_CHUNK_MAX_SIZE") or os.getenv("CHUNK_SIZE", "512"))
OLLAMA_CHUNK_OVERLAP = int(os.getenv("OLLAMA_CHUNK_OVERLAP") or os.getenv("CHUNK_OVERLAP", "50"))
CHUNK_SIZE = OLLAMA_CHUNK_MAX_SIZE
CHUNK_OVERLAP = OLLAMA_CHUNK_OVERLAP
# Max chars per part when splitting large document (no truncation; parts processed separately)
MAX_DOCUMENT_PART_CHARS = int(os.getenv("MAX_DOCUMENT_PART_CHARS", "0")) or None  # 0 = no split
# Max chars per block when translating (general section split to avoid LLM timeout/failure)
MAX_TRANSLATION_BLOCK_CHARS = int(os.getenv("MAX_TRANSLATION_BLOCK_CHARS", "4000"))
MAX_THREAD_WORKERS = int(os.getenv("MAX_THREAD_WORKERS", "4"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
# Celery broker (отдельный процесс воркера — обработка не блокирует HTTP)
CELERY_BROKER_URL = get_env("CELERY_BROKER_URL", "redis://redis:6379/7")
FALLBACK_RESPONSE = get_env("FALLBACK_RESPONSE", "Sorry, I will respond you a bit later.")
SUPPORTED_EXTENSIONS = frozenset(
    e.strip().lower()
    for e in get_env("SUPPORTED_FILE_TYPES", "pdf,docx,txt,md").split(",")
    if e.strip()
)
