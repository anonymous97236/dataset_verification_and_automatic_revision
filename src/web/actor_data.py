"""Sample-data access and Actor-input serialization for the web interface."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from actor import format_ocr_results, prepare_actor_screenshots, render_actor_prompt
from utils.output_store import (
    ensure_output_snapshot,
    list_output_ids,
    read_output_snapshot,
)
from utils.action_evaluate import EvaluationResult, evaluate_action
from utils.visualization import actor_test_output_visualization
from utils.reference_action import resolve_reference_action

from utils.configuration import provider_for_role, repository_root


_SCREENSHOT_LABELS = (
    "Previous screenshot",
    "Current screenshot",
    "Current screenshot with coordinate grid",
)


def _simple_name(value: str, *, field_name: str) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name:
        raise ValueError(f"{field_name} must be a simple name")
    return name


def _sample_root(root: Path | None = None) -> Path:
    return (root or repository_root()) / "sample_data"


def _read_episode(episode_id: str, root: Path | None = None) -> dict[str, Any]:
    episode_name = _simple_name(episode_id, field_name="episode_id")
    path = _sample_root(root) / episode_name / "data.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Unknown sample episode: {episode_name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid sample data for {episode_name}: {exc.msg}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        raise ValueError(f"Invalid sample data for {episode_name}")
    return value


def _read_ocr_results(episode_id: str, step_id: str, root: Path) -> str:
    """Read and format the OCR data used in the Actor's request."""

    path = _sample_root(root) / episode_id / "ocr_results" / f"{step_id}.json"
    try:
        return format_ocr_results(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ValueError(f"OCR results are missing for {step_id}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid OCR results for {step_id}: {exc.msg}") from exc


def list_episode_ids(root: Path | None = None) -> list[str]:
    """List usable sample-data episodes in a deterministic order."""

    sample_root = _sample_root(root)
    if not sample_root.is_dir():
        return []
    episode_ids: list[str] = []
    for directory in sorted(sample_root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or not (directory / "data.json").is_file():
            continue
        try:
            _read_episode(directory.name, root)
        except ValueError:
            continue
        episode_ids.append(directory.name)
    return episode_ids


def _image_data_url(image: Image.Image) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _load_current_screenshot(episode_id: str, step_id: str, root: Path) -> Image.Image:
    """Load the unmodified screenshot used before output visualization."""

    path = _sample_root(root) / episode_id / "screenshots" / f"{step_id}.png"
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except FileNotFoundError as exc:
        raise ValueError(f"Screenshot is missing for {step_id}") from exc
    except OSError as exc:
        raise ValueError(f"Invalid screenshot for {step_id}: {exc}") from exc


def _actor_output_view(value: Any) -> dict[str, Any]:
    """Split an Actor JSON response into its thought and action fields."""

    if not isinstance(value, dict):
        return {"thought": "", "fields": []}
    thought = ""
    fields: list[dict[str, Any]] = []
    for key, field_value in value.items():
        if str(key).strip().casefold() == "thought":
            thought = str(field_value or "")
        else:
            fields.append({"name": str(key), "value": field_value})
    return {"thought": thought, "fields": fields}


def _actor_output_action(value: Any) -> dict[str, Any]:
    """Extract the action fields from an Actor response for screenshot overlay."""

    if not isinstance(value, dict):
        return {}
    normalized = {
        "".join(character for character in str(key).casefold() if character.isalnum()): field_value
        for key, field_value in value.items()
    }
    return {
        "type": normalized.get("actiontype", normalized.get("type")),
        "parameter": normalized.get("actionparameter", normalized.get("parameter")),
    }


def _reproduction_view(reference_action: dict[str, Any], actor_output: Any) -> dict[str, str] | None:
    """Return the paper-defined reproduction status for one normal Actor output.

    The paper defines ``Reproduces(a1, a2)`` as whether ``a1`` is correct when
    evaluated with ``a2`` as the reference action.  API errors, malformed
    responses, and not-yet-run Actors have no action to evaluate, so they do
    not receive a status badge.
    """

    if not isinstance(actor_output, dict):
        return None
    action = _actor_output_action(actor_output)
    if not str(action.get("type") or "").strip():
        return None
    result = evaluate_action(reference_action, action)
    return {
        "result": "correct" if result == EvaluationResult.correct else "incorrect",
        "label": "Reproduces Reference" if result == EvaluationResult.correct else "Does Not Reproduce Reference",
    }


def _reference_action_view(reference: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a resolved reference action, omitting an unavailable bbox."""

    fields = [
        {"name": "Action Type", "value": reference.get("type")},
        {"name": "Action Parameter", "value": reference.get("parameter")},
    ]
    if "bbox" in reference:
        fields.append({"name": "Target Bounding Box", "value": reference["bbox"]})
    fields.append({"name": "Operation", "value": reference.get("operation")})
    return fields


def actor_view(
    episode_id: str,
    step_index: int,
    output_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build the complete browser payload for one Actor sample-data step."""

    repository = root or repository_root()
    sample = _read_episode(episode_id, repository)
    if output_id is None:
        output_id, _output_path = ensure_output_snapshot(repository, episode_id, sample)
    output = read_output_snapshot(repository, episode_id, output_id)
    steps = output["steps"]
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
        raise ValueError(f"Step {step_index} is outside the selected episode")
    step = steps[step_index]
    if not isinstance(step, dict):
        raise ValueError(f"Step {step_index} is invalid")
    step_id = _simple_name(str(step.get("step_id") or ""), field_name="step_id")
    task_goal = str(output.get("task_goal") or "")
    expected_result = str(output.get("expected_result") or "")
    previous_action_history = str(step.get("previous_action_history") or "")
    ocr_results = _read_ocr_results(episode_id, step_id, repository)
    images = prepare_actor_screenshots(
        episode_id,
        steps,
        step_index,
        sample_data_root=_sample_root(repository),
    )
    reference_action = resolve_reference_action(step)
    actor1_output = step.get("actor1_output")
    actor2_output = step.get("actor2_output")
    output_screenshot = actor_test_output_visualization(
        _load_current_screenshot(episode_id, step_id, repository),
        actor1_action=_actor_output_action(actor1_output),
        actor2_action=_actor_output_action(actor2_output),
        reference_action=reference_action,
    )
    return {
        "episode_id": episode_id,
        "episodes": list_episode_ids(repository),
        "output_ids": list_output_ids(repository, episode_id),
        "output_id": output_id,
        "steps": [
            {
                "index": index,
                "step_id": str(item.get("step_id") or f"step_{index}"),
                "label": f"Step {index + 1}: {item.get('step_id') or f'step_{index}'}",
            }
            for index, item in enumerate(steps)
            if isinstance(item, dict)
        ],
        "step_index": step_index,
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": previous_action_history,
        "prompt": render_actor_prompt(
            task_goal=task_goal,
            expected_result=expected_result,
            previous_action_history=previous_action_history,
            ocr_results=ocr_results,
        ),
        "screenshots": [
            {"label": label, "data_url": _image_data_url(image)}
            for label, image in zip(_SCREENSHOT_LABELS, images)
        ],
        "current_screenshot": _image_data_url(output_screenshot),
        "actor_outputs": {
            "actor1": _actor_output_view(actor1_output),
            "actor2": _actor_output_view(actor2_output),
        },
        "actor_reproduction": {
            "actor1": _reproduction_view(reference_action, actor1_output),
            "actor2": _reproduction_view(reference_action, actor2_output),
        },
        "actor_providers": {
            "actor1": provider_for_role("actor1", repository),
            "actor2": provider_for_role("actor2", repository),
        },
        "reference_action": _reference_action_view(reference_action),
    }
