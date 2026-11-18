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

import hashlib
import logging
import os
import re
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
        "deepseek": "deepseek-chat",
        "grok": "grok-2-latest",
        "ollama": "llama3.1",
        "llamacpp": "gpt-3.5-turbo",  # llamacpp servers serve whatever model is loaded
    }

    # Providers that reliably follow the OpenAI tool-calling schema.
    # Small local models (Ollama qwen 4B, llama 3.1 8B, llama.cpp) often
    # echo the tool schema back as plain text instead of emitting
    # tool_calls, which surfaces as garbage in the chat UI. For those
    # providers we drop `tools` and let the model answer as plain text.
    DEFAULT_TIMEOUT_S: float = 120.0
    TOOL_CAPABLE_PROVIDERS: ClassVar[frozenset[str]] = frozenset(
        {"anthropic", "openai", "gemini", "deepseek", "grok"}
    )

    def __init__(
        self,
        provider: str,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        base_url: str | None = None,
        fallbacks: list[LLMProvider] | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model or self.PROVIDER_MODELS.get(provider, "gpt-4o")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
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
        if self.provider == "openai":
            model_str = self.model
        elif self.provider == "llamacpp":
            # llama.cpp exposes an OpenAI-compatible endpoint; route via openai/
            model_str = f"openai/{self.model}"
        elif self.provider == "ollama":
            # `ollama_chat/` hits Ollama's /api/chat endpoint which supports
            # tool calling; the legacy `ollama/` prefix hits /api/generate
            # and silently drops the `tools` parameter.
            model_str = f"ollama_chat/{self.model}"
        else:
            model_str = f"{self.provider}/{self.model}"

        # Drop `tools` for providers that don't reliably tool-call.
        # Otherwise small local models echo the schema back as text.
        if tools and self.provider not in self.TOOL_CAPABLE_PROVIDERS:
            logger.debug(
                "Dropping tools for provider=%s (not in TOOL_CAPABLE_PROVIDERS)",
                self.provider,
            )
            tools = None

        call_kwargs: dict[str, Any] = {
            "model": model_str,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": tools if tools else None,
        }
        if _is_gemini3(self.provider, self.model):
            call_kwargs.pop("temperature")  # Gemini 3: keep the default (1.0)
            call_kwargs["max_tokens"] = max(self.max_tokens, _GEMINI3_MIN_OUTPUT_TOKENS)
            call_kwargs.setdefault("reasoning_effort", "low")
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["api_base"] = self.base_url
        call_kwargs.update(kwargs)
        # litellm's default request timeout is 6000 s.  A hung Ollama would
        # otherwise pin a chat request (and its DB session) for 100 minutes.
        call_kwargs.setdefault("timeout", self.DEFAULT_TIMEOUT_S)

        pause_key = _cooldown_key(self.provider, self.model, self.api_key)
        paused_for = _PROVIDER_COOLDOWN_UNTIL.get(pause_key, 0.0) - time.monotonic()
        if paused_for > 0:
            raise RuntimeError(
                f"{self.provider} is rate limited (free-tier quota) — retry in {paused_for:.0f}s"
            )
        try:
            response = await litellm.acompletion(**call_kwargs)
        except Exception as exc:
            pause = _quota_cooldown_s(exc)
            if pause is not None:
                _PROVIDER_COOLDOWN_UNTIL[pause_key] = time.monotonic() + pause
                logger.warning("LLM provider %s is rate limited — pausing %.0fs", self.provider, pause)
            raise

        # Extract response content
        choice = response.choices[0]
        content = choice.message.content or ""

        # Reasoning models (qwen3, deepseek-r1) wrap their draft in <think>…</think>:
        # it is not part of the answer and breaks JSON parsing of extractions.
        result: dict[str, Any] = {"content": strip_reasoning(content)}

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


def strip_reasoning(content: str) -> str:
    """Remove ``<think>…</think>`` blocks (and an unterminated trailing one)."""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    cleaned = re.sub(r"<think>.*\Z", "", cleaned, flags=re.S)
    return cleaned.strip() if cleaned != content else content


# --- Gemini support -----------------------------------------------------------
# Gemini 3 models "think" before answering and count those hidden tokens against
# max_tokens, so a small budget (the advisor asks for 250) is spent before the JSON
# is finished and the reply is cut off.  They also recommend leaving temperature at
# its default of 1.0 (lower values can loop / degrade).  The free tier is capped at
# a handful of requests per minute and a daily quota.
_GEMINI3_MIN_OUTPUT_TOKENS = 2048
_GEMINI_MAX_RPM = 10  # free-tier flash-lite is ~15 RPM; stay clear of it


def _is_gemini3(provider: str, model: str | None) -> bool:
    name = (model or "").lower().removeprefix("models/").removeprefix("gemini/")
    return provider.lower().strip() == "gemini" and name.startswith("gemini-3")


def _quota_cooldown_s(exc: BaseException) -> float | None:
    """Seconds to pause a provider after a rate-limit / quota error, else None.

    Deliberately strict: only a real rate-limit signal counts.  A bare "quota" or
    "429" substring also matches unrelated failures (a 403 "requires a quota
    project", an id like "1429") that must not silence the provider.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    status = getattr(exc, "status_code", None)
    if not (
        status == 429
        or "ratelimit" in type(exc).__name__.lower()
        or "resource_exhausted" in text
        or re.search(r"\b429\b", text)
        or ("quota" in text and "exceed" in text)
    ):
        return None
    if "per day" in text or "perday" in text or "daily" in text:
        return 3600.0  # the daily quota will not recover within the hour
    match = re.search(r"retry(?: in|delay)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*(ms|s)\b", text)
    if match:
        seconds = float(match.group(1)) / (1000.0 if match.group(2) == "ms" else 1.0)
        return min(max(seconds + 1.0, 5.0), 300.0)
    return 60.0


def _cooldown_key(provider: str, model: str | None, api_key: str | None) -> str:
    """Quotas are per project (key) and per model: pause exactly that combination."""
    digest = hashlib.sha256((api_key or "").encode()).hexdigest()[:8]
    return f"{provider.lower().strip()}|{(model or '').lower()}|{digest}"


# Shared across provider instances: the advisor builds a new ClimateIQLLMProvider
# for every call, so per-instance limiter/cooldown state would never accumulate.
_SHARED_LIMITERS: dict[tuple[str, int], _RateLimiter] = {}
_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}
_SHARED_LOCK = threading.Lock()


def _require_litellm() -> Any:
    try:
        import litellm

        # Silently drop model-specific unsupported params (e.g. GPT-5 rejects
        # temperature != 1; Anthropic rejects certain OpenAI-only params).
        # Without this, a chat call with the "wrong" model raises
        # UnsupportedParamsError before we get a chance to fall back.
        litellm.drop_params = True

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
    SUPPORTED_PROVIDERS = (
        "anthropic", "openai", "gemini", "grok", "deepseek", "ollama", "llamacpp",
    )

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
                with _SHARED_LOCK:
                    limiter = _SHARED_LIMITERS.setdefault(
                        (s.provider, s.rpm), _RateLimiter(s.rpm)
                    )
                self._rate_limiters[s.provider] = limiter

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
            pause_key = _cooldown_key(settings.provider, resolved_model, settings.api_key)
            paused_until = _PROVIDER_COOLDOWN_UNTIL.get(pause_key, 0.0)
            if time.monotonic() < paused_until:
                logger.debug(
                    "LLM provider %s paused for %.0fs more (rate limit)",
                    settings.provider, paused_until - time.monotonic(),
                )
                continue
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
                pause = _quota_cooldown_s(e)
                if pause is not None:
                    # Out of quota (e.g. the Gemini free tier): stop calling for a
                    # while instead of failing on every tick; callers fall back.
                    _PROVIDER_COOLDOWN_UNTIL[pause_key] = time.monotonic() + pause
                    logger.warning(
                        "LLM provider %s is rate limited — pausing it for %.0fs",
                        settings.provider, pause,
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
        gemini3 = _is_gemini3(settings.provider, model)
        if temperature is not None and not gemini3:
            params["temperature"] = float(temperature)
        if max_tokens is not None:
            tokens = int(max_tokens)
            # Hidden "thinking" tokens share this budget on Gemini 3.
            params["max_tokens"] = max(tokens, _GEMINI3_MIN_OUTPUT_TOKENS) if gemini3 else tokens
        if tools is not None:
            params["tools"] = list(tools)
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
        # JSON mode (valid JSON guaranteed by Gemini's responseMimeType); other
        # providers ignore the flag.
        if kwargs.pop("json_mode", False) and settings.provider == "gemini":
            params["response_format"] = {"type": "json_object"}
        if gemini3:
            # Short, cheap, deterministic thinking for control decisions.
            params.setdefault("reasoning_effort", "low")

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
            # Machine-readable marker: this is ClimateIQ's own text, NOT a model
            # reply.  Non-conversational callers (e.g. ClimateAdvisor) must check
            # this instead of trying to parse the prose as a decision.
            "climateiq_provider_unavailable": True,
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
    rpm = s.rpm

    if p == "openai":
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        base_url = base_url or os.getenv("OPENAI_BASE_URL")
    elif p == "anthropic":
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
    elif p == "gemini":
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        base_url = base_url or os.getenv("GEMINI_BASE_URL")
        rpm = min(rpm, _GEMINI_MAX_RPM)
    elif p == "grok":
        api_key = api_key or os.getenv("GROK_API_KEY")
        base_url = base_url or os.getenv("GROK_BASE_URL")
    elif p == "deepseek":
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        base_url = base_url or os.getenv("DEEPSEEK_BASE_URL")
    elif p == "ollama":
        base_url = base_url or os.getenv("OLLAMA_BASE_URL")
    elif p == "llamacpp":
        base_url = base_url or os.getenv("LLAMACPP_BASE_URL")

    return ProviderSettings(
        provider=p,
        api_key=api_key,
        base_url=base_url,
        default_model=s.default_model,
        rpm=rpm,
        timeout_s=s.timeout_s,
    )


def _litellm_model(provider: str, model: str) -> str:
    p = provider.lower().strip()
    if p == "ollama":
        # Route Ollama through /api/chat so tool calling works.
        return f"ollama_chat/{model}"
    if p == "llamacpp":
        # llama.cpp's OpenAI-compatible endpoint.
        return f"openai/{model}"
    return f"{p}/{model}"


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
    if p == "deepseek":
        return os.getenv("CLIMATEIQ_DEEPSEEK_FALLBACK_MODEL", "deepseek-chat")
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
