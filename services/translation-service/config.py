"""Translation service configuration from environment."""

import os


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


OLLAMA_URL = get_env("OLLAMA_URL", "http://host.docker.internal:11434")
# Модель для перевода: из OLLAMA_TRANSLATE_MODEL или legacy DEFAULT_MODEL
OLLAMA_TRANSLATE_MODEL = get_env("OLLAMA_TRANSLATE_MODEL", "")
DEFAULT_MODEL = OLLAMA_TRANSLATE_MODEL or get_env("DEFAULT_MODEL", "translategemma")
FALLBACK_MODEL = get_env("FALLBACK_MODEL", "tinyllama")
REDIS_URL = get_env("REDIS_URL", "redis://redis:6379/8")
ENABLE_CACHE = os.getenv("ENABLE_CACHE", "true").lower() in ("true", "1", "yes")
CACHE_TTL_SEC = int(os.getenv("CACHE_TTL_SEC", "86400"))
RABBITMQ_URL = get_env("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
TRANSLATION_TASKS_QUEUE = get_env("TRANSLATION_TASKS_QUEUE", "translation_tasks")
TRANSLATION_RESULTS_QUEUE = get_env("TRANSLATION_RESULTS_QUEUE", "translation_results")
CELERY_BROKER_URL = get_env("CELERY_BROKER_URL", "redis://redis:6379/9")
# Prompt file paths by direction (mounted or in image)
TRANSLATE_PROMPT_PATH_RU_EN = get_env("TRANSLATE_PROMPT_PATH_RU_EN", get_env("TRANSLATE_PROMPT_PATH", "/app/prompts/translate-ru-en.txt"))
TRANSLATE_PROMPT_PATH_EN_RU = get_env("TRANSLATE_PROMPT_PATH_EN_RU", "/app/prompts/translate-en-ru.txt")
# Default temperature for translation
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.3"))
