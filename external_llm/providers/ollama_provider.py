"""Ollama provider wrapping local /api/generate and OpenAI-compatible embeddings."""

import logging
from typing import Any

from openai import AsyncOpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


def _messages_to_prompt(messages: list[dict[str, str]]) -> tuple[str, str | None]:
    """Convert messages to (prompt, system_prompt)."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)
        elif role == "assistant":
            user_parts.append(f"Assistant: {content}\nUser:")
    system = "\n".join(system_parts) if system_parts else None
    prompt = "\n".join(user_parts) if user_parts else ""
    return prompt, system


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM for chat and embeddings."""

    def __init__(
        self,
        base_url: str,
        *,
        chat_model: str = "llama3.2",
        embed_model: str = "snowflake-arctic-embed",
        fallback_model: str = "tinyllama",
    ):
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._embed_model = embed_model
        self._fallback_model = fallback_model
        self._client = AsyncOpenAI(
            base_url=f"{self._base_url}/v1",
            api_key="ollama",
        )

    @property
    def name(self) -> str:
        return "ollama"

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
        prompt, system = _messages_to_prompt(messages)
        if not prompt:
            return ""

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
                    "Ollama chat failed with %s, trying fallback: %s", model, e
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
