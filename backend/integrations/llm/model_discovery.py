"""Dynamic model discovery for supported LLM providers.

Uses provider APIs via httpx and caches results with a TTL. Returned models are
filtered to those suitable for chat.

Supported providers:
- anthropic: /v1/models
- openai: /v1/models
- gemini: Generative Language API /v1beta/models
- grok (xAI): OpenAI-compatible /v1/models
- ollama: /api/tags
- llamacpp: OpenAI-compatible /v1/models (if available)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelInfo:
    provider: str
    id: str
    display_name: str | None = None
    context_length: int | None = None
    chat_capable: bool = True
    raw: Mapping[str, Any] | None = None


class _TTLCache:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, list[ModelInfo]]] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @ttl_seconds.setter
    def ttl_seconds(self, v: int) -> None:
        self._ttl_seconds = max(1, int(v))

    def get(self, key: str) -> list[ModelInfo] | None:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, value = item
            if now >= expires_at:
                self._store.pop(key, None)
                return None
            return list(value)

    def set(self, key: str, value: Sequence[ModelInfo]) -> None:
        with self._lock:
            self._store[key] = (time.time() + self._ttl_seconds, list(value))

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_CACHE = _TTLCache(int(os.getenv("CLIMATEIQ_LLM_MODEL_TTL_SECONDS", "300")))


def _require_httpx() -> Any:
    try:
        import httpx

        return httpx
    except Exception as e:  # pragma: no cover
        raise RuntimeError("httpx is required. Install with: pip install httpx") from e


def _http_client(timeout_s: float) -> Any:
    httpx = _require_httpx()
    return httpx.Client(timeout=httpx.Timeout(timeout_s), follow_redirects=True)


def _cache_key(provider: str, base_url: str | None, api_key: str | None) -> str:
    # Do not include the raw key.
    return f"{provider}|{base_url or ''}|{'set' if api_key else 'unset'}"


def _filter_chat_models(provider: str, models: Iterable[ModelInfo]) -> list[ModelInfo]:
    p = provider.lower().strip()
    out: list[ModelInfo] = []
    for m in models:
        if not m.chat_capable:
            continue
        mid = m.id.lower()

        # Heuristics: keep only broadly chat-capable families.
        if p == "openai":
            if not (
                mid.startswith("gpt-")
                or mid.startswith("o1")
                or "chat" in mid
                or mid.startswith("gpt")
            ):
                continue
        elif p == "gemini":
            if "gemini" not in mid:
                continue
        elif p == "grok":
            if "grok" not in mid:
                continue

        out.append(m)

    out.sort(key=lambda x: x.id)
    return out


def fetch_anthropic_models(
    *,
    api_key: str,
    base_url: str = "https://api.anthropic.com",
    timeout_s: float = 10.0,
) -> list[ModelInfo]:
    if not api_key:
        return []

    url = base_url.rstrip("/") + "/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
    }

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url, headers=headers, params={"limit": 1000})
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("data") or []:
                mid = item.get("id")
                if not mid:
                    continue
                models.append(
                    ModelInfo(
                        provider="anthropic",
                        id=mid,
                        display_name=item.get("display_name"),
                        context_length=item.get("context_length"),
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("Anthropic model discovery failed")
        return []

    return _filter_chat_models("anthropic", models)


def fetch_openai_models(
    *,
    api_key: str,
    base_url: str = "https://api.openai.com",
    timeout_s: float = 10.0,
) -> list[ModelInfo]:
    if not api_key:
        return []

    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("data") or []:
                mid = item.get("id")
                if not mid:
                    continue
                models.append(
                    ModelInfo(
                        provider="openai",
                        id=mid,
                        display_name=mid,
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("OpenAI model discovery failed")
        return []

    return _filter_chat_models("openai", models)


def fetch_gemini_models(
    *,
    api_key: str,
    base_url: str = "https://generativelanguage.googleapis.com",
    timeout_s: float = 10.0,
) -> list[ModelInfo]:
    if not api_key:
        return []

    url = base_url.rstrip("/") + "/v1beta/models"

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url, params={"key": api_key, "pageSize": 1000})
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("models") or []:
                name = item.get("name")
                if not name:
                    continue
                # name: "models/gemini-1.5-pro" -> "gemini-1.5-pro"
                mid = name.split("/", 1)[-1]
                methods = item.get("supportedGenerationMethods") or []
                if "generateContent" not in methods and "streamGenerateContent" not in methods:
                    continue
                models.append(
                    ModelInfo(
                        provider="gemini",
                        id=mid,
                        display_name=item.get("displayName") or mid,
                        context_length=item.get("inputTokenLimit"),
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("Gemini model discovery failed")
        return []

    return _filter_chat_models("gemini", models)


def fetch_grok_models(
    *,
    api_key: str,
    base_url: str = "https://api.x.ai",
    timeout_s: float = 10.0,
) -> list[ModelInfo]:
    if not api_key:
        return []

    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("data") or []:
                mid = item.get("id")
                if not mid:
                    continue
                models.append(
                    ModelInfo(
                        provider="grok",
                        id=mid,
                        display_name=mid,
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("Grok model discovery failed")
        return []

    return _filter_chat_models("grok", models)


def fetch_ollama_models(
    *,
    base_url: str = "http://localhost:11434",
    timeout_s: float = 5.0,
) -> list[ModelInfo]:
    url = base_url.rstrip("/") + "/api/tags"

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url)
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("models") or []:
                name = item.get("name")
                if not name:
                    continue
                models.append(
                    ModelInfo(
                        provider="ollama",
                        id=name,
                        display_name=name,
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("Ollama model discovery failed")
        return []

    return _filter_chat_models("ollama", models)


def fetch_llamacpp_models(
    *,
    base_url: str = "http://localhost:8080",
    timeout_s: float = 5.0,
) -> list[ModelInfo]:
    """Fetch models from an OpenAI-compatible llama.cpp server if available."""

    url = base_url.rstrip("/") + "/v1/models"

    models: list[ModelInfo] = []
    try:
        with _http_client(timeout_s) as c:
            resp = c.get(url)
            resp.raise_for_status()
            data = resp.json() or {}
            for item in data.get("data") or []:
                mid = item.get("id")
                if not mid:
                    continue
                models.append(
                    ModelInfo(
                        provider="llamacpp",
                        id=mid,
                        display_name=mid,
                        chat_capable=True,
                        raw=item,
                    )
                )
    except Exception:
        logger.exception("llama.cpp model discovery failed")
        return []

    return _filter_chat_models("llamacpp", models)


def discover_models(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 10.0,
    ttl_seconds: int | None = None,
    force_refresh: bool = False,
) -> list[ModelInfo]:
    """Discover chat-capable models for a provider.

    Results are cached in-memory with TTL.
    """

    p = provider.lower().strip()
    if ttl_seconds is not None:
        _CACHE.ttl_seconds = ttl_seconds

    key = _cache_key(p, base_url, api_key)
    if not force_refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    try:
        if p == "anthropic":
            resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
            if not resolved_key:
                return []
            resolved_base = base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            if resolved_base is None:
                return []
            models = fetch_anthropic_models(
                api_key=resolved_key,
                base_url=resolved_base,
                timeout_s=timeout_s,
            )
        elif p == "openai":
            resolved_key = api_key or os.getenv("OPENAI_API_KEY", "")
            if not resolved_key:
                return []
            resolved_base = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
            if resolved_base is None:
                return []
            models = fetch_openai_models(
                api_key=resolved_key,
                base_url=resolved_base,
                timeout_s=timeout_s,
            )
        elif p == "gemini":
            resolved_key = api_key or os.getenv("GEMINI_API_KEY", "")
            if not resolved_key:
                return []
            resolved_base = base_url or os.getenv(
                "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
            )
            if resolved_base is None:
                return []
            models = fetch_gemini_models(
                api_key=resolved_key,
                base_url=resolved_base,
                timeout_s=timeout_s,
            )
        elif p == "grok":
            resolved_key = api_key or os.getenv("GROK_API_KEY", "")
            if not resolved_key:
                return []
            resolved_base = base_url or os.getenv("GROK_BASE_URL", "https://api.x.ai")
            if resolved_base is None:
                return []
            models = fetch_grok_models(
                api_key=resolved_key,
                base_url=resolved_base,
                timeout_s=timeout_s,
            )
        elif p == "ollama":
            resolved_base = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            if resolved_base is None:
                return []
            models = fetch_ollama_models(
                base_url=resolved_base,
                timeout_s=min(timeout_s, 15.0),
            )
        elif p == "llamacpp":
            resolved_base = base_url or os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080")
            if resolved_base is None:
                return []
            models = fetch_llamacpp_models(
                base_url=resolved_base,
                timeout_s=min(timeout_s, 15.0),
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    except Exception:
        logger.exception("Model discovery failed provider=%s", provider)
        models = []

    _CACHE.set(key, models)
    return list(models)


def discover_many(
    providers: Sequence[str],
    *,
    provider_configs: Mapping[str, Mapping[str, str]] | None = None,
    timeout_s: float = 10.0,
    force_refresh: bool = False,
) -> dict[str, list[ModelInfo]]:
    out: dict[str, list[ModelInfo]] = {}
    for p in providers:
        cfg = (provider_configs or {}).get(p, {})
        out[p] = discover_models(
            p,
            api_key=cfg.get("api_key"),
            base_url=cfg.get("base_url"),
            timeout_s=timeout_s,
            force_refresh=force_refresh,
        )
    return out
