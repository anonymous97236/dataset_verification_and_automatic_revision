"""Environment-only configuration for Gemini and Qwen providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from .errors import LLMConfigurationError

ProviderName = Literal["gemini", "qwen"]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise LLMConfigurationError(f"Missing required environment variable: {name}")
    return value


def _optional_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def _optional_positive_float(name: str, default: float) -> float:
    value = _optional_float(name, default)
    if value <= 0:
        raise LLMConfigurationError(f"{name} must be positive, got {value}")
    return value


def _optional_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise LLMConfigurationError(f"{name} must be a boolean, got {raw!r}")


@dataclass(frozen=True)
class ProviderConfig:
    """Connection and generation settings for one provider.

    `base_url` and `enable_thinking` apply only to Qwen.  Qwen is expected to be
    served by an OpenAI-compatible API, such as vLLM.
    """

    provider: ProviderName
    model: str
    api_key: str
    timeout_seconds: float = 180.0
    temperature: float = 0.0
    base_url: str | None = None
    enable_thinking: bool = False


def load_gemini_config() -> ProviderConfig:
    """Load the paper's Gemini provider settings from `GEMINI_*` variables."""

    return ProviderConfig(
        provider="gemini",
        api_key=_required_env("GEMINI_API_KEY"),
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash",
        temperature=_optional_float("LLM_TEMPERATURE", 0.0),
        timeout_seconds=_optional_positive_float("LLM_TIMEOUT_SECONDS", 180.0),
    )


def load_qwen_config() -> ProviderConfig:
    """Load Qwen URL/model settings; local OpenAI-compatible servers use `not-needed` as their key."""

    return ProviderConfig(
        provider="qwen",
        base_url=_required_env("QWEN_BASE_URL").rstrip("/"),
        api_key="not-needed",
        model=_required_env("QWEN_MODEL"),
        enable_thinking=_optional_bool("QWEN_ENABLE_THINKING", False),
        temperature=_optional_float("LLM_TEMPERATURE", 0.0),
        timeout_seconds=_optional_positive_float("LLM_TIMEOUT_SECONDS", 180.0),
    )
