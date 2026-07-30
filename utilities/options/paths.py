"""Shared path resolution for options-pipeline artifacts."""

from __future__ import annotations

from pathlib import Path


def strategy_data_root(root: Path, strategy: dict) -> Path:
    """Resolve the configured strategy-data root with legacy precedence."""
    configured = Path(strategy.get("strategy_data_root", "data")).expanduser()
    return (configured if configured.is_absolute() else root / configured).resolve()
