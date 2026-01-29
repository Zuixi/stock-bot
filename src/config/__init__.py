"""Configuration loading utilities."""

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).parent


def load_config(name: str) -> dict[str, Any]:
    """Load a YAML configuration file from the config directory.

    Args:
        name: Config file name without extension (e.g., 'sse')

    Returns:
        Parsed configuration dictionary

    Raises:
        FileNotFoundError: If the config file doesn't exist
        yaml.YAMLError: If the config file is invalid YAML
    """
    config_path = CONFIG_DIR / f"{name}.yaml"
    if not config_path.exists():
        sample_path = CONFIG_DIR / f"{name}.sample.yaml"
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Please copy {sample_path} to {config_path} and fill in your values."
        )

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config_path(name: str) -> Path:
    """Get the path to a configuration file."""
    return CONFIG_DIR / f"{name}.yaml"
