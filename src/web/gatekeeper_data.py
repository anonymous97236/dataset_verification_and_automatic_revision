"""Read-only Gatekeeper input previews from existing output snapshots."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Mapping

from gatekeeper import (
    build_gatekeeper_arbiter_actions,
    build_gatekeeper_reference_action,
    prepare_gatekeeper_screenshots,
    render_gatekeeper_prompt,
)
from llm import pillow_image_to_data_url
from utils.output_store import list_output_ids, read_output_snapshot

from .actor_data import _read_episode, _read_ocr_results, _sample_root, _simple_name
from utils.configuration import provider_for_role, repository_root


def _selected_gatekeeper_case(step: Mapping[str, Any]) -> str | None:
    """Return a valid stored Gatekeeper case, excluding error responses."""

    output = step.get("gatekeeper_output")
    if not isinstance(output, Mapping):
        return None
    fields = {
        "".join(character for character in str(name).casefold() if character.isalnum()): value
        for name, value in output.items()
    }
    if any(name in fields for name in ("error", "apierror", "rawresponse")):
        return None
    case = str(fields.get("case") or "").strip().upper()
    return case if case in {"A", "B", "C"} else None


def _gatekeeper_thought(step: Mapping[str, Any]) -> str:
    output = step.get("gatekeeper_output")
    if not isinstance(output, Mapping):
        return ""
    for name, value in output.items():
        normalized = "".join(character for character in str(name).casefold() if character.isalnum())
        if normalized == "thought" and value is not None:
            return str(value).strip()
    return ""


def _eligible_step_indices(output: Mapping[str, Any]) -> list[int]:
    steps = output.get("steps")
    if not isinstance(steps, list):
        return []
    eligible = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or step.get("index") != index:
            continue
        try:
            build_gatekeeper_reference_action(step)
            actions, _ = build_gatekeeper_arbiter_actions(step, rng=random.Random(0))
        except ValueError:
            continue
        if actions:
            eligible.append(index)
    return eligible


def _eligible_output_ids(repository: Path, episode_id: str) -> list[str]:
    eligible = []
    for output_id in list_output_ids(repository, episode_id):
        try:
            output = read_output_snapshot(repository, episode_id, output_id)
        except ValueError:
            continue
        if _eligible_step_indices(output):
            eligible.append(output_id)
    return eligible


def list_gatekeeper_episode_ids(root: Path | None = None) -> list[str]:
    """List episodes with at least one step accepted by the Gatekeeper class."""

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


def gatekeeper_view(
    episode_id: str,
    output_id: str | None = None,
    step_index: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Use the class's prompt and image builders without making an API request."""

    repository = root or repository_root()
    episode_name = _simple_name(episode_id, field_name="episode_id")
    _read_episode(episode_name, repository)
    output_ids = _eligible_output_ids(repository, episode_name)
    if not output_ids:
        raise ValueError(f"No Gatekeeper-eligible outputs exist for {episode_name}")
    selected_output_id = output_id if output_id is not None else output_ids[0]
    if selected_output_id not in output_ids:
        raise ValueError("The selected output has no Gatekeeper-eligible steps")
    output = read_output_snapshot(repository, episode_name, selected_output_id)
    eligible_steps = _eligible_step_indices(output)
    if not eligible_steps:
        raise ValueError("The selected output has no Gatekeeper-eligible steps")
    selected_step_index = step_index if step_index is not None else eligible_steps[0]
    if isinstance(selected_step_index, bool) or selected_step_index not in eligible_steps:
        raise ValueError("The selected step is not eligible for Gatekeeper input")
    steps = output["steps"]
    step = steps[selected_step_index]
    step_id = _simple_name(str(step.get("step_id") or ""), field_name="step_id")
    seed = f"gatekeeper:{episode_name}:{selected_output_id}:{selected_step_index}"
    reference = build_gatekeeper_reference_action(step)
    actions, sources = build_gatekeeper_arbiter_actions(step, rng=random.Random(seed))
    images = prepare_gatekeeper_screenshots(
        episode_name, steps, selected_step_index, reference, actions,
        sample_data_root=_sample_root(repository),
    )
    task_goal = str(output.get("task_goal") or "")
    expected_result = str(output.get("expected_result") or "")
    history = str(step.get("previous_action_history") or "")
    return {
        "episode_id": episode_name,
        "episodes": list_gatekeeper_episode_ids(repository),
        "output_id": selected_output_id,
        "output_ids": output_ids,
        "step_index": selected_step_index,
        "steps": [
            {"index": index, "step_id": steps[index]["step_id"],
             "label": f"Step {index + 1}: {steps[index]['step_id']}"}
            for index in eligible_steps
        ],
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": history,
        "reference_action": reference,
        "arbiter_actions": actions,
        "source_map": sources,
        "gatekeeper_case": _selected_gatekeeper_case(step),
        "gatekeeper_thought": _gatekeeper_thought(step),
        "gatekeeper_provider": provider_for_role("gatekeeper", repository),
        "decision_seed": seed,
        "prompt": render_gatekeeper_prompt(
            task_goal=task_goal, expected_result=expected_result,
            previous_action_history=history,
            ocr_results=_read_ocr_results(episode_name, step_id, repository),
            reference_action=reference, arbiter_actions=actions,
        ),
        "screenshots": [
            {"label": label, "data_url": pillow_image_to_data_url(image)}
            for label, image in zip(
                ("Previous screenshot", "Current screenshot with reference and Arbiter-selected actions"), images,
            )
        ],
    }
