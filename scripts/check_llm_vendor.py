#!/usr/bin/env python3
"""
Проверка валидности внешнего LLM API (OpenAI/DeepSeek/Gemini).
Выход 0 = vendor API доступен, ollama не нужен.
Выход 1 = нужен fallback на ollama (ключ отсутствует/невалидный или LLM_PROVIDER=local).
Запуск: python scripts/check_llm_vendor.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load .env from project root."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1].replace('\\"', '"')
            elif v.startswith("'") and v.endswith("'"):
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v


def _env(k: str, default: str = "") -> str:
    return (os.environ.get(k) or default).strip()


def _is_valid_key(key: str | None) -> bool:
    if not key:
        return False
    k = key.strip()
    return bool(k and k not in ("sk-...", "...", "xxx", "your-api-key"))


def _test_openai(api_key: str, model: str = "gpt-4o-mini") -> bool:
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def _test_deepseek(api_key: str, model: str = "deepseek-chat") -> bool:
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def _test_gemini(api_key: str, model: str = "gemini-2.0-flash") -> bool:
    url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def main() -> int:
    _load_dotenv()
    mode = _env("LLM_PROVIDER", "auto").lower()
    favorite = _env("FAVORITE_LLM_PROVIDER", "deepseek").lower()

    if mode == "local":
        return 1  # Need ollama

    def try_openai() -> bool:
        key = _env("OPENAI_API_KEY")
        if not _is_valid_key(key):
            return False
        return _test_openai(key, _env("OPENAI_CHAT_MODEL", "gpt-4o-mini"))

    def try_deepseek() -> bool:
        key = _env("DEEPSEEK_API_KEY")
        if not _is_valid_key(key):
            return False
        return _test_deepseek(key, _env("DEEPSEEK_CHAT_MODEL", "deepseek-chat"))

    def try_gemini() -> bool:
        key = _env("GEMINI_API_KEY")
        if not _is_valid_key(key):
            return False
        return _test_gemini(key, _env("GEMINI_CHAT_MODEL", "gemini-2.0-flash"))

    order = ("openai", "deepseek", "gemini")
    try_fns = {"openai": try_openai, "deepseek": try_deepseek, "gemini": try_gemini}
    fav = favorite if favorite in order else "deepseek"

    if mode in ("openai", "deepseek", "gemini"):
        fn = try_fns.get(mode)
        if fn and fn():
            print(f"LLM_PROVIDER={mode}: API OK", file=sys.stderr)
            return 0
        return 1

    if mode == "auto":
        for v in [fav] + [x for x in order if x != fav]:
            if try_fns[v]():
                print(f"LLM_PROVIDER=auto: {v} API OK", file=sys.stderr)
                return 0
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
