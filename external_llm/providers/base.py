"""Base LLM provider interface for chat and embeddings."""

from abc import ABC, abstractmethod
from typing import Any


class BaseLLMProvider(ABC):
    """Interface for LLM chat and embedding. Implementations: OpenAI, DeepSeek, Ollama."""

    @property
    def name(self) -> str:
        """Provider identifier (openai, deepseek, ollama)."""
        return "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> str:
        """Generate chat completion. Returns assistant content as string."""
        ...

    @abstractmethod
    async def embed(self, text: str, *, model: str | None = None) -> list[float]:
        """Get embedding vector for single text."""
        ...

    async def embed_batch(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        """Get embeddings for multiple texts. Default: sequential embed()."""
        result: list[list[float]] = []
        for t in texts:
            result.append(await self.embed(t, model=model))
        return result
