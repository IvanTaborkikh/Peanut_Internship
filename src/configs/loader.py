"""YAML config loader with ${ENV_VAR} expansion."""
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.configs.schema import BotConfig


_ENV_VAR_PATTERN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


class ConfigError(Exception):
    pass


def _expand_env_vars(raw: str, env: dict[str, str]) -> str:
    """Replace ${VAR} occurrences with env[VAR]. Raises if any var is missing."""
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        name = match.group(1)
        value = env.get(name)
        if value is None:
            missing.append(name)
            return match.group(0)
        return value

    expanded = _ENV_VAR_PATTERN.sub(replace, raw)
    if missing:
        unique = sorted(set(missing))
        raise ConfigError(f"missing environment variables: {', '.join(unique)}")
    return expanded


def load_config(
    path: str | Path,
    env: dict[str, str] | None = None,
    load_dotenv_file: bool = True,
) -> BotConfig:
    """Load YAML config, expand ${ENV_VAR} refs, validate against BotConfig schema.

    Args:
        path: path to a YAML config file
        env: explicit env dict; defaults to os.environ
        load_dotenv_file: if True, load .env into os.environ before reading env vars
    """
    if load_dotenv_file and env is None:
        load_dotenv()

    if env is None:
        env = dict(os.environ)

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    raw_text = config_path.read_text(encoding='utf-8')
    expanded = _expand_env_vars(raw_text, env)
    data: Any = yaml.safe_load(expanded)
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")

    return BotConfig(**data)


def load_config_from_dict(data: dict) -> BotConfig:
    """Validate an already-parsed config dict (mainly for tests)."""
    return BotConfig(**data)
