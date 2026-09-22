"""Shared provider selection, environment loading, and startup validation."""

from __future__ import annotations

import os
from pathlib import Path


_ROLE_NAMES = ("actor1", "actor2", "arbiter1", "arbiter2", "gatekeeper", "bbox_generator")
_PROVIDERS = frozenset({"gemini", "qwen"})


def repository_root() -> Path:
    """Return the reproducibility-material repository root."""

    return Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> list[str]:
    """Load simple ``KEY=VALUE`` entries without adding a dotenv dependency."""

    errors: list[str] = []
    if not path.is_file():
        return [f"Missing required configuration file: {path.name}"]
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"Invalid {path.name} entry at line {line_number}")
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            errors.append(f"Invalid {path.name} entry at line {line_number}")
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key] = value
    return errors


def _nonempty_env_fields(path: Path) -> tuple[list[str], list[str]]:
    """Return parsing errors and blank assignment names from an env file."""

    errors: list[str] = []
    blanks: list[str] = []
    if not path.is_file():
        return [f"Missing required configuration file: {path.name}"], blanks
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"Invalid {path.name} entry at line {line_number}")
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            errors.append(f"Invalid {path.name} entry at line {line_number}")
        elif not value:
            blanks.append(key)
    return errors, blanks


def _read_provider_selection(path: Path) -> tuple[dict[str, str], list[str]]:
    """Read the compact role-to-provider YAML mapping used by this repository."""

    errors: list[str] = []
    values: dict[str, str] = {}
    if not path.is_file():
        return values, [f"Missing required configuration file: {path.name}"]
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errors.append(f"Invalid {path.name} entry at line {line_number}")
            continue
        role, provider = (part.strip() for part in line.split(":", 1))
        if role in values:
            errors.append(f"Duplicate role in {path.name}: {role}")
        values[role] = provider
    return values, errors


def provider_for_role(role: str, root: Path | None = None) -> str:
    """Return the configured provider for one pipeline role."""

    name = str(role or "").strip()
    if name not in _ROLE_NAMES:
        raise ValueError(f"Unsupported pipeline role: {name}")
    configuration, errors = _read_provider_selection((root or repository_root()) / "config.yaml")
    if errors:
        raise ValueError("; ".join(errors))
    provider = configuration.get(name, "")
    if provider not in _PROVIDERS:
        raise ValueError(f"Configure {name} as 'gemini' or 'qwen' in config.yaml")
    return provider


def validate_startup(root: Path | None = None) -> list[str]:
    """Return all configuration issues that should prevent web-server startup."""

    root = root or repository_root()
    env_path, config_path = root / ".env", root / "config.yaml"
    env_errors, blank_env_fields = _nonempty_env_fields(env_path)
    config, config_errors = _read_provider_selection(config_path)
    errors = [*env_errors, *config_errors]
    errors.extend(f"Fill {env_path.name}: {name}" for name in blank_env_fields)

    for role in _ROLE_NAMES:
        provider = config.get(role, "")
        if not provider:
            errors.append(f"Fill {config_path.name}: {role}")
        elif provider not in _PROVIDERS:
            errors.append(f"Use 'gemini' or 'qwen' for {config_path.name}: {role}")
    for extra_role in sorted(set(config) - set(_ROLE_NAMES)):
        errors.append(f"Unsupported role in {config_path.name}: {extra_role}")
    return errors


def startup_error_message(errors: list[str]) -> str:
    """Render a concise English message suitable for terminal output."""

    lines = ["The web application was not started.", "Complete the following configuration fields:"]
    lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines)
