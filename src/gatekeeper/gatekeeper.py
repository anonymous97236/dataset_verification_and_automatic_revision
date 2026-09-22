"""Classify one output step and apply its Gatekeeper decision locally."""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from PIL import Image

from actor import format_ocr_results
from llm import (
    LLMConfigurationError, LLMRequest, ProviderConfig, call_llm,
    load_gemini_config, load_qwen_config, pillow_image_to_data_url, render_prompt,
)
from llm.json_utils import recover_json_object
from prompts.action_space import ACTION_SPACE
from prompts.criteria import ACTION_SELECTION_CRITERIA
from prompts.gatekeeper_prompt import PROMPT_GATEKEEPER
from utils.action_evaluate import EvaluationResult, _normalize_action_type, evaluate_action
from utils.output_store import output_data_path, read_output_snapshot, store_step_output
from utils.reference_action import resolve_reference_action
from utils.visualization import build_missing_screenshot, gatekeeper_screenshots


@dataclass(frozen=True)
class GatekeeperRunResult:
    """Complete response, anonymous-label provenance, and applied decision."""

    output: dict[str, Any]
    output_path: Path
    provider: str
    model: str
    usage: dict[str, int | None]
    source_map: dict[str, str]
    output_field: str = "gatekeeper_output"
    case: str | None = None
    selected_arbiter: str | None = None
    error: str | None = None


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(f"Expected a simple file name, got {value!r}")
    return name


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "".join(char for char in str(key).casefold() if char.isalnum()): item
        for key, item in value.items()
    }


