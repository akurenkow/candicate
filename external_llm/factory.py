"""Factory to resolve LLM provider based on env (LLM_PROVIDER, API keys)."""

import logging
import os
from typing import Literal

from .providers import DeepSeekProvider, GeminiProvider, OllamaProvider, OpenAIProvider
from .providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

ProviderKind = Literal["openai", "deepseek", "gemini", "ollama"]


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _is_valid_key(key: str | None) -> bool:
    """Consider key valid if non-empty and not obviously placeholder."""
    if not key:
        return False
    k = key.strip()
    if not k or k in ("sk-...", "...", "xxx", "your-api-key"):
        return False
    return True


def _get_ollama_config() -> dict:
    return {
        "base_url": _env("OLLAMA_URL", "http://host.docker.internal:11434"),
        "chat_model": _env("OLLAMA_CHAT_MODEL") or _env("DEFAULT_MODEL", "llama3.2"),
        "embed_model": _env("OLLAMA_EMBED_MODEL", "snowflake-arctic-embed"),
        "fallback_model": _env("FALLBACK_MODEL", "tinyllama"),
    }


def _get_openai_config() -> dict:
    return {
        "api_key": _env("OPENAI_API_KEY"),
        "chat_model": _env("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        "embed_model": _env("OPENAI_EMBED_MODEL", "gpt-4o-mini"),
        "fallback_model": _env("OPENAI_FALLBACK_MODEL", "gpt-4o-mini"),
    }


def _get_deepseek_config() -> dict:
    return {
        "api_key": _env("DEEPSEEK_API_KEY"),
        "chat_model": _env("DEEPSEEK_CHAT_MODEL", "deepseek-chat"),
        "embed_model": _env("DEEPSEEK_EMBED_MODEL", "deepseek-embedding-v2"),
        "fallback_model": _env("DEEPSEEK_FALLBACK_MODEL", "deepseek-chat"),
    }


def _get_gemini_config() -> dict:
    return {
        "api_key": _env("GEMINI_API_KEY"),
        "chat_model": _env("GEMINI_CHAT_MODEL", "gemini-2.0-flash"),
        "embed_model": _env("GEMINI_EMBED_MODEL", "text-embedding-004"),
        "fallback_model": _env("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash"),
    }


def _create_ollama() -> OllamaProvider:
    cfg = _get_ollama_config()
    return OllamaProvider(
        base_url=cfg["base_url"],
        chat_model=cfg["chat_model"],
        embed_model=cfg["embed_model"],
        fallback_model=cfg["fallback_model"],
    )


def _create_openai() -> OpenAIProvider:
    cfg = _get_openai_config()
    return OpenAIProvider(
        api_key=cfg["api_key"] or "",
        chat_model=cfg["chat_model"],
        embed_model=cfg["embed_model"],
        fallback_model=cfg["fallback_model"],
    )


def _create_deepseek() -> DeepSeekProvider:
    cfg = _get_deepseek_config()
    return DeepSeekProvider(
        api_key=cfg["api_key"] or "",
        chat_model=cfg["chat_model"],
        embed_model=cfg["embed_model"],
        fallback_model=cfg["fallback_model"],
    )


def _create_gemini() -> GeminiProvider:
    cfg = _get_gemini_config()
    return GeminiProvider(
        api_key=cfg["api_key"] or "",
        chat_model=cfg["chat_model"],
        embed_model=cfg["embed_model"],
        fallback_model=cfg["fallback_model"],
    )


def resolve_provider() -> BaseLLMProvider:
    """
    Resolve LLM provider from env.
    - local: force Ollama
    - openai / deepseek / gemini: force vendor (fallback to local if key invalid)
    - auto: favorite first if key valid, else other vendors, else local
    """
    mode = _env("LLM_PROVIDER", "auto").lower()
    favorite = _env("FAVORITE_LLM_PROVIDER", "deepseek").lower()

    def _try_openai() -> OpenAIProvider | None:
        if _is_valid_key(_get_openai_config()["api_key"]):
            return _create_openai()
        return None

    def _try_deepseek() -> DeepSeekProvider | None:
        if _is_valid_key(_get_deepseek_config()["api_key"]):
            return _create_deepseek()
        return None

    def _try_gemini() -> GeminiProvider | None:
        if _is_valid_key(_get_gemini_config()["api_key"]):
            return _create_gemini()
        return None

    if mode == "local":
        p = _create_ollama()
        logger.info("LLM_PROVIDER=local → using ollama (forced)")
        return p

    if mode == "openai":
        prov = _try_openai()
        if prov:
            logger.info("LLM_PROVIDER=openai → using openai (valid key)")
            return prov
        logger.warning("LLM_PROVIDER=openai but OPENAI_API_KEY invalid → fallback to ollama")
        return _create_ollama()

    if mode == "deepseek":
        prov = _try_deepseek()
        if prov:
            logger.info("LLM_PROVIDER=deepseek → using deepseek (valid key)")
            return prov
        logger.warning(
            "LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY invalid → fallback to ollama"
        )
        return _create_ollama()

    if mode == "gemini":
        prov = _try_gemini()
        if prov:
            logger.info("LLM_PROVIDER=gemini → using gemini (valid key)")
            return prov
        logger.warning(
            "LLM_PROVIDER=gemini but GEMINI_API_KEY invalid → fallback to ollama"
        )
        return _create_ollama()

    if mode == "auto":
        _ORDER = ("openai", "deepseek", "gemini")
        _TRY = {"openai": _try_openai, "deepseek": _try_deepseek, "gemini": _try_gemini}
        fav = favorite if favorite in _ORDER else "deepseek"
        # Try favorite first
        prov = _TRY[fav]()
        if prov:
            logger.info("LLM_PROVIDER=auto, FAVORITE=%s → using %s", fav, prov.name)
            return prov
        # Try other vendors in order
        for v in _ORDER:
            if v == fav:
                continue
            prov = _TRY[v]()
            if prov:
                logger.info("LLM_PROVIDER=auto → using %s (%s key invalid)", v, fav)
                return prov
        logger.info("LLM_PROVIDER=auto, no valid vendor keys → fallback to ollama")
        return _create_ollama()

    logger.warning("Unknown LLM_PROVIDER=%s, defaulting to ollama", mode)
    return _create_ollama()


def resolve_for_chat() -> BaseLLMProvider:
    """Resolve provider for chat/generation. Same as resolve_provider (unified)."""
    return resolve_provider()


def resolve_for_embed() -> BaseLLMProvider:
    """
    Resolve provider for embeddings. Falls back to Ollama if vendor doesn't support embeddings.
    DeepSeek: chat only, embeddings not supported → force Ollama for embeddings.
    """
    mode = _env("LLM_PROVIDER", "auto").lower()
    favorite = _env("FAVORITE_LLM_PROVIDER", "deepseek").lower()

    def _try_openai() -> OpenAIProvider | None:
        if _is_valid_key(_get_openai_config()["api_key"]):
            return _create_openai()
        return None

    def _try_gemini() -> GeminiProvider | None:
        if _is_valid_key(_get_gemini_config()["api_key"]):
            return _create_gemini()
        return None

    if mode == "local":
        p = _create_ollama()
        logger.info("resolve_for_embed: LLM_PROVIDER=local → ollama")
        return p

    if mode == "openai":
        prov = _try_openai()
        if prov:
            logger.info("resolve_for_embed: LLM_PROVIDER=openai → openai embeddings")
            return prov
        logger.warning("resolve_for_embed: openai key invalid → fallback to ollama")
        return _create_ollama()

    if mode == "deepseek":
        logger.info("resolve_for_embed: deepseek does not support embeddings → ollama")
        return _create_ollama()

    if mode == "gemini":
        prov = _try_gemini()
        if prov:
            logger.info("resolve_for_embed: LLM_PROVIDER=gemini → gemini embeddings")
            return prov
        logger.warning("resolve_for_embed: gemini key invalid → fallback to ollama")
        return _create_ollama()

    if mode == "auto":
        _EMBED_ORDER = ("openai", "gemini")  # Skip deepseek (no embeddings)
        _TRY = {"openai": _try_openai, "gemini": _try_gemini}
        fav = favorite if favorite in _EMBED_ORDER else "openai"
        prov = _TRY.get(fav, lambda: None)()
        if prov:
            logger.info("resolve_for_embed: auto, FAVORITE=%s → %s", fav, prov.name)
            return prov
        for v in _EMBED_ORDER:
            if v == fav:
                continue
            prov = _TRY[v]()
            if prov:
                logger.info("resolve_for_embed: auto → %s embeddings", v)
                return prov
        logger.info("resolve_for_embed: auto, no valid vendor → ollama")
        return _create_ollama()

    logger.warning("resolve_for_embed: unknown mode=%s → ollama", mode)
    return _create_ollama()


def needs_ollama() -> bool:
    """True if resolved provider is Ollama (for Docker: start ollama only when True)."""
    p = resolve_provider()
    return p.name == "ollama"
