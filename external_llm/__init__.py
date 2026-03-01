"""External LLM adapter layer: OpenAI, DeepSeek, Ollama with fallback."""

from .factory import resolve_provider, resolve_for_chat, resolve_for_embed
from .providers.base import BaseLLMProvider

__all__ = [
    "BaseLLMProvider",
    "resolve_provider",
    "resolve_for_chat",
    "resolve_for_embed",
]
