"""Conversation manager configuration (CTR-09, FLOW-01)."""

import os
from pathlib import Path
from urllib.parse import quote_plus

# Загружаем .env из корня репозитория (при локальном запуске или если смонтирован в контейнер)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _env_path.exists():
        load_dotenv(_env_path)
    else:
        load_dotenv()  # cwd или родительские каталоги
except ImportError:
    pass


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _build_database_url() -> str:
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=get_env("POSTGRES_USER", "assistant_user"),
        pw=quote_plus(get_env("POSTGRES_PASSWORD", "strongpassword")),
        host=get_env("POSTGRES_HOST", "postgres"),
        port=get_env("POSTGRES_PORT", "5432"),
        db=get_env("POSTGRES_DB", "assistant"),
    )


if get_env("POSTGRES_HOST") or get_env("POSTGRES_USER"):
    DATABASE_URL = _build_database_url()
elif get_env("DATABASE_URL"):
    DATABASE_URL = get_env("DATABASE_URL")
else:
    DATABASE_URL = get_env("DATABASE_URL", "postgresql://assistant_user:strongpassword@postgres:5432/assistant")

RABBITMQ_URL = get_env("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
RAG_QUEUE = get_env("RAG_QUEUE", "rag_tasks")
LOCK_TTL_SECONDS = int(os.getenv("LOCK_TTL_SECONDS", "300"))

def _service_url(env_key: str, default: str) -> str:
    v = get_env(env_key, default)
    if not v or "localhost" in v.split("//")[-1].split("/")[0].split(":")[0].lower():
        return default
    return v


CLEARING_SERVICE_URL = _service_url("CLEARING_SERVICE_URL", "http://clearing-service:8000")
RETRIEVAL_SERVICE_URL = _service_url("RETRIEVAL_SERVICE_URL", "http://retrieval-service:8000")
GENERATION_SERVICE_URL = _service_url("GENERATION_SERVICE_URL", "http://generation-service:8000")
FALLBACK_RESPONSE = get_env("FALLBACK_RESPONSE", "Sorry, I will respond you a bit later.")
# Канонические формулировки — в services/telegram-bot/main.py TEXTS["*"]["no_documents_stub"]; здесь — значение по умолчанию для пайплайна (без языка пользователя).
NO_DOCUMENTS_STUB_RESPONSE = get_env(
    "NO_DOCUMENTS_STUB_RESPONSE",
    "Утром деньги, вечером стулья... Сначала загрузите резюме (/update). / Please upload your resume first (/update).",
)
LOW_RELEVANCE_STUB_RESPONSE = get_env(
    "LOW_RELEVANCE_STUB_RESPONSE",
    "В резюме по этому запросу не найдено достаточно релевантного. Уточните формулировку или загрузите обновлённое резюме (/update). / No sufficiently relevant content in the resume for this query. Rephrase or upload an updated resume (/update).",
)
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "10"))
MODEL_CONTEXT_LIMIT = int(os.getenv("MODEL_CONTEXT_LIMIT", "4096"))

# Пороги RAG
CLARIFY_THRESHOLD = float(os.getenv("CLARIFY_THRESHOLD", "0.35"))
ANSWER_THRESHOLD = float(os.getenv("ANSWER_THRESHOLD", "0.65"))
MAX_CLARIFICATION_ATTEMPTS = int(os.getenv("MAX_CLARIFICATION_ATTEMPTS", "2"))
COMPANY_FUZZY_CUTOFF = float(os.getenv("COMPANY_FUZZY_CUTOFF", "0.55"))

TRANSLATION_SERVICE_URL = _service_url("TRANSLATION_SERVICE_URL", "http://translation-service:8000")
MODEL_WORKING_LANGUAGE = get_env("MODEL_WORKING_LANGUAGE", "en").lower()
TRANSLATION_RETRY_LIMIT = int(os.getenv("TRANSLATION_RETRY_LIMIT", "3"))
TRANSLATION_TIMEOUT = float(os.getenv("TRANSLATION_TIMEOUT", "60.0"))

EMBEDDING_SERVICE_URL = _service_url("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
RAG_LABELS_PATH = get_env("RAG_LABELS_PATH", "/app/prompts/rag-labels.txt")
POSTPROCESS_TRANSLATION_RETRY_LIMIT = int(os.getenv("POSTPROCESS_TRANSLATION_RETRY_LIMIT", "3"))
POSTPROCESS_FIDELITY_THRESHOLD = float(os.getenv("POSTPROCESS_FIDELITY_THRESHOLD", "0.85"))
POSTPROCESS_FIDELITY_THRESHOLD_CROSSLINGUAL = float(os.getenv("POSTPROCESS_FIDELITY_THRESHOLD_CROSSLINGUAL", "0.62"))
POSTPROCESS_EMBED_TIMEOUT = float(os.getenv("POSTPROCESS_EMBED_TIMEOUT", "30.0"))

# Cover letter (Executive Summary)
COVER_LETTER_MIN_SIZE = int(os.getenv("COVER_LETTER_MIN_SIZE", "500"))
COVER_LETTER_MAX_SIZE = int(os.getenv("COVER_LETTER_MAX_SIZE", "1500"))

# Match vacancy: chunk limit for each summary block (tokens); 1000 can truncate long "Strong sides"
GENERATE_MAX_CHUNK = int(os.getenv("GENERATE_MAX_CHUNK", "2000"))
GENERATION_CHUNK_TIMEOUT = float(os.getenv("GENERATION_CHUNK_TIMEOUT", "120.0"))
MATCHING_REVIEW_MIN_SIZE = int(os.getenv("MATCHING_REVIEW_MIN_SIZE", "500"))
MATCHING_REVIEW_MAX_SIZE = int(os.getenv("MATCHING_REVIEW_MAX_SIZE", "1500"))
COVER_LETTER_DEFAULT_LANG = get_env("COVER_LETTER_DEFAULT_LANG", "en").lower()
COVER_LETTER_PROMPT_PATH = get_env("COVER_LETTER_PROMPT_PATH", "/app/prompts/executive-summary-cover-letter.txt")
