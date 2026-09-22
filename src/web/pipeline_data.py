"""Read-only previews and stage snapshots for the end-to-end observer page."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from pipeline.runner import _complete_action
from utils.configuration import provider_for_role, repository_root
from utils.output_store import create_output_snapshot
from utils.reference_action import resolve_reference_action
from utils.visualization import actor_test_output_visualization, arbiter_test_output_visualization, overlay_action_parameter
from .actor_data import (
    _actor_output_action, _actor_output_view, _image_data_url, _load_current_screenshot,
    _read_episode, _reference_action_view, _reproduction_view, _simple_name, list_episode_ids,
)
from .gatekeeper_data import _selected_gatekeeper_case, _gatekeeper_thought


STAGES = ("actor", "arbiter", "gatekeeper", "bbox")
_GENERATED_FIELDS = (
    "reference_action", "reference_bbox_generation", "exclusion", "pipeline_status", "pipeline_history",
    "actor1_output", "actor2_output", "arbiter1_output", "arbiter2_output", "gatekeeper_output",
    "gatekeeper_source_map",
)


def pipeline_sample(episode_id: str, step_index: int, root: Path | None = None) -> dict[str, Any]:
    """Validate selection and start from original sample annotations on every run."""

    sample = copy.deepcopy(_read_episode(episode_id, root))
    steps = sample["steps"]
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
        raise ValueError("step_index must be a valid zero-based step index")
    for step in steps:
        if isinstance(step, dict):
            for field in _GENERATED_FIELDS:
                step.pop(field, None)
    step = steps[step_index]
    if not isinstance(step, dict) or step.get("index") != step_index:
        raise ValueError("The selected step has an inconsistent stored index")
    return sample


def new_pipeline_output(episode_id: str, step_index: int) -> tuple[str, Path]:
    """Create a fresh snapshot only after an explicit Run Pipeline request."""

    root = repository_root()
    sample = pipeline_sample(episode_id, step_index, root)
    return create_output_snapshot(root, episode_id, sample)


def initial_stages(step: dict[str, Any]) -> dict[str, Any]:
    return {name: {"status": "idle", "cycle": None, "step": copy.deepcopy(step)} for name in STAGES}


def pipeline_stage_views(episode_id: str, stages: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Render each stage with the reference used in that stage's own cycle."""

    root = root or repository_root()
    first = stages["actor"]["step"]
    image = _load_current_screenshot(episode_id, _simple_name(first["step_id"], field_name="step_id"), root)
    views = {}
    for name, saved in stages.items():
        step = saved["step"]
        reference = resolve_reference_action(step)
        view = {"status": saved["status"], "cycle": saved["cycle"], "error": saved.get("error"),
                "reference_action": _reference_action_view(reference)}
        if name in {"actor", "arbiter"}:
            outputs = {f"{name}{number}": step.get(f"{name}{number}_output") for number in (1, 2)}
            view["outputs"] = {}
            for role, output in outputs.items():
                item = _actor_output_view(output)
                item["available"] = output is not None
                item["error"] = next((str(output[key]) for key in ("api_error", "error", "raw_response")
                                      if isinstance(output, dict) and output.get(key)), None)
                item["reproduction"] = _reproduction_view(reference, output) if _complete_action(output) else None
                view["outputs"][role] = item
            visualize = actor_test_output_visualization if name == "actor" else arbiter_test_output_visualization
            view["screenshot"] = _image_data_url(visualize(image, reference_action=reference,
                **{f"{role}_action": _actor_output_action(output) for role, output in outputs.items()}))
        elif name == "gatekeeper":
            view["case"] = _selected_gatekeeper_case(step)
            view["thought"] = _gatekeeper_thought(step)
            output = step.get("gatekeeper_output") or {}
            view["error"] = view["error"] or output.get("api_error") or output.get("error") or output.get("raw_response")
        else:
            generation = step.get("reference_bbox_generation") or {}
            view["intents"] = saved.get("intents", {}) if saved["status"] != "completed" else generation
            view["source"] = generation.get("source") if saved["status"] == "completed" else None
            view["bbox"] = reference.get("bbox") if saved["status"] == "completed" else None
            view["edges"] = saved.get("edges", {})
            view["screenshot"] = _image_data_url(overlay_action_parameter(image, reference))
        views[name] = view
    return views


def pipeline_selection(episode_id: str, step_index: int = 0) -> dict[str, Any]:
    root = repository_root()
    sample = pipeline_sample(episode_id, step_index, root)
    return {
        "episode_id": episode_id, "step_index": step_index, "episodes": list_episode_ids(root),
        "steps": [{"index": index, "label": f"Step {index + 1}: {step['step_id']}"}
                  for index, step in enumerate(sample["steps"]) if isinstance(step, dict)],
        "providers": {role: provider_for_role(role, root) for role in ("actor1", "actor2", "arbiter1", "arbiter2", "gatekeeper")},
        "stages": pipeline_stage_views(episode_id, initial_stages(sample["steps"][step_index]), root),
    }
