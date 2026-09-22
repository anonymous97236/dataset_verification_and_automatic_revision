"""Utility function for deterministic comparison of an inferred action and ground truth."""

from __future__ import annotations

import ast
import re
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from typing import Any, Mapping


class EvaluationResult(IntEnum):
    """Action-evaluation outcomes with both enum and integer semantics."""

    incorrect = 0
    correct = 1


_ACTION_ALIASES = {
    "long_touch": "long_press",
    "longpress": "long_press",
    "tap": "click",
    "end": "terminate",
    "input": "type",
}


def _normalize_action_type(value: Any) -> str:
    action_type = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _ACTION_ALIASES.get(action_type, action_type)


def _motion_direction(
    action_type: str,
    points: tuple[tuple[int, int], tuple[int, int]] | None,
) -> str:
    """Derive a motion direction from the canonical action convention.

    A scroll's named direction is opposite to the vertical finger movement:
    moving top-to-bottom is ``up`` and bottom-to-top is ``down``.  A swipe's
    named direction follows its horizontal finger movement.
    """

    if points is None:
        return ""
    start, end = points
    if action_type == "scroll":
        if end[1] > start[1]:
            return "up"
        if end[1] < start[1]:
            return "down"
    if action_type == "swipe":
        if end[0] < start[0]:
            return "left"
        if end[0] > start[0]:
            return "right"
    return ""


def _integer_coordinates(value: Any) -> list[int]:
    """Parse only integer coordinate values in the sample data's 0--1000 space."""

    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, int) and not isinstance(item, bool)] if all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        ) else []
    if isinstance(value, int) and not isinstance(value, bool):
        return [value]
    if not isinstance(value, str):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    return _integer_coordinates(parsed) if not isinstance(parsed, str) else []


def _point(value: Any) -> tuple[int, int] | None:
    values = _integer_coordinates(value)
    return (values[0], values[1]) if len(values) >= 2 else None


def _two_points(value: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        first, second = _point(value[0]), _point(value[1])
        if first is not None and second is not None:
            return first, second
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
        return _two_points(parsed) if not isinstance(parsed, str) else None
    values = _integer_coordinates(value)
    if len(values) >= 4:
        return (values[0], values[1]), (values[2], values[3])
    return None


def _bbox(value: Any) -> tuple[int, int, int, int] | None:
    values = _integer_coordinates(value)
    if len(values) < 4:
        return None
    x1, y1, x2, y2 = values[:4]
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _in_bbox(point: tuple[int, int] | None, bbox: tuple[int, int, int, int] | None) -> bool:
    if point is None or bbox is None:
        return False
    x, y = point
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().replace("\\'", "'").replace('\\"', '"')
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].replace("\\'", "'").replace('\\"', '"')
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")


def _typed_text(value: Any) -> str:
    return _normalize_text(value)


def _normalized_levenshtein_similarity(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[right_index] + 1, previous[right_index - 1] + (left_char != right_char)))
        previous = current
    return 1.0 - (previous[-1] / max(len(left), len(right)))


def _end_status(value: Any) -> str:
    text = _normalize_text(value).strip().lower()
    return text if text in {"success", "failure"} else ""


def _wait_seconds(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def evaluate_action(ground_truth: Mapping[str, Any], inferred_action: Mapping[str, Any]) -> EvaluationResult:
    """Return whether an inferred action matches the ground-truth action.

    Ground truth is the sample-data ``original_action_annotation`` shape and
    reads only `type`, `parameter`, and `bbox`.  An inferred action reads only
    `type` and `parameter`.  The ground-truth parameter is the reference value
    for type, wait, terminate, scroll/swipe direction, and drag. All coordinates
    must be integers in the 0--1000 range.

    Returns ``EvaluationResult.correct`` (``1``) or
    ``EvaluationResult.incorrect`` (``0``).  As an ``IntEnum``, the result can
    be compared with either its enum member or the corresponding integer.
    """

    gt = dict(ground_truth or {})
    predicted = dict(inferred_action or {})
    gt_type = _normalize_action_type(gt.get("type"))
    predicted_type = _normalize_action_type(predicted.get("type"))
    if not gt_type or gt_type != predicted_type:
        return EvaluationResult.incorrect

    parameter = predicted.get("parameter")
    gt_parameter = gt.get("parameter")

    if gt_type in {"back", "home"}:
        return EvaluationResult.correct
    if gt_type in {"click", "long_press"}:
        return EvaluationResult.correct if _in_bbox(_point(parameter), _bbox(gt.get("bbox"))) else EvaluationResult.incorrect
    if gt_type in {"scroll", "swipe"}:
        points = _two_points(parameter)
        bbox = _bbox(gt.get("bbox"))
        direction = _motion_direction(gt_type, _two_points(gt_parameter))
        if points is None or not all(_in_bbox(point, bbox) for point in points):
            return EvaluationResult.incorrect
        start, end = points
        if gt_type == "scroll":
            return EvaluationResult.correct if (direction == "up" and end[1] > start[1]) or (direction == "down" and start[1] > end[1]) else EvaluationResult.incorrect
        return EvaluationResult.correct if (direction == "left" and start[0] > end[0]) or (direction == "right" and end[0] > start[0]) else EvaluationResult.incorrect
    if gt_type == "drag":
        predicted_points, gt_points = _two_points(parameter), _two_points(gt_parameter)
        if predicted_points is None or gt_points is None:
            return EvaluationResult.incorrect
        tolerance = 25
        return EvaluationResult.correct if all(abs(predicted_coordinate - gt_coordinate) <= tolerance for predicted_point, gt_point in zip(predicted_points, gt_points) for predicted_coordinate, gt_coordinate in zip(predicted_point, gt_point)) else EvaluationResult.incorrect
    if gt_type == "type":
        gt_text = _typed_text(gt_parameter).strip().lower()
        predicted_text = _typed_text(parameter).strip().lower()
        return EvaluationResult.correct if gt_text and predicted_text and _normalized_levenshtein_similarity(predicted_text, gt_text) >= 0.5 else EvaluationResult.incorrect
    if gt_type == "wait":
        return EvaluationResult.correct if _wait_seconds(gt_parameter) == _wait_seconds(parameter) and _wait_seconds(parameter) is not None else EvaluationResult.incorrect
    if gt_type == "terminate":
        gt_status = _end_status(gt_parameter)
        return EvaluationResult.correct if gt_status and gt_status == _end_status(parameter) else EvaluationResult.incorrect
    return EvaluationResult.incorrect
