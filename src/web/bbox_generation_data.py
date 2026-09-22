"""Output-snapshot views for the Bounding Box Generation Test page."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping

from llm import pillow_image_to_data_url
from utils.action_evaluate import _normalize_action_type
from utils.output_store import list_output_ids, read_output_snapshot
from utils.visualization import overlay_action_parameter

from .actor_data import _load_current_screenshot, _read_episode, _sample_root, _simple_name
from utils.configuration import repository_root


_BBOX_ACTIONS = frozenset({"click", "long_press", "scroll", "swipe"})


def _fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "".join(character for character in str(name).casefold() if character.isalnum()): item
        for name, item in value.items()
    }


def _reference_action(step: Mapping[str, Any]) -> dict[str, Any] | None:
    value = step.get("reference_action")
    if not isinstance(value, Mapping):
        return None
    fields = _fields(value)
    action_type = _normalize_action_type(fields.get("actiontype", fields.get("type")))
    if action_type not in _BBOX_ACTIONS:
        return None
    return {
        "type": action_type,
        "parameter": fields.get("actionparameter", fields.get("parameter")),
        "operation": fields.get("operation") or step.get("operation"),
        "bbox": fields.get("bbox", fields.get("targetboundingbox")),
    }


def _bbox(value: Any) -> list[int] | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
    if not isinstance(parsed, (list, tuple)) or len(parsed) != 4:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 1000 for item in parsed):
        return None
    return list(parsed)


def _eligible_step_indices(output: Mapping[str, Any]) -> list[int]:
    steps = output.get("steps")
    if not isinstance(steps, list):
        return []
    return [
        index for index, step in enumerate(steps)
        if isinstance(step, Mapping) and step.get("index") == index and _reference_action(step) is not None
    ]


def _eligible_output_ids(repository: Path, episode_id: str) -> list[str]:
    eligible = []
    for output_id in list_output_ids(repository, episode_id):
        try:
            if _eligible_step_indices(read_output_snapshot(repository, episode_id, output_id)):
                eligible.append(output_id)
        except ValueError:
            continue
    return eligible


def list_bbox_generation_episode_ids(root: Path | None = None) -> list[str]:
    """List episodes containing a bbox-generation-eligible reference action."""

    repository = root or repository_root()
    sample_root = _sample_root(repository)
    if not sample_root.is_dir():
        return []
    episodes = []
    for directory in sorted(sample_root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or not (directory / "data.json").is_file():
            continue
        try:
            _read_episode(directory.name, repository)
        except ValueError:
            continue
        if _eligible_output_ids(repository, directory.name):
            episodes.append(directory.name)
    return episodes


def bbox_generation_view(
    episode_id: str,
    output_id: str | None = None,
    step_index: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one complete Bounding Box Generation Test browser payload."""

    repository = root or repository_root()
    episode_name = _simple_name(episode_id, field_name="episode_id")
    _read_episode(episode_name, repository)
    output_ids = _eligible_output_ids(repository, episode_name)
    if not output_ids:
        raise ValueError(f"No bbox-generation-eligible outputs exist for {episode_name}")
    selected_output_id = output_id if output_id in output_ids else output_ids[0]
    output = read_output_snapshot(repository, episode_name, selected_output_id)
    eligible_steps = _eligible_step_indices(output)
    if not eligible_steps:
        raise ValueError("The selected output has no bbox-generation-eligible steps")
    selected_step_index = step_index if step_index in eligible_steps else eligible_steps[0]
    steps = output["steps"]
    step = steps[selected_step_index]
    assert isinstance(step, Mapping)
    reference = _reference_action(step)
    assert reference is not None
    step_id = _simple_name(str(step.get("step_id") or ""), field_name="step_id")
    current = _load_current_screenshot(episode_name, step_id, repository)
    input_image = overlay_action_parameter(current, {"type": reference["type"], "parameter": reference["parameter"]})
    generation = step.get("reference_bbox_generation")
    generation = generation if isinstance(generation, Mapping) else {}
    bbox = _bbox(reference.get("bbox"))
    return {
        "episode_id": episode_name,
        "episodes": list_bbox_generation_episode_ids(repository),
        "output_id": selected_output_id,
        "output_ids": output_ids,
        "step_index": selected_step_index,
        "steps": [
            {
                "index": index,
                "step_id": steps[index]["step_id"],
                "label": f"Step {index + 1}: {steps[index]['step_id']}",
            }
            for index in eligible_steps
        ],
        "reference_action": {
            "Action Type": reference["type"],
            "Action Parameter": reference["parameter"],
            "Operation": reference["operation"],
            **({"Target Bounding Box": bbox} if bbox is not None else {}),
        },
        "current_screenshot": pillow_image_to_data_url(input_image),
        "generation": {name: generation.get(name) for name in ("Target", "Top", "Bottom", "Left", "Right")},
        "generation_source": generation.get("source"),
        "bbox": bbox,
    }
