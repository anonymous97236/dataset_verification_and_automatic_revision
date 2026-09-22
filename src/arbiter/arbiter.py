"""Standalone Arbiter implementation for one pipeline-output step."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from PIL import Image

from actor import format_ocr_results
from llm import (
    LLMConfigurationError,
    LLMRequest,
    ProviderConfig,
    call_llm,
    load_gemini_config,
    load_qwen_config,
    pillow_image_to_data_url,
    render_prompt,
)
from llm.json_utils import recover_json_object
from prompts.action_space import ACTION_SPACE, OUTPUT_FORMAT
from prompts.arbiter_prompt import PROMPT_ARBITER
from prompts.criteria import ACTION_SELECTION_CRITERIA
from utils.output_store import output_data_path, output_root, store_step_output
from utils.reference_action import resolve_reference_action
from utils.visualization import arbiter_screenshots, build_missing_screenshot


Provider = Literal["gemini", "qwen"]
_PROVIDERS = frozenset({"gemini", "qwen"})
_ARBITER_IMAGE_MAX_SIDE = 1024


@dataclass(frozen=True)
class ArbiterRunResult:
    """The parsed Arbiter response and the pipeline-output file that contains it."""

    output: dict[str, Any]
    output_field: str
    output_path: Path
    provider: str
    model: str
    usage: dict[str, int | None]
    error: str | None = None


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_name(value: str, *, field_name: str) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name:
        raise ValueError(f"{field_name} must be a simple file name, got {value!r}")
    return name


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Required sample-data file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc.msg}") from exc


def _load_screenshot(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Required screenshot does not exist: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Could not open screenshot {path}: {exc}") from exc


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _field_map(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "".join(character for character in str(key).casefold() if character.isalnum()): field_value
        for key, field_value in value.items()
    }


def _action_type(value: Any) -> str:
    action = _text(value).strip().casefold().replace("-", "_").replace(" ", "_")
    return {
        "tap": "click",
        "longtouch": "long_press",
        "long_touch": "long_press",
        "longpress": "long_press",
        "input": "type",
        "end": "terminate",
    }.get(action, action)


def _actor_decision_block(output: Any) -> tuple[dict[str, str], dict[str, Any]] | None:
    """Build the source arbitration payload block for one valid Actor result."""

    if not isinstance(output, Mapping):
        return None
    fields = _field_map(output)
    raw_type = fields.get("actiontype", fields.get("type"))
    raw_parameter = fields.get("actionparameter", fields.get("parameter"))
    action_type = _action_type(raw_type)
    if not action_type:
        return None
    if action_type not in {"back", "home"} and not _text(raw_parameter).strip():
        return None

    block = {
        "Action Type": _text(raw_type).strip(),
        "Action Parameter": _text(raw_parameter).strip(),
        "Operation": _text(fields.get("operation")).strip(),
    }
    thought = _text(fields.get("thought")).strip()
    if thought:
        block["Thought"] = thought
    core_criteria = _text(fields.get("corecriteria", fields.get("corepolicy"))).strip()
    if core_criteria and core_criteria.casefold() != "none":
        block["Core Criteria"] = core_criteria
    return block, {"type": action_type, "parameter": raw_parameter}


def build_arbiter_model_decisions(
    step: Mapping[str, Any],
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    """Build the original arbitration-style anonymized candidate payload.

    The reference annotation and every complete Actor output are shuffled, then
    exposed only as ``Model 1``, ``Model 2``, and so on.  The same ordering is
    used for the prompt's JSON data and the current-screenshot visualization.
    """

    source_step = dict(step or {})
    reference = resolve_reference_action(source_step)
    reference_type = _text(reference.get("type")).strip()
    reference_parameter = reference.get("parameter")
    blocks: list[tuple[dict[str, str], dict[str, Any]]] = [
        (
            {
                "Action Type": reference_type,
                "Action Parameter": _text(reference_parameter).strip(),
                "Operation": _text(reference.get("operation")).strip(),
            },
            {"type": _action_type(reference_type), "parameter": reference_parameter},
        )
    ]
    for actor_role in ("actor1", "actor2"):
        block = _actor_decision_block(source_step.get(f"{actor_role}_output"))
        if block is not None:
            blocks.append(block)

    shuffler = rng if rng is not None else random.SystemRandom()
    shuffler.shuffle(blocks)
    decisions: dict[str, dict[str, str]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    for index, (decision, action) in enumerate(blocks, start=1):
        label = f"Model {index}"
        decisions[label] = decision
        candidates[label] = action
    return decisions, candidates


def _resize_final_arbiter_image(image: Image.Image) -> Image.Image:
    """Perform the original final-composition resize: cap, never upscale."""

    base = image.convert("RGB")
    width, height = base.size
    longest_side = max(width, height, 1)
    if longest_side <= _ARBITER_IMAGE_MAX_SIDE:
        return base
    ratio = _ARBITER_IMAGE_MAX_SIDE / longest_side
    return base.resize((max(1, round(width * ratio)), max(1, round(height * ratio))))


def prepare_arbiter_screenshots(
    episode_id: str,
    steps: list[Any],
    step_index: int,
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    sample_data_root: Path | None = None,
) -> list[Image.Image]:
    """Return Arbiter images in source order: previous, current candidates.

    Both overlays are completed at source resolution before the original
    arbitration final-resize operation is applied.
    """

    episode_name = _safe_name(episode_id, field_name="episode_id")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
        raise IndexError(f"step_index {step_index} is outside the sample's steps")
    current_step = steps[step_index]
    if not isinstance(current_step, Mapping):
        raise ValueError(f"Step {step_index} must be a JSON object")
    current_id = _safe_name(str(current_step.get("step_id") or ""), field_name="step_id")
    screenshot_root = sample_data_root or (_repository_root() / "sample_data")
    screenshots_dir = screenshot_root / episode_name / "screenshots"
    current_image = _load_screenshot(screenshots_dir / f"{current_id}.png")
    if step_index == 0:
        previous_image = build_missing_screenshot(
            current_image.width,
            current_image.height,
            "Previous screenshot is not available.",
        )
        previous_action: Mapping[str, Any] | None = None
        previous_operation = ""
    else:
        previous_step = steps[step_index - 1]
        if not isinstance(previous_step, Mapping):
            raise ValueError(f"Step {step_index - 1} must be a JSON object")
        previous_id = _safe_name(str(previous_step.get("step_id") or ""), field_name="step_id")
        previous_image = _load_screenshot(screenshots_dir / f"{previous_id}.png")
        # Previous screenshots always depict the original recorded action.
        action = previous_step.get("original_action_annotation")
        previous_action = action if isinstance(action, Mapping) else None
        previous_operation = _text(previous_step.get("operation")).strip()
    composed = arbiter_screenshots(
        previous_image,
        current_image,
        candidates,
        previous_action=previous_action,
        previous_operation=previous_operation,
    )
    return [_resize_final_arbiter_image(image) for image in composed]


def arbiter_prompt_data(
    *,
    task_goal: str,
    expected_result: str,
    previous_action_history: str,
    ocr_results: str,
    model_decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Return the full Arbiter prompt substitution mapping."""

    return {
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": previous_action_history,
        "ocr_results": ocr_results,
        "model_decisions": json.dumps(model_decisions, ensure_ascii=False, indent=2),
        "action_space": ACTION_SPACE,
        "criteria": ACTION_SELECTION_CRITERIA,
        "output_format": OUTPUT_FORMAT,
    }


