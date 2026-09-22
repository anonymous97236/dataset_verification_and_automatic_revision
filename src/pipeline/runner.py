"""File-backed Actor → Arbiter → Gatekeeper revision loop."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from actor import Actor
from arbiter import Arbiter
from bbox_generator import BBoxGenerator
from gatekeeper import Gatekeeper
from utils.action_evaluate import EvaluationResult, _point, _two_points, _wait_seconds, evaluate_action
from utils.configuration import provider_for_role, repository_root
from utils.output_store import (
    create_output_snapshot, output_data_path, read_output_snapshot, update_step_fields,
)
from utils.reference_action import _normalized_action, resolve_reference_action


_BBOX_ACTIONS = frozenset({"click", "long_press", "scroll", "swipe"})
_ROUND_FIELDS = (
    "actor1_output", "actor2_output", "arbiter1_output", "arbiter2_output",
    "gatekeeper_output", "gatekeeper_source_map",
)


@dataclass(frozen=True)
class PipelineStepResult:
    """Terminal state of one step; module responses remain in the output JSON."""

    episode_id: str
    step_index: int
    output_path: Path
    status: str
    reason: str
    iterations: int
    gatekeeper_calls: int
    error: str | None = None


def _complete_action(output: Any) -> bool:
    """Reject API/error responses and actions with unusable parameters."""

    if not isinstance(output, Mapping):
        return False
    fields = {"".join(c for c in str(key).casefold() if c.isalnum()): value
              for key, value in output.items()}
    if any(key in fields for key in ("error", "apierror", "rawresponse")):
        return False
    action = _normalized_action(output, default_operation=None)
    if action is None or not isinstance(action["operation"], str) or not action["operation"].strip():
        return False
    kind, parameter = action["type"], action["parameter"]
    if kind in {"click", "long_press", "scroll", "swipe", "drag"}:
        points = [_point(parameter)] if kind in {"click", "long_press"} else _two_points(parameter)
        return bool(points) and all(point is not None and all(0 <= value <= 1000 for value in point)
                                    for point in points)
    if kind == "type":
        return isinstance(parameter, str) and bool(parameter.strip())
    if kind == "terminate":
        return str(parameter).strip().strip("\"'").lower() in {"success", "failure"}
    if kind == "wait":
        seconds = _wait_seconds(parameter)
        return seconds is not None and seconds.is_finite() and seconds >= 0
    return kind in {"back", "home"}


def _reference(step: Mapping[str, Any]) -> dict[str, Any]:
    """Read only the stored reference inside a pipeline cycle, with no fallback."""

    value = step.get("reference_action")
    action = _normalized_action(value, default_operation=step.get("operation")) if isinstance(value, Mapping) else None
    if action is None:
        raise ValueError("The pipeline step must contain a valid reference_action")
    return action


def _both_reproduce(step: Mapping[str, Any], stage: str) -> bool:
    reference = _reference(step)
    return all(evaluate_action(reference, _normalized_action(step[f"{stage}{index}_output"],
                                                            default_operation=None)) == EvaluationResult.correct
               for index in (1, 2))


class EndToEndPipeline:
    """Run two Actors, two Arbiters, one Gatekeeper, and one bbox generator.

    Provider assignments come from config.yaml. Environment variables must be
    loaded by the caller (the command-line entry point loads .env). A failed
    stage stops the step without retrying any request. Manual reruns start a
    new loop with the currently stored reference and replace the latest outputs.
    """

    MAX_GATEKEEPER_CALLS = 3

    def __init__(self) -> None:
        self.root = repository_root()
        self.actors = tuple(Actor(provider_for_role(role, self.root)) for role in ("actor1", "actor2"))
        self.arbiters = tuple(Arbiter(provider_for_role(role, self.root)) for role in ("arbiter1", "arbiter2"))
        self.gatekeeper = Gatekeeper(provider_for_role("gatekeeper", self.root))
        self.bbox_generator = BBoxGenerator()

    def _output(self, episode_id: str, output_id: str | None) -> Path:
        if output_id is not None:
            read_output_snapshot(self.root, episode_id, output_id)
            return output_data_path(self.root, episode_id, output_id)
        # Validate the identifier before accessing sample_data.
        output_data_path(self.root, episode_id, "validation")
        sample_path = self.root / "sample_data" / episode_id / "data.json"
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(sample, dict) or not isinstance(sample.get("steps"), list):
            raise ValueError("Sample data must contain a steps list")
        return create_output_snapshot(self.root, episode_id, sample)[1]

    def _step(self, episode_id: str, path: Path, step_index: int) -> dict[str, Any]:
        steps = read_output_snapshot(self.root, episode_id, path.parent.name)["steps"]
        if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index < len(steps):
            raise ValueError("step_index must be a valid zero-based step index")
        step = steps[step_index]
        if not isinstance(step, dict) or step.get("index") != step_index:
            raise ValueError("The requested step has an inconsistent stored index")
        return step

    def _update(self, episode_id: str, path: Path, step_index: int, **updates: Any) -> None:
        update_step_fields(self.root, episode_id=episode_id, output_id=path.parent.name,
                           step_index=step_index, updates=updates)

    def _pair(self, modules: tuple, stage: str, episode_id: str, path: Path, step_index: int,
              progress_callback: Callable | None = None) -> None:
        """Join both requests, then verify both responses were persisted successfully."""

        errors = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"pipeline-{stage}") as executor:
            jobs = {executor.submit(module.run, episode_id, step_index,
                                    output_field=f"{stage}{index}_output", output_path=path): index
                    for index, module in enumerate(modules, start=1)}
            for future in as_completed(jobs):
                index = jobs[future]
                try:
                    result = future.result()
                    if result.error:
                        errors.append(f"{stage}{index}: {result.error}")
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    self._update(episode_id, path, step_index, **{f"{stage}{index}_output": {"api_error": message}})
                    errors.append(f"{stage}{index}: {message}")
                if progress_callback is not None:
                    progress_callback()
        step = self._step(episode_id, path, step_index)
        for index in (1, 2):
            if not _complete_action(step.get(f"{stage}{index}_output")):
                errors.append(f"{stage}{index}: no valid action was saved")
        if errors:
            raise ValueError("; ".join(errors))

    def run_step(self, episode_id: str, step_index: int, *, output_id: str | None = None,
                 progress_callback: Callable | None = None) -> PipelineStepResult:
        """Run one step against an existing output, or create a new snapshot."""

        path = self._output(episode_id, output_id)
        initial = self._step(episode_id, path, step_index)
        # Seed the current reference once before the loop. Previous screenshot
        # overlays are the exception: they always use the original annotation.
        if initial.get("reference_action") is None:
            reference = resolve_reference_action(initial)
            if not reference or not reference.get("type"):
                raise ValueError("The step must contain a valid reference action or original annotation")
            self._update(episode_id, path, step_index, reference_action=reference)
        else:
            _reference(initial)
        history: list[dict[str, Any]] = []
        state: dict[str, Any] = {"status": "running", "stage": "actor", "iteration": 0,
                                 "gatekeeper_calls": 0, "reason": None, "error": None}

        def notify(status: str, **details: Any) -> None:
            if progress_callback is not None:
                progress_callback({"stage": state["stage"], "status": status,
                                   "cycle": state["iteration"],
                                   "step": self._step(episode_id, path, step_index), **details})

        def stage(name: str) -> None:
            state["stage"] = name
            self._update(episode_id, path, step_index, pipeline_status=state, pipeline_history=history)
            notify("running", started=True)

        def finish(status: str, reason: str, error: str | None = None) -> PipelineStepResult:
            current = self._step(episode_id, path, step_index)
            if history:
                history[-1].update({name: copy.deepcopy(current.get(name)) for name in _ROUND_FIELDS})
                history[-1]["reference_after"] = copy.deepcopy(current.get("reference_action"))
                history[-1]["reference_bbox_generation"] = copy.deepcopy(current.get("reference_bbox_generation"))
                history[-1]["outcome"] = reason
            state.update(status=status, reason=reason, error=error)
            self._update(episode_id, path, step_index, pipeline_status=state, pipeline_history=history)
            return PipelineStepResult(episode_id, step_index, path, status, reason,
                                      state["iteration"], state["gatekeeper_calls"], error)

        self._update(episode_id, path, step_index, exclusion=False, pipeline_status=state, pipeline_history=history)
        try:
            for iteration in range(1, self.MAX_GATEKEEPER_CALLS + 1):
                state["iteration"] = iteration
                before = self._step(episode_id, path, step_index)
                history.append({"iteration": iteration, "reference_before": copy.deepcopy(_reference(before))})
                # Clear downstream responses so errors never expose an earlier round as current.
                self._update(episode_id, path, step_index, **{name: None for name in _ROUND_FIELDS})
                stage("actor")
                self._pair(self.actors, "actor", episode_id, path, step_index,
                           progress_callback=lambda: notify("running"))
                notify("completed")
                if _both_reproduce(self._step(episode_id, path, step_index), "actor"):
                    return finish("completed", "actors_reproduced")
                stage("arbiter")
                self._pair(self.arbiters, "arbiter", episode_id, path, step_index,
                           progress_callback=lambda: notify("running"))
                notify("completed")
                current = self._step(episode_id, path, step_index)
                if _both_reproduce(current, "arbiter"):
                    return finish("completed", "arbiters_reproduced")
                state["gatekeeper_calls"] += 1
                stage("gatekeeper")
                result = self.gatekeeper.run(episode_id, step_index, output_path=path)
                if result.error or result.case not in {"A", "B", "C"}:
                    raise ValueError(result.error or "Gatekeeper did not return a valid case")
                notify("completed")
                current = self._step(episode_id, path, step_index)
                history[-1].update({name: copy.deepcopy(current.get(name)) for name in _ROUND_FIELDS})
                if result.case == "A":
                    self._update(episode_id, path, step_index, exclusion=True)
                    return finish("excluded", "case_a")
                if result.case == "B":
                    return finish("completed", "case_b")
                if state["gatekeeper_calls"] == self.MAX_GATEKEEPER_CALLS:
                    self._update(episode_id, path, step_index, exclusion=True)
                    return finish("excluded", "gatekeeper_limit")
                if _reference(current)["type"] in _BBOX_ACTIONS:
                    stage("bbox")
                    self.bbox_generator.generate_for_output(episode_id, path.parent.name, step_index,
                                                             repository_root=self.root,
                                                             progress_callback=lambda payload: notify("running", bbox_progress=payload))
                    notify("completed")
                current = self._step(episode_id, path, step_index)
                history[-1].update(reference_after=copy.deepcopy(current.get("reference_action")),
                                   reference_bbox_generation=copy.deepcopy(current.get("reference_bbox_generation")),
                                   outcome="case_c")
        except Exception as exc:
            notify("failed", error=f"{type(exc).__name__}: {exc}")
            return finish("failed", f"{state['stage']}_error", f"{type(exc).__name__}: {exc}")
        raise AssertionError("The final Gatekeeper call must terminate the step")

    def run_episode(self, episode_id: str, *, output_id: str | None = None) -> list[PipelineStepResult]:
        """Run steps in order in one snapshot; a failed step does not retry or stop others."""

        path = self._output(episode_id, output_id)
        steps = read_output_snapshot(self.root, episode_id, path.parent.name)["steps"]
        return [self.run_step(episode_id, index, output_id=path.parent.name) for index in range(len(steps))]
