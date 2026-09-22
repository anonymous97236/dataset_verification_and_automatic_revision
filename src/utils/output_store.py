"""Filesystem-backed output snapshots for interactive pipeline runs."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_STEP_OUTPUT_LOCKS: dict[Path, threading.Lock] = {}
_STEP_OUTPUT_LOCKS_GUARD = threading.Lock()


def _write_snapshot(destination: Path, payload: dict[str, Any]) -> None:
    """Publish a complete JSON file so parallel readers never see partial writes."""

    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".data-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _simple_name(value: str, *, field_name: str) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name:
        raise ValueError(f"{field_name} must be a simple name")
    return name


def output_root(repository_root: Path) -> Path:
    """Return the root directory containing all episode output snapshots."""

    return repository_root / "pipeline_outputs"


def output_data_path(repository_root: Path, episode_id: str, output_id: str) -> Path:
    """Return the canonical ``data.json`` path for one output id."""

    return output_root(repository_root) / _simple_name(episode_id, field_name="episode_id") / _simple_name(output_id, field_name="output_id") / "data.json"


def list_output_ids(repository_root: Path, episode_id: str) -> list[str]:
    """List valid output ids for an episode, newest first."""

    directory = output_root(repository_root) / _simple_name(episode_id, field_name="episode_id")
    if not directory.is_dir():
        return []
    return sorted(
        (item.name for item in directory.iterdir() if item.is_dir() and (item / "data.json").is_file()),
        reverse=True,
    )


def create_output_snapshot(repository_root: Path, episode_id: str, sample: dict[str, Any]) -> tuple[str, Path]:
    """Copy sample data into one new, uniquely identified output directory."""

    episode_name = _simple_name(episode_id, field_name="episode_id")
    episode_directory = output_root(repository_root) / episode_name
    episode_directory.mkdir(parents=True, exist_ok=True)
    while True:
        output_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}_{uuid.uuid4().hex[:8]}"
        directory = episode_directory / output_id
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        destination = directory / "data.json"
        _write_snapshot(destination, copy.deepcopy(sample))
        return output_id, destination


def ensure_output_snapshot(repository_root: Path, episode_id: str, sample: dict[str, Any]) -> tuple[str, Path]:
    """Return an existing output, creating one only when none exists."""

    output_ids = list_output_ids(repository_root, episode_id)
    if output_ids:
        output_id = output_ids[0]
        return output_id, output_data_path(repository_root, episode_id, output_id)
    return create_output_snapshot(repository_root, episode_id, sample)


def read_output_snapshot(repository_root: Path, episode_id: str, output_id: str) -> dict[str, Any]:
    """Read and validate one output snapshot's top-level JSON object."""

    path = output_data_path(repository_root, episode_id, output_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Unknown output id: {output_id}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid output data for {output_id}: {exc.msg}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        raise ValueError(f"Invalid output data for {output_id}")
    return value


def update_step_fields(
    repository_root: Path,
    *,
    episode_id: str,
    output_id: str,
    step_index: int,
    updates: dict[str, Any],
) -> Path:
    """Atomically replace non-identity fields in an existing output step."""

    root = Path(repository_root).resolve()
    episode_name = _simple_name(episode_id, field_name="episode_id")
    output_name = _simple_name(output_id, field_name="output_id")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
        raise ValueError("step_index must be a non-negative integer")
    if not isinstance(updates, dict) or not updates:
        raise ValueError("updates must be a non-empty JSON object")
    if {"step_id", "index"}.intersection(updates):
        raise ValueError("updates must not replace step identity")

    destination = output_data_path(root, episode_name, output_name).resolve()
    with _STEP_OUTPUT_LOCKS_GUARD:
        lock = _STEP_OUTPUT_LOCKS.setdefault(destination, threading.Lock())
    with lock:
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Pipeline output does not exist: {destination}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in pipeline output: {exc.msg}") from exc
        steps = payload.get("steps") if isinstance(payload, dict) else None
        if not isinstance(steps, list) or step_index >= len(steps) or not isinstance(steps[step_index], dict):
            raise ValueError("Pipeline output does not contain the requested step")
        steps[step_index].update(copy.deepcopy(updates))
        _write_snapshot(destination, payload)
    return destination


def store_step_output(
    repository_root: Path,
    *,
    episode_id: str,
    sample: dict[str, Any],
    step_index: int,
    output_field: str,
    output: dict[str, Any],
    output_path: str | Path | None = None,
    step_updates: dict[str, Any] | None = None,
) -> Path:
    """Store one module response, replacing its field in the selected step.

    When a caller supplies an existing output path, each invocation overwrites
    that module's prior result.  A process-local per-file lock lets separate
    Actor or Arbiter instances safely update distinct fields in one snapshot.
    Related step updates are written under the same lock as the response.
    """

    root = Path(repository_root).resolve()
    episode_name = _simple_name(episode_id, field_name="episode_id")
    field = str(output_field or "").strip()
    if not field:
        raise ValueError("output_field must be a non-empty string")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
        raise ValueError("step_index must be a non-negative integer")
    if not isinstance(sample, dict):
        raise ValueError("sample must be a JSON object")
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    if step_updates is not None and not isinstance(step_updates, dict):
        raise ValueError("step_updates must be a JSON object")
    if step_updates and (field in step_updates or {"step_id", "index"}.intersection(step_updates)):
        raise ValueError("step_updates must not replace the response field or step identity")

    outputs_dir = output_root(root).resolve()
    if output_path is None:
        _output_id, destination = create_output_snapshot(root, episode_name, sample)
    else:
        destination = Path(output_path).resolve()
        try:
            destination.relative_to(outputs_dir)
        except ValueError as exc:
            raise ValueError("output_path must be inside pipeline_outputs") from exc
        expected = output_data_path(root, episode_name, destination.parent.name)
        if destination.name != "data.json" or destination != expected:
            raise ValueError("output_path must be an episode output's data.json")

    canonical_destination = destination.resolve()
    with _STEP_OUTPUT_LOCKS_GUARD:
        lock = _STEP_OUTPUT_LOCKS.setdefault(canonical_destination, threading.Lock())
    with lock:
        try:
            payload = json.loads(canonical_destination.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Pipeline output does not exist: {canonical_destination}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in pipeline output: {exc.msg}") from exc
        steps = payload.get("steps") if isinstance(payload, dict) else None
        if not isinstance(steps, list) or step_index >= len(steps) or not isinstance(steps[step_index], dict):
            raise ValueError("Pipeline output does not contain the requested step")
        steps[step_index][field] = output
        steps[step_index].update(step_updates or {})
        _write_snapshot(canonical_destination, payload)
    return canonical_destination
