"""Loading ``config/shadow_config.yaml`` and resolving its paths (operational config only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.shadow_logger import PROJECT_ROOT

__all__ = ["CONFIG_PATH", "load_config", "config_path", "config_thresholds"]

CONFIG_PATH: Path = PROJECT_ROOT / "config" / "shadow_config.yaml"
_DEFAULT_PATHS = {"predictions": "data/predictions", "evaluations": "data/evaluations",
                  "output": "output", "reports": "output/reports", "logs": "logs"}


def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    """Load the shadow configuration YAML.

    Args:
        path: Path to ``shadow_config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def config_path(config: dict[str, Any], key: str) -> Path:
    """Resolve ``paths.<key>`` (relative paths are relative to the project root).

    Args:
        config: Parsed configuration.
        key: One of predictions, evaluations, output, reports, logs.

    Returns:
        Path: Absolute path.
    """
    p = Path((config.get("paths") or {}).get(key, _DEFAULT_PATHS[key]))
    return p if p.is_absolute() else PROJECT_ROOT / p


def config_thresholds(config: dict[str, Any]) -> dict[str, float]:
    """Return the ``thresholds`` block (alert thresholds for metrics).

    Args:
        config: Parsed configuration.

    Returns:
        dict: Threshold name → value.
    """
    return dict(config.get("thresholds") or {})
