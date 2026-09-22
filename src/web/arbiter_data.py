"""Filtered output-snapshot access and Arbiter-input serialization for the web UI."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Mapping

from arbiter import (
    arbiter_prompt_data,
    build_arbiter_model_decisions,
    prepare_arbiter_screenshots,
    render_arbiter_prompt,
)
from llm import pillow_image_to_data_url
from utils.action_evaluate import EvaluationResult, evaluate_action
from utils.reference_action import resolve_reference_action
from utils.output_store import list_output_ids, read_output_snapshot
from utils.visualization import arbiter_test_output_visualization

from .actor_data import (
    _image_data_url,
    _load_current_screenshot,
    _read_episode,
    _read_ocr_results,
    _sample_root,
    _simple_name,
)
from utils.configuration import provider_for_role, repository_root


def _decision_seed(episode_id: str, output_id: str, step_index: int) -> str:
    return f"arbiter:{episode_id}:{output_id}:{step_index}"


def _action(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    fields = {
        "".join(character for character in str(key).casefold() if character.isalnum()): field_value
        for key, field_value in value.items()
    }
    return {
        "type": fields.get("actiontype", fields.get("type")),
        "parameter": fields.get("actionparameter", fields.get("parameter")),
    }


def _complete_actor_output(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    fields = {
        "".join(character for character in str(key).casefold() if character.isalnum()): field_value
        for key, field_value in value.items()
    }
    action_type = str(fields.get("actiontype", fields.get("type", "")) or "").strip().casefold()
    parameter = fields.get("actionparameter", fields.get("parameter"))
    if not action_type:
        return False
    return action_type in {"back", "home"} or bool(str(parameter or "").strip())


def _eligible_step_indices(output: Mapping[str, Any]) -> list[int]:
    """Return steps with two Actor outputs and at least one failed reproduction."""

    steps = output.get("steps")
    if not isinstance(steps, list):
        return []
    eligible: list[int] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        actor_outputs = (step.get("actor1_output"), step.get("actor2_output"))
        if not all(_complete_actor_output(value) for value in actor_outputs):
            continue
        reference = resolve_reference_action(step)
        if not isinstance(reference, Mapping):
            continue
        if any(evaluate_action(reference, _action(value)) == EvaluationResult.incorrect for value in actor_outputs):
            eligible.append(index)
    return eligible


def _eligible_output_ids(repository: Path, episode_id: str) -> list[str]:
    eligible: list[str] = []
    for output_id in list_output_ids(repository, episode_id):
        try:
            output = read_output_snapshot(repository, episode_id, output_id)
        except ValueError:
            continue
        if _eligible_step_indices(output):
            eligible.append(output_id)
    return eligible


def list_arbiter_episode_ids(root: Path | None = None) -> list[str]:
    """List only episodes with a fully run Actor step needing arbitration."""

    repository = root or repository_root()
    sample_root = _sample_root(repository)
    if not sample_root.is_dir():
        return []
    episodes: list[str] = []
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


def _output_view(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"thought": "", "fields": []}
    thought = ""
    fields: list[dict[str, Any]] = []
    for key, item in value.items():
        if str(key).strip().casefold() == "thought":
            thought = str(item or "")
        else:
            fields.append({"name": str(key), "value": item})
    return {"thought": thought, "fields": fields}


def _reference_fields(reference: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = [
        {"name": "Action Type", "value": reference.get("type")},
        {"name": "Action Parameter", "value": reference.get("parameter")},
    ]
    if "bbox" in reference:
        fields.append({"name": "Target Bounding Box", "value": reference["bbox"]})
    fields.append({"name": "Operation", "value": reference.get("operation")})
    return fields


def _reproduction(reference: Mapping[str, Any], output: Any) -> dict[str, str] | None:
    action = _action(output)
    if not str(action.get("type") or "").strip():
        return None
    result = evaluate_action(reference, action)
    return {
        "result": "correct" if result == EvaluationResult.correct else "incorrect",
        "label": "Reproduces Reference" if result == EvaluationResult.correct else "Does Not Reproduce Reference",
    }


def arbiter_view(
    episode_id: str,
    output_id: str | None = None,
    step_index: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one complete, eligible Arbiter Test browser payload."""

    repository = root or repository_root()
    episode_name = _simple_name(episode_id, field_name="episode_id")
    _read_episode(episode_name, repository)
    output_ids = _eligible_output_ids(repository, episode_name)
    if not output_ids:
        raise ValueError(f"No Arbiter-eligible outputs exist for {episode_name}")
    selected_output_id = output_id if output_id in output_ids else output_ids[0]
    output = read_output_snapshot(repository, episode_name, selected_output_id)
    eligible_steps = _eligible_step_indices(output)
    if not eligible_steps:
        raise ValueError(f"No Arbiter-eligible steps exist for output {selected_output_id}")
    selected_step_index = step_index if step_index in eligible_steps else eligible_steps[0]
    steps = output["steps"]
    step = steps[selected_step_index]
    if not isinstance(step, Mapping):
        raise ValueError(f"Step {selected_step_index} is invalid")
    step_id = _simple_name(str(step.get("step_id") or ""), field_name="step_id")
    task_goal = str(output.get("task_goal") or "")
    expected_result = str(output.get("expected_result") or "")
    previous_history = str(step.get("previous_action_history") or "")
    ocr_results = _read_ocr_results(episode_name, step_id, repository)
    seed = _decision_seed(episode_name, selected_output_id, selected_step_index)
    model_decisions, candidates = build_arbiter_model_decisions(step, rng=random.Random(seed))
    screenshots = prepare_arbiter_screenshots(
        episode_name,
        steps,
        selected_step_index,
        candidates,
        sample_data_root=_sample_root(repository),
    )
    reference = resolve_reference_action(step)
    arbiter1_output, arbiter2_output = step.get("arbiter1_output"), step.get("arbiter2_output")
    output_screenshot = arbiter_test_output_visualization(
        _load_current_screenshot(episode_name, step_id, repository),
        arbiter1_action=_action(arbiter1_output),
        arbiter2_action=_action(arbiter2_output),
        reference_action=reference,
    )
    return {
        "episode_id": episode_name,
        "episodes": list_arbiter_episode_ids(repository),
        "output_ids": output_ids,
        "output_id": selected_output_id,
        "steps": [
            {
                "index": index,
                "step_id": str(steps[index].get("step_id") or f"step_{index}"),
                "label": f"Step {index + 1}: {steps[index].get('step_id') or f'step_{index}'}",
            }
            for index in eligible_steps
        ],
        "step_index": selected_step_index,
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": previous_history,
        "model_decisions": model_decisions,
        "prompt": render_arbiter_prompt(
            task_goal=task_goal,
            expected_result=expected_result,
            previous_action_history=previous_history,
            ocr_results=ocr_results,
            model_decisions=model_decisions,
        ),
        "screenshots": [
            {"label": label, "data_url": pillow_image_to_data_url(image)}
            for label, image in zip(("Previous screenshot", "Current screenshot with model decisions"), screenshots)
        ],
        "current_screenshot": _image_data_url(output_screenshot),
        "arbiter_outputs": {"arbiter1": _output_view(arbiter1_output), "arbiter2": _output_view(arbiter2_output)},
        "arbiter_reproduction": {
            "arbiter1": _reproduction(reference, arbiter1_output),
            "arbiter2": _reproduction(reference, arbiter2_output),
        },
        "arbiter_providers": {
            "arbiter1": provider_for_role("arbiter1", repository),
            "arbiter2": provider_for_role("arbiter2", repository),
        },
        "reference_action": _reference_fields(reference),
        "decision_seed": seed,
    }
