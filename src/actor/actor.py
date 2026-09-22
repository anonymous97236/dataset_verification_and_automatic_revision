"""Standalone Actor implementation for one sample-data step."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from PIL import Image

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
from prompts.actor_prompt import ACTOR_PROMPT
from prompts.criteria import ACTION_SELECTION_CRITERIA
from utils.visualization import actor_screenshots, build_missing_screenshot
from utils.output_store import output_data_path, output_root, store_step_output


Provider = Literal["gemini", "qwen"]
_PROVIDERS = frozenset({"gemini", "qwen"})
_ACTOR_SCREENSHOT_MAX_SIDE = 1024


@dataclass(frozen=True)
class ActorRunResult:
    """The parsed Actor response and the pipeline-output file that contains it."""

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


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def format_ocr_results(items: Any) -> str:
    """Format OCR items in 20 top-to-bottom bins, then left-to-right.

    OCR uses the reproducibility material's 0--1000 integer coordinates and
    emits integer bbox centers.
    """

    if not isinstance(items, list):
        return ""

    ordered: list[tuple[int, int, int, int, int, int, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        bbox = item.get("bbox")
        if not text or not isinstance(bbox, list) or len(bbox) < 4:
            continue
        coordinates = [_integer(part) for part in bbox[:4]]
        if any(part is None for part in coordinates):
            continue
        x1, y1, x2, y2 = (int(part) for part in coordinates)
        center_x_sum, center_y_sum = x1 + x2, y1 + y2
        if center_y_sum <= 0:
            y_bin = 0
        elif center_y_sum >= 2000:
            y_bin = 19
        else:
            y_bin = (center_y_sum * 20) // 2000
        ordered.append((y_bin, center_x_sum, center_y_sum, index, (center_x_sum + 1) // 2, (center_y_sum + 1) // 2, text))

    ordered.sort(key=lambda item: item[:4])
    return "\n".join(f"[{center_x}, {center_y}]: {text}" for _, _, _, _, center_x, center_y, text in ordered)


def actor_prompt_data(
    *,
    task_goal: str,
    expected_result: str,
    previous_action_history: str,
    ocr_results: str,
) -> dict[str, str]:
    """Return the complete data set injected into the Actor text prompt."""

    return {
        "task_goal": task_goal,
        "expected_result": expected_result,
        "previous_action_history": previous_action_history,
        "ocr_results": ocr_results,
        "action_space": ACTION_SPACE,
        "criteria": ACTION_SELECTION_CRITERIA,
        "output_format": OUTPUT_FORMAT,
    }


def render_actor_prompt(
    *,
    task_goal: str,
    expected_result: str,
    previous_action_history: str,
    ocr_results: str,
) -> str:
    """Render the exact text prompt supplied to an Actor provider request."""

    return render_prompt(
        ACTOR_PROMPT,
        actor_prompt_data(
            task_goal=task_goal,
            expected_result=expected_result,
            previous_action_history=previous_action_history,
            ocr_results=ocr_results,
        ),
    )


def _api_error_output(error: Exception) -> dict[str, str]:
    """Preserve a one-shot API failure as Actor output without retrying it."""

    return {"api_error": f"{type(error).__name__}: {error}"}


def _load_screenshot(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Required screenshot does not exist: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Could not open screenshot {path}: {exc}") from exc


def _resize_actor_screenshot(image: Image.Image) -> Image.Image:
    """Resize an Actor input so its long side is always exactly 1024px."""

    prepared = image.convert("RGB")
    width, height = prepared.size
    longest_side = max(width, height, 1)
    if longest_side == _ACTOR_SCREENSHOT_MAX_SIDE:
        return prepared
    ratio = _ACTOR_SCREENSHOT_MAX_SIDE / longest_side
    return prepared.resize(
        (
            max(1, round(width * ratio)),
            max(1, round(height * ratio)),
        )
    )


def prepare_actor_screenshots(
    episode_id: str,
    steps: list[Any],
    step_index: int,
    *,
    sample_data_root: Path | None = None,
) -> list[Image.Image]:
    """Return the three fully composed images supplied to an Actor request.

    The web interface and ``Actor.run`` both use this function so their previous
    overlay, current image, coordinate grid, placeholder, and 1024px resize are
    pixel-equivalent.
    """

    episode_name = _safe_name(episode_id, field_name="episode_id")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
        raise IndexError(f"step_index {step_index} is outside the sample's steps")
    current_step = steps[step_index]
    if not isinstance(current_step, dict):
        raise ValueError(f"Step {step_index} must be a JSON object")
    current_id = _safe_name(str(current_step.get("step_id") or ""), field_name="step_id")
    screenshots_dir = (sample_data_root or (_repository_root() / "sample_data")) / episode_name / "screenshots"
    current_image = _load_screenshot(screenshots_dir / f"{current_id}.png")
    if step_index == 0:
        composed = actor_screenshots(
            build_missing_screenshot(
                current_image.width,
                current_image.height,
                "Previous screenshot is not available.",
            ),
            current_image,
        )
    else:
        previous_step = steps[step_index - 1]
        if not isinstance(previous_step, dict):
            raise ValueError(f"Step {step_index - 1} must be a JSON object")
        previous_id = _safe_name(str(previous_step.get("step_id") or ""), field_name="step_id")
        # Previous screenshots always depict the original recorded action.
        previous_action = previous_step.get("original_action_annotation")
        if not isinstance(previous_action, Mapping):
            previous_action = None
        composed = actor_screenshots(
            _load_screenshot(screenshots_dir / f"{previous_id}.png"),
            current_image,
            previous_action=previous_action,
            previous_operation=str(previous_step.get("operation") or ""),
        )
    return [_resize_actor_screenshot(image) for image in composed]


class Actor:
    """Run one configured Actor against a local sample-data step."""

    def __init__(
        self,
        provider: Provider,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> None:
        if provider not in _PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(_PROVIDERS)}, got {provider!r}")
        if provider_config is not None and provider_config.provider != provider:
            raise LLMConfigurationError(
                f"provider_config is for {provider_config.provider!r}, not {provider!r}"
            )
        self.provider = provider
        self._provider_config = provider_config

    def run(
        self,
        episode_id: str,
        step_index: int,
        *,
        output_field: str,
        output_path: str | Path | None = None,
    ) -> ActorRunResult:
        """Call the provider and store this Actor's complete JSON response.

        Supplying a prior ``output_path`` appends to the same pipeline snapshot.
        The pipeline chooses ``output_field`` (for example, ``actor1_output``)
        so this class remains reusable for any number of Actor instances.
        """

        episode_name = _safe_name(episode_id, field_name="episode_id")
        if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
            raise ValueError("step_index must be a non-negative integer")
        field = str(output_field or "").strip()
        if not field:
            raise ValueError("output_field must be a non-empty string")
        sample_path = _repository_root() / "sample_data" / episode_name / "data.json"
        sample = _read_json(sample_path)
        if not isinstance(sample, dict):
            raise ValueError(f"Sample data must be a JSON object: {sample_path}")
        input_data = sample
        if output_path is not None:
            selected_output_path = Path(output_path).resolve()
            outputs_dir = output_root(_repository_root()).resolve()
            try:
                selected_output_path.relative_to(outputs_dir)
            except ValueError as exc:
                raise ValueError("output_path must be inside pipeline_outputs") from exc
            if selected_output_path.name != "data.json" or selected_output_path != output_data_path(
                _repository_root(),
                episode_name,
                selected_output_path.parent.name,
            ):
                raise ValueError("output_path must be an episode output's data.json")
            input_data = _read_json(selected_output_path)
            if not isinstance(input_data, dict):
                raise ValueError(f"Pipeline output must be a JSON object: {selected_output_path}")

        steps = input_data.get("steps")
        if not isinstance(steps, list) or step_index >= len(steps):
            raise IndexError(f"step_index {step_index} is outside the sample's steps")
        step = steps[step_index]
        if not isinstance(step, dict):
            raise ValueError(f"Step {step_index} must be a JSON object")
        if step.get("index") != step_index:
            raise ValueError(f"Step {step_index} has inconsistent stored index {step.get('index')!r}")

        step_id = _safe_name(str(step.get("step_id") or ""), field_name="step_id")
        ocr_path = _repository_root() / "sample_data" / episode_name / "ocr_results" / f"{step_id}.json"
        ocr_results = format_ocr_results(_read_json(ocr_path))
        # The original action-decision pipeline sent its final composed
        # screenshots as JPEG base64 data URLs. Keep the same provider-neutral
        # boundary here for Gemini and OpenAI-compatible Qwen requests.
        images = tuple(
            pillow_image_to_data_url(image)
            for image in self._screenshots(episode_name, steps, step_index)
        )
        prompt_data = actor_prompt_data(
            task_goal=str(input_data.get("task_goal") or ""),
            expected_result=str(input_data.get("expected_result") or ""),
            previous_action_history=str(step.get("previous_action_history") or ""),
            ocr_results=ocr_results,
        )
        request = LLMRequest(
            prompt=ACTOR_PROMPT,
            images=images,
            prompt_data=prompt_data,
        )
        config: ProviderConfig | None = None
        try:
            config = self._config()
            # ``call_llm`` makes precisely one provider request.  A provider
            # exception is retained as this Actor's output; it is never retried.
            result = call_llm(config, request)
        except Exception as exc:
            output = _api_error_output(exc)
            destination = store_step_output(
                _repository_root(),
                sample=sample,
                episode_id=episode_name,
                step_index=step_index,
                output=output,
                output_field=field,
                output_path=output_path,
            )
            return ActorRunResult(
                output=output,
                output_field=field,
                output_path=destination,
                provider=self.provider,
                model=config.model if config is not None else "",
                usage={"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
                error=output["api_error"],
            )

        # A non-empty provider response, including an error message returned as
        # ordinary text or JSON, is stored verbatim/recovered as-is.  It is not
        # treated as a reason to send another request.
        output = recover_json_object(result.raw_text)
        destination = store_step_output(
            _repository_root(),
            sample=sample,
            episode_id=episode_name,
            step_index=step_index,
            output=output,
            output_field=field,
            output_path=output_path,
        )
        return ActorRunResult(
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

    def _screenshots(self, episode_id: str, steps: list[Any], step_index: int):
        return prepare_actor_screenshots(episode_id, steps, step_index)