def render_arbiter_prompt(
    *,
    task_goal: str,
    expected_result: str,
    previous_action_history: str,
    ocr_results: str,
    model_decisions: Mapping[str, Mapping[str, Any]],
) -> str:
    """Render the exact text prompt supplied to an Arbiter provider request."""

    return render_prompt(
        PROMPT_ARBITER,
        arbiter_prompt_data(
            task_goal=task_goal,
            expected_result=expected_result,
            previous_action_history=previous_action_history,
            ocr_results=ocr_results,
            model_decisions=model_decisions,
        ),
    )


def _api_error_output(error: Exception) -> dict[str, str]:
    return {"api_error": f"{type(error).__name__}: {error}"}


class Arbiter:
    """Run one configured Arbiter against one selected pipeline-output step."""

    def __init__(self, provider: Provider, *, provider_config: ProviderConfig | None = None) -> None:
        if provider not in _PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(_PROVIDERS)}, got {provider!r}")
        if provider_config is not None and provider_config.provider != provider:
            raise LLMConfigurationError(f"provider_config is for {provider_config.provider!r}, not {provider!r}")
        self.provider = provider
        self._provider_config = provider_config

    def run(
        self,
        episode_id: str,
        step_index: int,
        *,
        output_field: str,
        output_path: str | Path | None = None,
        decision_seed: str | None = None,
    ) -> ArbiterRunResult:
        """Call the provider once and replace this Arbiter's stored JSON output."""

        episode_name = _safe_name(episode_id, field_name="episode_id")
        if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
            raise ValueError("step_index must be a non-negative integer")
        field = str(output_field or "").strip()
        if not field:
            raise ValueError("output_field must be a non-empty string")
        root = _repository_root()
        sample = _read_json(root / "sample_data" / episode_name / "data.json")
        if not isinstance(sample, dict):
            raise ValueError("Sample data must be a JSON object")
        input_data = sample
        if output_path is not None:
            selected_path = Path(output_path).resolve()
            try:
                selected_path.relative_to(output_root(root).resolve())
            except ValueError as exc:
                raise ValueError("output_path must be inside pipeline_outputs") from exc
            expected_path = output_data_path(root, episode_name, selected_path.parent.name)
            if selected_path.name != "data.json" or selected_path != expected_path:
                raise ValueError("output_path must be an episode output's data.json")
            input_data = _read_json(selected_path)
            if not isinstance(input_data, dict):
                raise ValueError("Pipeline output must be a JSON object")

        steps = input_data.get("steps")
        if not isinstance(steps, list) or step_index >= len(steps):
            raise IndexError(f"step_index {step_index} is outside the sample's steps")
        step = steps[step_index]
        if not isinstance(step, Mapping):
            raise ValueError(f"Step {step_index} must be a JSON object")
        if step.get("index") != step_index:
            raise ValueError(f"Step {step_index} has inconsistent stored index {step.get('index')!r}")

        step_id = _safe_name(str(step.get("step_id") or ""), field_name="step_id")
        ocr_path = root / "sample_data" / episode_name / "ocr_results" / f"{step_id}.json"
        ocr_results = format_ocr_results(_read_json(ocr_path))
        model_decisions, candidates = build_arbiter_model_decisions(
            step,
            rng=random.Random(decision_seed) if decision_seed is not None else None,
        )
        images = tuple(
            pillow_image_to_data_url(image)
            for image in prepare_arbiter_screenshots(
                episode_name,
                steps,
                step_index,
                candidates,
            )
        )
        prompt_data = arbiter_prompt_data(
            task_goal=_text(input_data.get("task_goal")).strip(),
            expected_result=_text(input_data.get("expected_result")).strip(),
            previous_action_history=_text(step.get("previous_action_history")).strip(),
            ocr_results=ocr_results,
            model_decisions=model_decisions,
        )
        request = LLMRequest(prompt=PROMPT_ARBITER, images=images, prompt_data=prompt_data)
        config: ProviderConfig | None = None
        try:
            config = self._config()
            result = call_llm(config, request)
        except Exception as exc:
            output = _api_error_output(exc)
            destination = store_step_output(
                root,
                episode_id=episode_name,
                sample=sample,
                step_index=step_index,
                output_field=field,
                output=output,
                output_path=output_path,
            )
            return ArbiterRunResult(
                output=output,
                output_field=field,
                output_path=destination,
                provider=self.provider,
                model=config.model if config is not None else "",
                usage={"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
                error=output["api_error"],
            )

        output = recover_json_object(result.raw_text)
        destination = store_step_output(
            root,
            episode_id=episode_name,
            sample=sample,
            step_index=step_index,
            output_field=field,
            output=output,
            output_path=output_path,
        )
        return ArbiterRunResult(
            output=output,
            output_field=field,
            output_path=destination,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
        )

    def _config(self) -> ProviderConfig:
        if self._provider_config is not None:
            return self._provider_config
        return load_gemini_config() if self.provider == "gemini" else load_qwen_config()
