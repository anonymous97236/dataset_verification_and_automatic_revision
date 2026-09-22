"""Small, dependency-free JSON parsing and schema-subset validation helpers."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from typing import Any

from .errors import LLMResponseError, LLMResponseSchemaError


def recover_json_object(raw_text: str) -> dict[str, Any]:
    """Recover one JSON object while retaining an unrecoverable response.

    Model outputs occasionally contain fenced JSON, trailing commas, or
    unquoted identifier-like keys.  This compact recovery path is deliberately
    shared by every pipeline module.  It does not trigger another API call.
    """

    text = str(raw_text or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    first_object, last_object = text.find("{"), text.rfind("}")
    if first_object >= 0 and last_object > first_object:
        candidates.append(text[first_object : last_object + 1])

    for candidate in dict.fromkeys(candidates):
        for repaired in (
            candidate,
            re.sub(r",\s*([}\]])", r"\1", candidate),
            re.sub(r"([,{]\s*)([A-Za-z_][A-Za-z0-9_ ]*)(\s*:)", r'\1"\2"\3', candidate),
        ):
            try:
                value = json.loads(repaired)
            except json.JSONDecodeError:
                try:
                    value = ast.literal_eval(repaired)
                except (SyntaxError, ValueError):
                    continue
            if isinstance(value, dict):
                return value
    return {"raw_response": text}


def parse_json_response(raw_text: str) -> Any:
    """Parse plain JSON, fenced JSON, or JSON embedded in model text."""

    text = str(raw_text or "").strip()
    if not text:
        raise LLMResponseError("Cannot parse JSON from an empty model response")
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\}|\[[\s\S]*\])\s*```", text, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    first_object, last_object = text.find("{"), text.rfind("}")
    if first_object >= 0 and last_object > first_object:
        candidates.append(text[first_object : last_object + 1])
    last_error: Exception | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
        try:
            return ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            last_error = exc
    raise LLMResponseError(f"Failed to parse a JSON response: {last_error}")


def _matches_type(value: Any, expected_type: str) -> bool:
    return {
        "array": isinstance(value, list),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }.get(expected_type, True)


def _schema_errors(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_matches_type(value, str(item)) for item in expected_type):
            return [f"{path}: expected one of {expected_type}, got {type(value).__name__}"]
    elif isinstance(expected_type, str) and not _matches_type(value, expected_type):
        return [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing required field")
        for key, child_schema in dict(schema.get("properties") or {}).items():
            if key in value and isinstance(child_schema, Mapping):
                errors.extend(_schema_errors(value[key], child_schema, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]"))
    return errors


def validate_json_schema_subset(value: Any, schema: Mapping[str, Any] | None) -> Any:
    """Validate `type`, `required`, `properties`, and `items` from a JSON-schema mapping."""

    if not schema:
        return value
    errors = _schema_errors(value, schema)
    if errors:
        raise LLMResponseSchemaError("; ".join(errors))
    return value
