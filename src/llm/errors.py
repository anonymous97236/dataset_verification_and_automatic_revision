"""Explicit errors raised by the standalone LLM utilities."""


class LLMError(RuntimeError):
    """Base class for errors raised by this package."""


class LLMConfigurationError(LLMError):
    """A required model-provider setting is missing or invalid."""


class LLMInputError(LLMError):
    """The prompt, image input, or provider selection is invalid."""


class LLMResponseError(LLMError):
    """A provider returned an empty or unparsable response."""


class LLMResponseSchemaError(LLMResponseError):
    """A JSON response does not meet the supplied schema subset."""
