"""Common Gemini and Qwen API interface for the compact pipeline."""

from __future__ import annotations

import base64
import string
from dataclasses import dataclass
from typing import Any

from .config import ProviderConfig
from .errors import LLMConfigurationError, LLMInputError, LLMResponseError
from .images import normalize_images
from .json_utils import parse_json_response, validate_json_schema_subset


@dataclass(frozen=True)
class LLMRequest:
    """A provider-neutral request.

    Images retain their given order; the paper uses this to distinguish previous,
    current-plain, and current-grid screenshots.
    """

    prompt: str
    system_prompt: str | None = None
    images: tuple[Any, ...] = ()
    prompt_data: dict[str, Any] | None = None
    json_schema: dict[str, Any] | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class LLMCallResult:
    provider: str
    model: str
    prompt: str
    raw_text: str
    parsed: Any | None
    usage: dict[str, int | None]


def render_prompt(template: str, data: dict[str, Any] | None = None) -> str:
    """Render simple `{placeholder}` templates and fail early on missing values."""

    if data is None:
        return str(template)
    fields = [name for _, name, _, _ in string.Formatter().parse(template) if name]
    missing = [name for name in fields if name not in data]
    if missing:
        raise LLMInputError(f"Missing prompt placeholders: {', '.join(sorted(set(missing)))}")
    try:
        return template.format_map(data)
    except (KeyError, ValueError) as exc:
        raise LLMInputError(f"Failed to render prompt: {exc}") from exc


def _gemini_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": _read_usage(usage, "prompt_token_count", "promptTokenCount"),
        "completion_tokens": _read_usage(usage, "candidates_token_count", "candidatesTokenCount"),
        "total_tokens": _read_usage(usage, "total_token_count", "totalTokenCount"),
    }


def _openai_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": _read_usage(usage, "prompt_tokens"),
        "completion_tokens": _read_usage(usage, "completion_tokens"),
        "total_tokens": _read_usage(usage, "total_tokens"),
    }


def _read_usage(value: Any, *names: str) -> int | None:
    for name in names:
        item = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if item is not None:
            try:
                return int(item)
            except (TypeError, ValueError):
                pass
    return None


def _maybe_parse(raw_text: str, request: LLMRequest) -> Any | None:
    if request.json_schema is None:
        return None
    return validate_json_schema_subset(parse_json_response(raw_text), request.json_schema)


def _call_gemini(config: ProviderConfig, request: LLMRequest, prompt: str) -> LLMCallResult:
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        raise LLMConfigurationError(
            "Gemini support requires google-genai. Install the project dependencies with `pip install -r requirements.txt`."
        ) from exc

    images = normalize_images(request.images)
    contents: list[Any] = [prompt]
    for image in images:
        contents.append(
            genai_types.Part.from_bytes(
                data=base64.b64decode(image["data"]), mime_type=image["mime_type"]
            )
        )

    generation_options: dict[str, Any] = {"temperature": config.temperature}
    if request.system_prompt:
        generation_options["system_instruction"] = request.system_prompt
    if request.json_schema is not None:
        generation_options["response_mime_type"] = "application/json"
        generation_options["response_schema"] = request.json_schema

    client = genai.Client(
        api_key=config.api_key,
        # google-genai's HttpOptions timeout is measured in milliseconds.
        http_options={
            "api_version": "v1beta",
            "timeout": int(config.timeout_seconds * 1000),
            "retry_options": {"attempts": 1},
        },
    )
    response = client.models.generate_content(
        model=config.model,
        contents=contents if images else prompt,
        config=genai_types.GenerateContentConfig(**generation_options),
    )
    raw_text = str(getattr(response, "text", "") or "").strip()
    if not raw_text:
        raise LLMResponseError("Gemini returned an empty response")
    return LLMCallResult(
        provider="gemini",
        model=config.model,
        prompt=prompt,
        raw_text=raw_text,
        parsed=_maybe_parse(raw_text, request),
        usage=_gemini_usage(response),
    )


def _call_qwen(config: ProviderConfig, request: LLMRequest, prompt: str) -> LLMCallResult:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError(
            "Qwen support requires openai. Install the project dependencies with `pip install -e .`."
        ) from exc

    images = normalize_images(request.images)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image['mime_type']};base64,{image['data']}"},
            }
        )
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": content})

    options: dict[str, Any] = {"temperature": config.temperature}
    if request.max_tokens is not None:
        options["max_tokens"] = request.max_tokens
    # This Qwen/vLLM extension configures the local serving endpoint.
    options["extra_body"] = {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": config.enable_thinking},
    }
    client = OpenAI(base_url=config.base_url, api_key=config.api_key, timeout=config.timeout_seconds, max_retries=0)
    response = client.chat.completions.create(model=config.model, messages=messages, **options)
    raw_text = ""
    if response.choices:
        raw_text = str(response.choices[0].message.content or "").strip()
    if not raw_text:
        raise LLMResponseError("Qwen returned an empty response")
    return LLMCallResult(
        provider="qwen",
        model=config.model,
        prompt=prompt,
        raw_text=raw_text,
        parsed=_maybe_parse(raw_text, request),
        usage=_openai_usage(response),
    )


def call_llm(config: ProviderConfig, request: LLMRequest) -> LLMCallResult:
    """Call the configured Gemini or Qwen endpoint exactly once.

    This reproducibility material deliberately implements no retry, backoff, or
    provider failover policy. Callers receive the first response or exception.
    """

    prompt = render_prompt(request.prompt, request.prompt_data)
    if config.provider == "gemini":
        return _call_gemini(config, request, prompt)
    if config.provider == "qwen":
        return _call_qwen(config, request, prompt)
    raise LLMInputError(f"Unsupported provider: {config.provider!r}")
