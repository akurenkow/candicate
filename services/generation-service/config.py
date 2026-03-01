"""Generation service configuration."""

import os


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


OLLAMA_URL = _env("OLLAMA_URL", "http://host.docker.internal:11434")
DEFAULT_MODEL = _env("DEFAULT_MODEL", "llama3.2")
FALLBACK_MODEL = _env("FALLBACK_MODEL", "tinyllama")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.7"))
MIN_TEMPERATURE = float(os.getenv("MIN_TEMPERATURE", "0.1"))
MAX_TEMPERATURE = float(os.getenv("MAX_TEMPERATURE", "1.0"))
MAX_RETRIES = int(os.getenv("GENERATION_MAX_RETRIES", "3"))
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120.0"))
RETRY_TIGHTEN_SUFFIX_PATH = os.getenv(
    "RETRY_TIGHTEN_SUFFIX_PATH", "/app/prompts/retry-tighten-suffix.txt"
)
