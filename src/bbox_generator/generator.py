"""Bounding-box generation with Gemini or Qwen for revised reference actions."""

from __future__ import annotations

import ast
import base64
import io
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from llm import (
    LLMInputError, LLMRequest, LLMResponseError, ProviderConfig,
    call_llm, load_gemini_config, load_qwen_config,
)
from llm.images import normalize_images
from prompts.bbox_generator_prompt import (
    PROMPT_BBOX_INTENT_CLICK,
    PROMPT_BBOX_INTENT_SCROLL,
    PROMPT_BBOX_INTENT_SWIPE,
    PROMPT_GENERATE_BBOX_BOTTOM,
    PROMPT_GENERATE_BBOX_LEFT,
    PROMPT_GENERATE_BBOX_RIGHT,
    PROMPT_GENERATE_BBOX_TOP,
)
from utils import overlay_action_parameter, overlay_axis_ticks
from utils.configuration import provider_for_role
from utils.output_store import output_data_path, read_output_snapshot, update_step_fields

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "Target": {"type": "string"},
        "Top": {"type": "string"},
        "Bottom": {"type": "string"},
        "Left": {"type": "string"},
        "Right": {"type": "string"},
    },
    "required": ["Target", "Top", "Bottom", "Left", "Right"],
}
_ACTION_ALIASES = {
    "tap": "click",
    "long_touch": "long_press",
    "longpress": "long_press",
}
_AXIS_PROMPTS = {
    "top": PROMPT_GENERATE_BBOX_TOP,
    "bottom": PROMPT_GENERATE_BBOX_BOTTOM,
    "left": PROMPT_GENERATE_BBOX_LEFT,
    "right": PROMPT_GENERATE_BBOX_RIGHT,
}

ProgressCallback = Callable


@dataclass(frozen=True)
class BBoxGenerationResult:
    """One stored bbox and the path of the updated pipeline output."""

    bbox: list[int]
    output_path: Path
    source: str


