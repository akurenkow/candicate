"""Resume converter service configuration."""

import os


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


DEEPSEEK_API_KEY = get_env("DEEPSEEK_API_KEY")
CELERY_BROKER_URL = get_env("CELERY_BROKER_URL", "redis://redis:6379/6")
RESUME_CONVERT_QUEUE = "resume_convert"
RESUME_CONVERT_TASK_TIMEOUT = int(os.getenv("RESUME_CONVERT_TASK_TIMEOUT", "600"))
