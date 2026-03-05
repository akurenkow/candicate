"""
LLM-based translation via external_llm provider (OpenAI, DeepSeek, or Ollama).
Uses prompt from TRANSLATE_PROMPT_PATH_RU_EN or TRANSLATE_PROMPT_PATH_EN_RU by source_lang/target_lang.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from config import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    TRANSLATE_PROMPT_PATH_EN_RU,
    TRANSLATE_PROMPT_PATH_RU_EN,
    DEFAULT_TEMPERATURE,
    TRANSLATION_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

_provider_cache = None


def _get_provider():
    """Lazy resolve via external_llm factory."""
    global _provider_cache
    if _provider_cache is None:
        from external_llm.factory import resolve_for_chat
        _provider_cache = resolve_for_chat()
    return _provider_cache

_PHANTOM_PREFIX = re.compile(r"^(?:translation|original\s+text)\s*:\s*\n?", re.I | re.MULTILINE)
_ORIGINAL_TEXT_MARKER = re.compile(r"\n\s*original\s+text\s*:\s*\n", re.I)
_FRENCH_INDICATORS = re.compile(r"\b(compagnie|période|objectif|mise\s+en|implémentation|utilisation|nombre\s+de)\b", re.I)


def _sanitize_translation(raw: str, target_lang: str) -> str:
    """
    Strip phantom labels ('Translation:', 'Original text:') and truncate at 'Original text:'.
    If target is English and content after 'Original text:' is French, discard it.
    """
    if not (raw or "").strip():
        return raw or ""
    out = raw.strip()
    while _PHANTOM_PREFIX.match(out):
        out = _PHANTOM_PREFIX.sub("", out, count=1).lstrip()
    if target_lang and target_lang.lower()[:2] == "en":
        m = _ORIGINAL_TEXT_MARKER.search(out)
        if m:
            out = out[: m.start()].rstrip()
            logger.warning("Translation output contained 'Original text:' marker; truncated")
        if _FRENCH_INDICATORS.search(out):
            logger.warning("Translation output appears to contain French; target was English")
    return out.strip()


def _load_prompt(path: Path, fallback: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return fallback


_PROMPT_RU_EN: Optional[str] = None
_PROMPT_EN_RU: Optional[str] = None


def get_prompt_template(source_lang: str, target_lang: str) -> str:
    global _PROMPT_RU_EN, _PROMPT_EN_RU
    src, tgt = (source_lang or "ru").lower()[:2], (target_lang or "en").lower()[:2]
    if src == "ru" and tgt == "en":
        if _PROMPT_RU_EN is None:
            _PROMPT_RU_EN = _load_prompt(
                Path(TRANSLATE_PROMPT_PATH_RU_EN),
                "Translate the following text from Russian to English. Preserve meaning, tone, and structure. Output only the translation, no explanations.\n\nText:\n{text}",
            )
        return _PROMPT_RU_EN
    if src == "en" and tgt == "ru":
        if _PROMPT_EN_RU is None:
            _PROMPT_EN_RU = _load_prompt(
                Path(TRANSLATE_PROMPT_PATH_EN_RU),
                "Translate the following text from English to Russian. Preserve meaning, tone, and structure. Output only the translation, no explanations.\n\nText:\n{text}",
            )
        return _PROMPT_EN_RU
    if _PROMPT_RU_EN is None:
        _PROMPT_RU_EN = _load_prompt(
            Path(TRANSLATE_PROMPT_PATH_RU_EN),
            "Translate the following text from Russian to English. Preserve meaning, tone, and structure. Output only the translation, no explanations.\n\nText:\n{text}",
        )
    return _PROMPT_RU_EN


def translate_via_ollama(
    text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout: float = 120.0,
) -> str:
    """
    Translate text using resolved LLM provider (OpenAI, DeepSeek, or Ollama).
    Prompt template must contain {text} placeholder.
    """
    text = (text or "").strip()
    if not text:
        return text

    model = model or DEFAULT_MODEL
    temp = temperature if temperature is not None else DEFAULT_TEMPERATURE
    template = get_prompt_template(source_lang, target_lang)
    if "{text}" in template:
        prompt = template.format(text=text)
    else:
        prompt = f"{template}\n\n{text}"

    provider = _get_provider()
    # For Ollama use translate model; for OpenAI/DeepSeek use provider default (model=None)
    chat_model = model if provider.name == "ollama" else None
    messages = [{"role": "user", "content": prompt}]

    async def _do_chat():
        return await provider.chat(
            messages,
            model=chat_model,
            temperature=temp,
            max_tokens=TRANSLATION_MAX_TOKENS,
        )

    try:
        result = asyncio.run(_do_chat())
        out = _sanitize_translation(result, target_lang)
        return out or text
    except Exception as e:
        if provider.name == "ollama" and model != FALLBACK_MODEL:
            logger.warning("Translation failed with %s, trying fallback model: %s", model, e)
            messages = [{"role": "user", "content": prompt}]
            try:
                result = asyncio.run(
                    provider.chat(
                        messages,
                        model=FALLBACK_MODEL,
                        temperature=temp,
                        max_tokens=TRANSLATION_MAX_TOKENS,
                    )
                )
                out = _sanitize_translation(result, target_lang)
                return out or text
            except Exception as e2:
                logger.exception("Fallback model also failed: %s", e2)
                raise
        raise
