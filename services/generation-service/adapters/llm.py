"""LLM adapter interface (SYS-04). Implementations are fully mockable."""

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass
class GenerateParams:
    prompt: str
    temperature: float = 0.7
    model: str | None = None
    system_prompt: str | None = None
    max_tokens: int | None = None


class LLMAdapter(Protocol):
    """Interface for LLM generation. Implement or mock in tests."""

    def generate(self, params: GenerateParams) -> str:
        ...


def _get_provider_adapter():
    """Lazy import to avoid loading external_llm when mocking in tests."""
    from external_llm.factory import resolve_for_chat

    return resolve_for_chat()


class ProviderLLMAdapter:
    """Unified LLM adapter: OpenAI, DeepSeek, or Ollama via external_llm factory."""

    def __init__(self, max_tokens: int = 512):
        self._max_tokens = max_tokens
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            self._provider = _get_provider_adapter()
        return self._provider

    def generate(self, params: GenerateParams) -> str:
        messages: list[dict[str, str]] = []
        if params.system_prompt:
            messages.append({"role": "system", "content": params.system_prompt})
        messages.append({"role": "user", "content": params.prompt})
        provider = self._get_provider()
        # Run async provider from sync context (FastAPI runs sync routes in threadpool)
        max_tokens = params.max_tokens if params.max_tokens is not None else self._max_tokens
        return _run_async(
            provider.chat(
                messages,
                model=params.model,
                temperature=params.temperature,
                max_tokens=max_tokens,
            )
        )


def _run_async(coro):
    """Run async coro from sync context. FastAPI sync routes run in threadpool (no loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in async context: run in thread to avoid nesting loops
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


class OllamaLLMAdapter:
    """Ollama /api/generate implementation. Kept for backward compatibility and tests."""

    def __init__(
        self,
        base_url: str,
        default_model: str,
        fallback_model: str,
        timeout: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._fallback_model = fallback_model
        self._timeout = timeout

    def generate(self, params: GenerateParams) -> str:
        import httpx

        model = params.model or self._default_model
        payload = {
            "model": model,
            "prompt": params.prompt,
            "stream": False,
            "options": {"temperature": params.temperature},
        }
        if params.system_prompt:
            payload["system"] = params.system_prompt
        url = f"{self._base_url}/api/generate"
        with httpx.Client(timeout=self._timeout) as client:
            try:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return (data.get("response") or "").strip()
            except Exception:
                if model != self._fallback_model:
                    payload["model"] = self._fallback_model
                    resp = client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    return (data.get("response") or "").strip()
                raise