def _action(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = _fields(value)
    return {
        "type": _normalize_action_type(fields.get("actiontype", fields.get("type"))),
        "parameter": fields.get("actionparameter", fields.get("parameter")),
    }


def _original_reference(step: Mapping[str, Any]) -> dict[str, Any]:
    original = step.get("original_action_annotation")
    if not isinstance(original, Mapping) or not original.get("type"):
        raise ValueError("The step must contain original_action_annotation")
    return {
        "Action Type": original["type"],
        "Action Parameter": copy.deepcopy(original.get("parameter")),
        "Operation": step.get("operation"),
    }


def build_gatekeeper_reference_action(step: Mapping[str, Any]) -> dict[str, Any]:
    """Use the current reference's three prompt fields, or the original annotation."""

    reference = step.get("reference_action")
    if reference is None:
        return _original_reference(step)
    if not isinstance(reference, Mapping):
        raise ValueError("reference_action must be a JSON object")
    fields = _fields(reference)
    action = _action(reference)
    if not action["type"]:
        raise ValueError("reference_action must contain an action type")
    return {
        "Action Type": action["type"],
        "Action Parameter": copy.deepcopy(action["parameter"]),
        "Operation": fields.get("operation"),
    }


def build_gatekeeper_arbiter_actions(
    step: Mapping[str, Any],
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Shuffle non-reproducing Arbiter outputs and retain each output field's identity.

    Eligibility is evaluated against the current reference (including its bbox),
    so another pipeline round uses the action revised by the preceding round.
    """

    reference = resolve_reference_action(step)
    if not reference or not reference.get("type"):
        raise ValueError("The step must contain a valid reference action")
    blocks: list[tuple[str, dict[str, str]]] = []
    for field in ("arbiter1_output", "arbiter2_output"):
        value = step.get(field)
        if not isinstance(value, Mapping) or "api_error" in value or "raw_response" in value:
            continue
        action = _action(value)
        if not action["type"]:
            continue
        if action["type"] not in {"back", "home"} and not _text(action["parameter"]).strip():
            continue
        if evaluate_action(reference, action) != EvaluationResult.incorrect:
            continue
        fields = _fields(value)
        block = {
            "Action Type": action["type"],
            "Action Parameter": _text(action["parameter"]),
            "Operation": _text(fields.get("operation")),
        }
        for name, key in (("Thought", "thought"), ("Core Criteria", "corecriteria")):
            text = _text(fields.get(key)).strip()
            if text and text.casefold() != "none":
                block[name] = text
        blocks.append((field, block))
    (rng if rng is not None else random.SystemRandom()).shuffle(blocks)
    actions, sources = {}, {}
    for label, (field, block) in zip(("Arbiter X", "Arbiter Y"), blocks):
        actions[label], sources[label] = block, field
    return actions, sources


def prepare_gatekeeper_screenshots(
    episode_id: str,
    steps: list[Any],
    step_index: int,
    reference_action: Mapping[str, Any],
    arbiter_actions: Mapping[str, Mapping[str, Any]],
    *,
    sample_data_root: Path | None = None,
) -> list[Image.Image]:
    """Compose previous/current at native resolution, then cap the long side at 1024."""

    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
        raise IndexError("step_index is outside the episode's steps")
    root = sample_data_root or (_repository_root() / "sample_data")
    directory = root / _safe_name(episode_id) / "screenshots"

    def load(step: Mapping[str, Any]) -> Image.Image:
        with Image.open(directory / f"{_safe_name(step.get('step_id'))}.png") as image:
            return image.convert("RGB")

    current = load(steps[step_index])
    previous_action, previous_operation = None, ""
    if step_index:
        previous_step = steps[step_index - 1]
        previous = load(previous_step)
        # Previous screenshots always depict the original recorded action.
        previous_action = previous_step.get("original_action_annotation")
        previous_operation = _text(previous_step.get("operation"))
    else:
        previous = build_missing_screenshot(*current.size, "Previous screenshot is not available.")
    composed = gatekeeper_screenshots(
        previous, current, _action(reference_action),
        {label: _action(action) for label, action in arbiter_actions.items()},
        previous_action=previous_action, previous_operation=previous_operation,
    )
    images = []
    for image in composed:
        ratio = min(1.0, 1024 / max(image.size))
        images.append(image if ratio == 1 else image.resize(tuple(max(1, round(size * ratio)) for size in image.size)))
    return images


def gatekeeper_prompt_data(
    *,
    task_goal: str,
    expected_result: str,
    previous_action_history: str,
    ocr_results: str,
    reference_action: Mapping[str, Any],
    arbiter_actions: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    return {
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": previous_action_history,
        "ocr_results": ocr_results,
        "reference_action": json.dumps(reference_action, ensure_ascii=False, indent=2),
        "arbiter_actions": json.dumps(arbiter_actions, ensure_ascii=False, indent=2),
        "action_space": ACTION_SPACE,
        "criteria": ACTION_SELECTION_CRITERIA,
    }


def render_gatekeeper_prompt(**kwargs: Any) -> str:
    """Render the same complete text supplied to the provider."""

    return render_prompt(PROMPT_GATEKEEPER, gatekeeper_prompt_data(**kwargs))


def _decision_updates(
    output: Mapping[str, Any], step: Mapping[str, Any], source_map: Mapping[str, str],
) -> tuple[str, str | None, dict[str, Any]]:
    fields = _fields(output)
    case = _text(fields.get("case")).strip().upper()
    if case not in {"A", "B", "C"} or any(key in fields for key in ("error", "apierror", "rawresponse")):
        raise ValueError("Gatekeeper response must contain Case A, B, or C without an error")
    if case == "A":
        return case, None, {"exclusion": True}
    if case == "B":
        reference = step.get("reference_action")
        if reference is None:
            reference = _original_reference(step)
            if "bbox" in step["original_action_annotation"]:
                reference["bbox"] = copy.deepcopy(step["original_action_annotation"]["bbox"])
        return case, None, {"exclusion": False, "reference_action": copy.deepcopy(reference)}
    label = _text(fields.get("remarks")).strip()
    source = next((value for key, value in source_map.items() if key.casefold() == label.casefold()), None)
    if source is None:
        raise ValueError("Case C Remarks must identify an Arbiter label included in this request")
    return case, source.removesuffix("_output"), {
        "exclusion": False,
        "reference_action": copy.deepcopy(step[source]),
        "reference_bbox_generation": None,
    }


class Gatekeeper:
    """Call Gemini or Qwen once and apply Case A/B/C to an existing output step."""

    def __init__(
        self, provider: Literal["gemini", "qwen"], *, provider_config: ProviderConfig | None = None,
    ) -> None:
        if provider not in {"gemini", "qwen"}:
            raise ValueError("provider must be gemini or qwen")
        if provider_config is not None and provider_config.provider != provider:
            raise LLMConfigurationError("provider_config does not match the selected provider")
        self.provider = provider
        self._provider_config = provider_config

    def run(
        self, episode_id: str, step_index: int, *, output_path: str | Path,
        decision_seed: str | None = None,
    ) -> GatekeeperRunResult:
        """Overwrite gatekeeper_output and apply a valid decision in the same file update.

        Invalid responses and API failures are retained without changing the
        reference/exclusion. Valid B/C decisions clear an earlier A exclusion.
        """

        root = _repository_root()
        episode_name = _safe_name(episode_id)
        path = Path(output_path).resolve()
        expected = output_data_path(root, episode_name, path.parent.name)
        if path != expected or path.name != "data.json":
            raise ValueError("output_path must be this episode's pipeline_outputs data.json")
        data = read_output_snapshot(root, episode_name, path.parent.name)
        steps = data["steps"]
        if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
            raise IndexError("step_index is outside the output's steps")
        step = copy.deepcopy(steps[step_index])
        if not isinstance(step, dict) or step.get("index") != step_index:
            raise ValueError("The requested step has an inconsistent stored index")
        reference = build_gatekeeper_reference_action(step)
        actions, source_map = build_gatekeeper_arbiter_actions(
            step, rng=random.Random(decision_seed) if decision_seed is not None else None,
        )
        if not actions:
            raise ValueError("No complete Arbiter output fails to reproduce the current reference action")
        ocr_path = root / "sample_data" / episode_name / "ocr_results" / f"{_safe_name(step.get('step_id'))}.json"
        prompt_data = gatekeeper_prompt_data(
            task_goal=_text(data.get("task_goal")), expected_result=_text(data.get("expected_result")),
            previous_action_history=_text(step.get("previous_action_history")),
            ocr_results=format_ocr_results(json.loads(ocr_path.read_text(encoding="utf-8"))),
            reference_action=reference, arbiter_actions=actions,
        )
        request = LLMRequest(
            prompt=PROMPT_GATEKEEPER, prompt_data=prompt_data,
            images=tuple(pillow_image_to_data_url(image) for image in prepare_gatekeeper_screenshots(
                episode_name, steps, step_index, reference, actions,
            )),
        )
        config = None
        case = selected_arbiter = error = None
        usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        updates: dict[str, Any] = {"gatekeeper_source_map": source_map}
        model = ""
        try:
            config = self._provider_config or (load_gemini_config() if self.provider == "gemini" else load_qwen_config())
            model = config.model
            result = call_llm(config, request)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            output = {"api_error": error}
        else:
            output = recover_json_object(result.raw_text)
            usage, model = result.usage, result.model
            try:
                case, selected_arbiter, decision = _decision_updates(output, step, source_map)
            except ValueError as exc:
                error = str(exc)
            else:
                updates.update(decision)
        destination = store_step_output(
            root, episode_id=episode_name, sample=data, step_index=step_index,
            output_field="gatekeeper_output", output=output, output_path=path, step_updates=updates,
        )
        return GatekeeperRunResult(
            output=output, output_path=destination, provider=self.provider, model=model,
            usage=usage, source_map=source_map, case=case, selected_arbiter=selected_arbiter, error=error,
        )
