"""ClimateIQ LLM provider abstraction.

Features:
- Unified chat completion interface using litellm
- Dynamic model discovery per provider
- Tool/function calling support (OpenAI tool schema)
- Per-provider request rate limiting (RPM)
- Fallback chain: primary -> secondary -> local -> rule-based
- Per-request cost tracking

This module does not implement tool execution; it only defines the LLM interface.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from .cost_tracker import CostTracker
from .model_discovery import ModelInfo, discover_models

logger = logging.getLogger(__name__)


# ============================================================================
# Simple LLM Provider for Chat Routes
# ============================================================================


class LLMProvider:
    """
    Simple LLM provider for chat functionality.

    Provides a unified interface for calling different LLM providers
    (Anthropic, OpenAI, Gemini) with tool support.
    """

    PROVIDER_MODELS: ClassVar[dict[str, str]] = {
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
        "gemini": "gemini-2.0-flash",
    }

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fallbacks: list[LLMProvider] | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model or self.PROVIDER_MODELS.get(provider, "gpt-4o")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.fallbacks: list[LLMProvider] = fallbacks or []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system: Optional system prompt
            tools: Optional list of tool definitions
            **kwargs: Additional provider-specific options

        Returns:
            Dict with 'content' and optionally 'tool_calls'
        """
        try:
            return await self._chat_once(messages, system=system, tools=tools, **kwargs)
        except Exception as e:
            logger.warning("LLM request failed on provider=%s: %s — trying fallbacks", self.provider, e)
            for fallback in self.fallbacks:
                try:
                    result = await fallback._chat_once(messages, system=system, tools=tools, **kwargs)
                    logger.info("LLM fallback succeeded via provider=%s", fallback.provider)
                    return result
                except Exception as fe:
                    logger.warning("LLM fallback failed provider=%s: %s", fallback.provider, fe)
            logger.error("LLM request failed: all providers exhausted")
            raise

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        litellm = _require_litellm()

        # Build messages list
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # Build model string for litellm — pass api_key directly instead of
        # setting os.environ (which is not concurrency-safe).
        if self.provider == "anthropic":
            model_str = f"anthropic/{self.model}"
        elif self.provider == "openai":
            model_str = self.model
        elif self.provider == "gemini":
            model_str = f"gemini/{self.model}"
        else:
            model_str = self.model

        response = await litellm.acompletion(
            model=model_str,
            messages=full_messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=tools if tools else None,
            api_key=self.api_key,
            **kwargs,
        )

        # Extract response content
        choice = response.choices[0]
        content = choice.message.content or ""

        result: dict[str, Any] = {"content": content}

        # Extract tool calls if present
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        return result

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Simple generate method for single-turn requests."""
        return await self.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
            **kwargs,
        )


# ============================================================================
# Original Provider Implementation
# ============================================================================


def _require_litellm() -> Any:
    try:
        import litellm

        return litellm
    except Exception as e:  # pragma: no cover
        raise RuntimeError("litellm is required. Install with: pip install litellm") from e


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    provider: str
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    rpm: int = 60
    timeout_s: float = 30.0


class _RateLimiter:
    """Simple requests-per-minute limiter."""

    def __init__(self, rpm: int) -> None:
        self._rpm = max(1, int(rpm))
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._count = 0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._window_start
                if elapsed >= 60.0:
                    self._window_start = now
                    self._count = 0
                if self._count < self._rpm:
                    self._count += 1
                    return
                sleep_s = max(0.01, 60.0 - elapsed)
            time.sleep(sleep_s)


class ClimateIQLLMProvider:
    SUPPORTED_PROVIDERS = ("anthropic", "openai", "gemini", "grok", "ollama", "llamacpp")

    def __init__(
        self,
        *,
        primary: ProviderSettings,
        secondary: ProviderSettings | None = None,
        local: ProviderSettings | None = None,
        model_ttl_seconds: int = 300,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.primary = _normalize_settings(primary)
        self.secondary = _normalize_settings(secondary) if secondary else None
        self.local = _normalize_settings(local) if local else None

        self.model_ttl_seconds = max(1, int(model_ttl_seconds))
        self.cost_tracker = cost_tracker or CostTracker()

        self._rate_limiters: dict[str, _RateLimiter] = {}
        for s in [self.primary, self.secondary, self.local]:
            if s:
                self._rate_limiters[s.provider] = _RateLimiter(s.rpm)

        self._models_lock = threading.Lock()
        self._models: dict[str, list[ModelInfo]] = {}

    def discover_models(
        self,
        *,
        providers: Sequence[str] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, list[ModelInfo]]:
        ps = list(providers) if providers else self._configured_providers()
        out: dict[str, list[ModelInfo]] = {}
        for p in ps:
            s = self._settings_for(p)
            out[p] = discover_models(
                p,
                api_key=s.api_key if s else None,
                base_url=s.base_url if s else None,
                ttl_seconds=self.model_ttl_seconds,
                force_refresh=force_refresh,
            )
        return out

    def refresh_models(self) -> dict[str, list[ModelInfo]]:
        discovered = self.discover_models(force_refresh=True)
        with self._models_lock:
            self._models = {k: list(v) for k, v in discovered.items()}
        return discovered

    def chat(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run a chat completion with fallbacks.

        Returns an OpenAI-style response dict.
        """

        candidates = self._candidate_chain(explicit_model=model)
        last_err: BaseException | None = None

        for settings, resolved_model in candidates:
            try:
                self._rate_limiters[settings.provider].acquire()
                resp = self._chat_once(
                    settings,
                    resolved_model,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    request_id=request_id,
                    **kwargs,
                )

                try:
                    self.cost_tracker.record_from_litellm_response(
                        provider=settings.provider,
                        model=resolved_model,
                        response=resp,
                        request_id=request_id,
                        metadata=metadata,
                        ts=datetime.now(UTC),
                    )
                except Exception:
                    logger.debug("Cost tracking failed", exc_info=True)

                return _as_dict(resp)
            except Exception as e:
                last_err = e
                logger.warning(
                    "LLM chat failed provider=%s model=%s err=%s",
                    settings.provider,
                    resolved_model,
                    e,
                )
                continue

        logger.error("All LLM fallbacks failed")
        if last_err:
            logger.debug("Last LLM error", exc_info=last_err)
        return self._rule_based_fallback(messages, request_id=request_id)

    def get_embedding(
        self,
        *,
        inputs: str | Sequence[str],
        model: str | None = None,
        provider: str | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        litellm = _require_litellm()

        settings = self._settings_for(provider) if provider else self.primary
        if settings is None:
            raise ValueError("LLM provider settings are not configured")
        resolved_model = model or _default_embedding_model(settings.provider)
        full_model = _litellm_model(settings.provider, resolved_model)

        self._rate_limiters[settings.provider].acquire()
        resp = litellm.embedding(
            model=full_model,
            input=inputs,
            api_key=settings.api_key,
            api_base=settings.base_url,
            request_id=request_id,
            timeout=settings.timeout_s,
            **kwargs,
        )

        try:
            self.cost_tracker.record_from_litellm_response(
                provider=settings.provider,
                model=resolved_model,
                response=resp,
                request_id=request_id,
                metadata=metadata,
                ts=datetime.now(UTC),
            )
        except Exception:
            logger.debug("Cost tracking failed (embedding)", exc_info=True)

        return _as_dict(resp)

    def _chat_once(
        self,
        settings: ProviderSettings,
        model: str,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None,
        tool_choice: str | Mapping[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        request_id: str | None,
        **kwargs: Any,
    ) -> Any:
        litellm = _require_litellm()
        full_model = _litellm_model(settings.provider, model)

        params: dict[str, Any] = {
            "model": full_model,
            "messages": list(messages),
            "api_key": settings.api_key,
            "api_base": settings.base_url,
            "request_id": request_id,
            "timeout": settings.timeout_s,
        }
        if temperature is not None:
            params["temperature"] = float(temperature)
        if max_tokens is not None:
            params["max_tokens"] = int(max_tokens)
        if tools is not None:
            params["tools"] = list(tools)
        if tool_choice is not None:
            params["tool_choice"] = tool_choice

        params.update(kwargs)
        return litellm.completion(**params)

    def _candidate_chain(self, *, explicit_model: str | None) -> list[tuple[ProviderSettings, str]]:
        chain: list[tuple[ProviderSettings, str]] = []

        # 1) Primary (allow explicit model override)
        chain.extend(self._candidates_for(self.primary, model=explicit_model))

        # 2) Secondary
        if self.secondary:
            chain.extend(self._candidates_for(self.secondary, model=None))

        # 3) Local
        if self.local:
            chain.extend(self._candidates_for(self.local, model=None))

        return chain

    def _candidates_for(
        self,
        settings: ProviderSettings,
        *,
        model: str | None,
    ) -> list[tuple[ProviderSettings, str]]:
        if model:
            return [(settings, model)]
        if settings.default_model:
            return [(settings, settings.default_model)]

        discovered = self._get_discovered(settings.provider)
        if discovered:
            return [(settings, discovered[0].id)]

        return [(settings, _fallback_default_model(settings.provider))]

    def _get_discovered(self, provider: str) -> list[ModelInfo]:
        with self._models_lock:
            existing = self._models.get(provider)
            if existing:
                return list(existing)

        s = self._settings_for(provider)
        models = discover_models(
            provider,
            api_key=s.api_key if s else None,
            base_url=s.base_url if s else None,
            ttl_seconds=self.model_ttl_seconds,
        )
        with self._models_lock:
            self._models[provider] = list(models)
        return list(models)

    def _settings_for(self, provider: str | None) -> ProviderSettings | None:
        if not provider:
            return None
        p = provider.lower().strip()
        for s in (self.primary, self.secondary, self.local):
            if s and s.provider == p:
                return s
        return None

    def _configured_providers(self) -> list[str]:
        ps = [self.primary.provider]
        if self.secondary:
            ps.append(self.secondary.provider)
        if self.local:
            ps.append(self.local.provider)
        out: list[str] = []
        seen = set()
        for p in ps:
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out

    def _rule_based_fallback(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        user_text = ""
        for m in reversed(list(messages)):
            if m.get("role") == "user":
                user_text = str(m.get("content") or "").strip()
                break

        content = (
            "I cannot reach any configured LLM provider right now. "
            "If you share a zone name/ID and a target temperature, or ask for zone status, "
            "I can proceed once connectivity is restored."
        )
        if user_text:
            content += f"\n\nLast user message: {user_text[:300]}"

        return {
            "id": request_id or "rule-based",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "rule-based",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        }


def _normalize_settings(s: ProviderSettings) -> ProviderSettings:
    p = s.provider.lower().strip()
    if p not in ClimateIQLLMProvider.SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {s.provider}")

    api_key = s.api_key
    base_url = s.base_url

    if p == "openai":
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        base_url = base_url or os.getenv("OPENAI_BASE_URL")
    elif p == "anthropic":
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
    elif p == "gemini":
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        base_url = base_url or os.getenv("GEMINI_BASE_URL")
    elif p == "grok":
        api_key = api_key or os.getenv("GROK_API_KEY")
        base_url = base_url or os.getenv("GROK_BASE_URL")
    elif p == "ollama":
        base_url = base_url or os.getenv("OLLAMA_BASE_URL")
    elif p == "llamacpp":
        base_url = base_url or os.getenv("LLAMACPP_BASE_URL")

    return ProviderSettings(
        provider=p,
        api_key=api_key,
        base_url=base_url,
        default_model=s.default_model,
        rpm=s.rpm,
        timeout_s=s.timeout_s,
    )


def _litellm_model(provider: str, model: str) -> str:
    return f"{provider.lower().strip()}/{model}"


def _fallback_default_model(provider: str) -> str:
    p = provider.lower().strip()
    if p == "anthropic":
        return os.getenv("CLIMATEIQ_ANTHROPIC_FALLBACK_MODEL", "claude-3-5-sonnet-20241022")
    if p == "openai":
        return os.getenv("CLIMATEIQ_OPENAI_FALLBACK_MODEL", "gpt-4o-mini")
    if p == "gemini":
        return os.getenv("CLIMATEIQ_GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
    if p == "grok":
        return os.getenv("CLIMATEIQ_GROK_FALLBACK_MODEL", "grok-2")
    if p == "ollama":
        return os.getenv("CLIMATEIQ_OLLAMA_FALLBACK_MODEL", "llama3.1")
    if p == "llamacpp":
        return os.getenv("CLIMATEIQ_LLAMACPP_FALLBACK_MODEL", "local-model")
    return "unknown"


def _default_embedding_model(provider: str) -> str:
    p = provider.lower().strip()
    if p == "openai":
        return os.getenv("CLIMATEIQ_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    if p == "gemini":
        return os.getenv("CLIMATEIQ_GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    return os.getenv("CLIMATEIQ_EMBEDDING_MODEL", "text-embedding-3-small")


def _as_dict(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    model_dump = getattr(resp, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return dict(dumped)

    if isinstance(resp, Mapping):
        return dict(resp)
    return {"raw": resp}
