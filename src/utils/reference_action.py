"""Normalize the reference action stored in one output-snapshot step."""

from __future__ import annotations

import ast
from typing import Any, Mapping

from .action_evaluate import _normalize_action_type


_ACTION_TYPES = frozenset({
    "terminate", "click", "scroll", "swipe", "type", "back", "wait", "long_press", "home", "drag",
})


def _fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "".join(character for character in str(name).casefold() if character.isalnum()): item
        for name, item in value.items()
    }


def _has_parameter(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _bbox(value: Any) -> list[int] | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
    if not isinstance(parsed, (list, tuple)) or len(parsed) != 4:
        return None
    if not all(isinstance(coordinate, int) and not isinstance(coordinate, bool) for coordinate in parsed):
        return None
    if not all(0 <= coordinate <= 1000 for coordinate in parsed):
        return None
    return list(parsed)


def _normalized_action(value: Mapping[str, Any], *, default_operation: Any) -> dict[str, Any] | None:
    fields = _fields(value)
    action_type = _normalize_action_type(fields.get("actiontype", fields.get("type")))
    parameter = fields.get("actionparameter", fields.get("parameter"))
    if action_type not in _ACTION_TYPES or (action_type not in {"back", "home"} and not _has_parameter(parameter)):
        return None
    action = {
        "type": action_type,
        "parameter": parameter,
        "operation": fields.get("operation", default_operation),
    }
    bbox = _bbox(fields.get("targetboundingbox", fields.get("bbox")))
    if bbox is not None:
        action["bbox"] = bbox
    return action


def resolve_reference_action(step: Mapping[str, Any]) -> dict[str, Any]:
    """Prefer a complete revised reference action, otherwise use the original annotation.

    The returned mapping uses ``type``, ``parameter``, and ``operation``.  A
    ``bbox`` key is included only when the selected source contains a valid
    0--1000 integer bounding box.
    """

    source = step.get("reference_action")
    if isinstance(source, Mapping):
        parsed = _normalized_action(source, default_operation=step.get("operation"))
        if parsed is not None:
            return parsed
    original = step.get("original_action_annotation")
    return _normalized_action(original, default_operation=step.get("operation")) if isinstance(original, Mapping) else {
        "type": "", "parameter": None, "operation": step.get("operation"),
    }
