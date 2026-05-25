"""Experiment config registry."""
from __future__ import annotations

from pipeline.experiments import distillation, other, photometer

_REGISTRY = {
    "distillation": distillation,
    "photometer": photometer,
    "other": other,
}


def get_experiment_config(name: str):
    """Return an experiment config module, or None when the name is unknown."""
    return _REGISTRY.get(name)
