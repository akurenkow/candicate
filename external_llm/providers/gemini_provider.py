"""Gemini provider using OpenAI-compatible API (generativelanguage.googleapis.com)."""

import logging
from typing import Any

from openai import AsyncOpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API (OpenAI-compatible) for chat and embeddings."""

    def __init__(
        self,
        api_key: str,
        *,
        chat_model: str = "gemini-2.0-flash",
        embed_model: str = "text-embedding-004",
        fallback_model: str = "gemini-1.5-flash",
    ):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=GEMINI_BASE_URL,
        )
        self._chat_model = chat_model
        self._embed_model = embed_model
        self._fallback_model = fallback_model

    @property
    def name(self) -> str:
        return "gemini"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> str:
        model = model or self._chat_model
        try:
            r = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            if model != self._fallback_model:
                logger.warning(
                    "Gemini chat failed with %s, trying fallback: %s", model, e
                )
                r = await self._client.chat.completions.create(
                    model=self._fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                return (r.choices[0].message.content or "").strip()
            raise

    async def embed(self, text: str, *, model: str | None = None) -> list[float]:
        model = model or self._embed_model
        r = await self._client.embeddings.create(model=model, input=text)
        return r.data[0].embedding

    async def embed_batch(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        model = model or self._embed_model
        r = await self._client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in sorted(r.data, key=lambda x: x.index)]
