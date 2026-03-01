"""DeepSeek provider using OpenAI-compatible API. Chat only; embeddings not supported."""

import logging
from typing import Any

from openai import AsyncOpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek API (OpenAI-compatible) for chat. Embeddings NOT supported by DeepSeek."""

    def __init__(
        self,
        api_key: str,
        *,
        chat_model: str = "deepseek-chat",
        embed_model: str = "deepseek-embedding-v2",
        fallback_model: str = "deepseek-chat",
    ):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
        )
        self._chat_model = chat_model
        self._embed_model = embed_model  # Not used; kept for compatibility
        self._fallback_model = fallback_model

    @property
    def name(self) -> str:
        return "deepseek"

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
                    "DeepSeek chat failed with %s, trying fallback: %s", model, e
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
        raise NotImplementedError("DeepSeek does not support embeddings API")

    async def embed_batch(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        raise NotImplementedError("DeepSeek does not support embeddings API")
