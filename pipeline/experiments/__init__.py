"""JSON-backed experiment registry."""
from __future__ import annotations

from pipeline.experiments.spec_loader import get_spec, list_specs, reload_specs


def get_experiment_config(name: str):
    """Compatibility name for callers; all configs are JSON dictionaries."""
    return get_spec(name)


def resolve_experiment_spec(name: str) -> dict | None:
    """Return the spec dict for any experiment."""
    return get_spec(name)