class BBoxGenerator:
    """Generate a 0--1000 integer ``[x1, y1, x2, y2]`` bbox with five model calls.

    Args:
        config: Optional explicit Gemini or Qwen provider configuration.
            Otherwise, ``bbox_generator`` in ``config.yaml`` selects the
            provider, whose connection settings come from the environment.
            All five calls share one provider configuration.

    ``generate`` accepts a local image path, bytes, a supported ``llm`` image
    mapping, or a Pillow image. ``generate_for_output`` reads the selected
    output step and writes the resulting bbox into ``reference_action``.
    """

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.provider = config.provider if config is not None else provider_for_role("bbox_generator")
        if self.provider not in {"gemini", "qwen"}:
            raise LLMInputError("BBoxGenerator requires a Gemini or Qwen provider")
        self._config = config

    def _provider_config(self) -> ProviderConfig:
        if self._config is not None:
            return self._config
        return load_gemini_config() if self.provider == "gemini" else load_qwen_config()

    def generate(
        self,
        screenshot: Any,
        *,
        operation: str,
        action_type: str,
        action_parameter: Any,
    ) -> list[int]:
        """Return one bbox after one intent call and four edge calls."""

        bbox, _intents = self._generate_bbox(screenshot, operation, action_type, action_parameter)
        return bbox

    def _generate_bbox(
        self,
        screenshot: Any,
        operation: str,
        action_type: str,
        action_parameter: Any,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[int], dict[str, str]]:
        """Return one bbox together with its five generated intent values."""

        action = _normalize_action_type(action_type)
        operation_text = str(operation or "").strip()
        if not operation_text:
            raise LLMInputError("operation must be a non-empty string")
        points = _action_points(action, action_parameter)
        image = _load_image(screenshot)
        action_payload = {"type": action, "parameter": action_parameter}
        config = self._provider_config()

        intents = self._generate_intents(
            overlay_action_parameter(image, action_payload), action, points, operation_text, config
        )
        _report_progress(progress_callback, {
            "stage": "intents",
            "intents": _generation_intents(intents),
        })
        axis_images = {
            axis: overlay_axis_ticks(overlay_action_parameter(image, action_payload, marker_scale=0.5), axis)
            for axis in ("top", "bottom", "left", "right")
        }
        # The four independent edge requests share the same intent but are
        # deliberately issued at once; this does not introduce retries.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="bbox-edge") as executor:
            requests = {
                axis: executor.submit(self._generate_edge, axis_images[axis], axis, intents, config)
                for axis in axis_images
            }
            edges: dict[str, int] = {}
            completed_axes = {request: axis for axis, request in requests.items()}
            for completed in as_completed(completed_axes):
                axis = completed_axes[completed]
                coordinate = completed.result()
                edges[axis] = coordinate
                _report_progress(progress_callback, {
                    "stage": "edge",
                    "axis": axis,
                    "coordinate": coordinate,
                })
        bbox = [edges["left"], edges["top"], edges["right"], edges["bottom"]]
        if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
            raise LLMResponseError(f"Model returned crossed bbox edges: {bbox}")
        return bbox, intents

    def generate_for_output(
        self,
        episode_id: str,
        output_id: str,
        step_index: int,
        *,
        repository_root: str | Path | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> BBoxGenerationResult:
        """Create and persist ``reference_action.bbox`` for one output step.

        Each call replaces any existing bbox. For click and long_press
        actions, the smallest OD bbox containing the action point is stored
        before any model call. All other eligible actions use the five-call
        flow with the configured provider.
        """

        root = Path(repository_root).resolve() if repository_root is not None else _repository_root()
        output = read_output_snapshot(root, episode_id, output_id)
        steps = output.get("steps")
        if not isinstance(step_index, int) or isinstance(step_index, bool) or not isinstance(steps, list) or not 0 <= step_index < len(steps):
            raise LLMInputError("step_index is outside the selected output")
        step = steps[step_index]
        if not isinstance(step, Mapping):
            raise LLMInputError("The selected output step is invalid")
        reference = step.get("reference_action")
        if not isinstance(reference, Mapping):
            raise LLMInputError("The selected step does not contain reference_action")

        fields = _normalized_fields(reference)
        action = _normalize_action_type(fields.get("actiontype", fields.get("type")))
        parameter = fields.get("actionparameter", fields.get("parameter"))
        points = _action_points(action, parameter)
        operation = str(fields.get("operation") or step.get("operation") or "").strip()
        if not operation:
            raise LLMInputError("reference_action must contain an operation")

        bbox = None
        intents: dict[str, str] | None = None
        source = self.provider
        if action in {"click", "long_press"}:
            bbox = _od_bbox_for_point(_read_od_results(root, episode_id, step), points[0])
            if bbox is not None:
                source = "od_results"
        if bbox is None:
            screenshot = root / "sample_data" / _simple_name(episode_id, "episode_id") / "screenshots" / f"{_step_id(step)}.png"
            bbox, intents = self._generate_bbox(
                screenshot,
                operation,
                action,
                parameter,
                progress_callback,
            )

        updated_reference = dict(reference)
        updated_reference["bbox"] = bbox
        updates: dict[str, Any] = {"reference_action": updated_reference}
        if source == "od_results":
            updates["reference_bbox_generation"] = {
                "source": "od_results",
                "Target": "Use the bounding-box information from Object Detection.",
            }
        elif intents is not None:
            updates["reference_bbox_generation"] = {
                "source": source,
                "Target": intents["bbox_intent"],
                "Top": intents["top_intent"],
                "Bottom": intents["bottom_intent"],
                "Left": intents["left_intent"],
                "Right": intents["right_intent"],
            }
        destination = update_step_fields(
            root,
            episode_id=episode_id,
            output_id=output_id,
            step_index=step_index,
            updates=updates,
        )
        return BBoxGenerationResult(bbox, destination, source)

    def _generate_intents(
        self,
        image: Image.Image,
        action: str,
        points: list[tuple[int, int]],
        operation: str,
        config: ProviderConfig,
    ) -> dict[str, str]:
        if action in {"click", "long_press"}:
            template, data = PROMPT_BBOX_INTENT_CLICK, {
                "click_coordinate": _format_point(points[0]),
                "operation": operation,
            }
        elif action == "scroll":
            template, data = PROMPT_BBOX_INTENT_SCROLL, {
                "starting_point": _format_point(points[0]),
                "ending_point": _format_point(points[1]),
                "operation": operation,
            }
        else:
            template, data = PROMPT_BBOX_INTENT_SWIPE, {
                "starting_point": _format_point(points[0]),
                "ending_point": _format_point(points[1]),
                "operation": operation,
            }
        result = call_llm(
            config,
            LLMRequest(
                prompt=template,
                prompt_data=data,
                images=(_image_bytes(image),),
                json_schema=_INTENT_SCHEMA,
            ),
        )
        if not isinstance(result.parsed, Mapping):
            raise LLMResponseError("Model bbox-intent response is not a JSON object")
        return _extract_intents(result.parsed)

    def _generate_edge(
        self, image: Image.Image, axis: str, intents: Mapping[str, str], config: ProviderConfig
    ) -> int:
        result = call_llm(
            config,
            LLMRequest(
                prompt=_AXIS_PROMPTS[axis],
                prompt_data={"bbox_intent": intents["bbox_intent"], f"{axis}_intent": intents[f"{axis}_intent"]},
                images=(_image_bytes(image),),
                max_tokens=32,
            ),
        )
        return _extract_coordinate(result.raw_text)


def _load_image(value: Any) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.convert("RGB")
    normalized = normalize_images([value])
    if len(normalized) != 1:
        raise LLMInputError("screenshot must contain exactly one image")
    try:
        return Image.open(io.BytesIO(base64.b64decode(normalized[0]["data"]))).convert("RGB")
    except Exception as exc:
        raise LLMInputError("screenshot could not be decoded as an image") from exc


def _image_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return output.getvalue()


def _normalize_action_type(value: str) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    action = _ACTION_ALIASES.get(action, action)
    if action not in {"click", "long_press", "scroll", "swipe"}:
        raise LLMInputError("action_type must be click, long_press, scroll, or swipe")
    return action


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _simple_name(value: Any, field_name: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise LLMInputError(f"{field_name} must be a simple name")
    return name


def _step_id(step: Mapping[str, Any]) -> str:
    return _simple_name(step.get("step_id"), "step_id")


def _normalized_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "".join(character for character in str(name).casefold() if character.isalnum()): item
        for name, item in value.items()
    }


def _generation_intents(intents: Mapping[str, str]) -> dict[str, str]:
    return {
        "Target": intents["bbox_intent"],
        "Top": intents["top_intent"],
        "Bottom": intents["bottom_intent"],
        "Left": intents["left_intent"],
        "Right": intents["right_intent"],
    }


def _report_progress(callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if callback is not None:
        callback(payload)


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
    x1, y1, x2, y2 = parsed
    if not all(0 <= coordinate <= 1000 for coordinate in parsed) or x1 >= x2 or y1 >= y2:
        return None
    return [x1, y1, x2, y2]


def _read_od_results(root: Path, episode_id: str, step: Mapping[str, Any]) -> Any:
    path = root / "sample_data" / _simple_name(episode_id, "episode_id") / "od_results" / f"{_step_id(step)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        raise LLMInputError(f"OD results for {_step_id(step)} are not valid JSON") from exc


def _od_bbox_for_point(value: Any, point: tuple[int, int]) -> list[int] | None:
    items = value if isinstance(value, list) else value.get("results", []) if isinstance(value, Mapping) else []
    selected: list[int] | None = None
    selected_area: int | None = None
    for item in items:
        if not isinstance(item, Mapping):
            continue
        fields = _normalized_fields(item)
        bbox = _bbox(fields.get("bbox", fields.get("box", fields.get("zerotoonebbox"))))
        if bbox is None:
            continue
        x1, y1, x2, y2 = bbox
        if not (x1 <= point[0] <= x2 and y1 <= point[1] <= y2):
            continue
        area = (x2 - x1) * (y2 - y1)
        if selected is None or area < selected_area:
            selected, selected_area = bbox, area
    return selected


def _action_points(action: str, value: Any) -> list[tuple[int, int]]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise LLMInputError("action_parameter must use the canonical point tuple format") from exc
    required = 1 if action in {"click", "long_press"} else 2
    raw_points: list[tuple[Any, Any]] = []
    if action in {"click", "long_press"} and isinstance(parsed, (list, tuple)) and len(parsed) == 2:
        raw_points = [(parsed[0], parsed[1])]
    elif action in {"scroll", "swipe"} and isinstance(parsed, (list, tuple)) and len(parsed) == 2:
        if all(isinstance(point, (list, tuple)) and len(point) == 2 for point in parsed):
            raw_points = [(point[0], point[1]) for point in parsed]
    if len(raw_points) != required:
        raise LLMInputError(f"{action} requires {required} point(s) in action_parameter")

    points: list[tuple[int, int]] = []
    for x_raw, y_raw in raw_points[:required]:
        if not all(isinstance(coordinate, int) and not isinstance(coordinate, bool) for coordinate in (x_raw, y_raw)):
            raise LLMInputError("action_parameter coordinates must be 0--1000 integers")
        x, y = x_raw, y_raw
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            raise LLMInputError("action_parameter coordinates must be in 0--1000")
        points.append((x, y))
    return points


def _format_point(point: tuple[int, int]) -> str:
    return f"({point[0]}, {point[1]})"


def _extract_intents(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise LLMResponseError("Model bbox-intent response must be a JSON object")
    fields = {
        "bbox_intent": "Target",
        "top_intent": "Top",
        "bottom_intent": "Bottom",
        "left_intent": "Left",
        "right_intent": "Right",
    }
    extracted: dict[str, str] = {}
    for name, field in fields.items():
        text = str(value.get(field) or "").strip()
        if not text:
            raise LLMResponseError(f"Model bbox-intent response is missing {name}")
        extracted[name] = text
    return extracted


def _extract_coordinate(raw_text: str) -> int:
    text = str(raw_text or "").strip()
    if not text:
        raise LLMResponseError("Model returned an empty bbox coordinate")
    try:
        value = int(text)
    except ValueError as exc:
        raise LLMResponseError(f"Model bbox coordinate is not an integer: {text!r}") from exc
    if not 0 <= value <= 1000:
        raise LLMResponseError(f"Model bbox coordinate is outside 0--1000: {value}")
    return value


__all__ = ["BBoxGenerationResult", "BBoxGenerator"]
