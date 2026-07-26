"""Load and validate experiment specifications from specs/*.json."""
from __future__ import annotations

import json
from pathlib import Path
from string import Formatter
from typing import Any

HERE = Path(__file__).resolve().parent
SPECS_DIR = HERE / "specs"
SUPPORTED_STRATEGIES = {"cv_reference", "retrieval", "vlm_only"}

_specs: dict[str, dict[str, Any]] = {}
_loaded = False


class SpecValidationError(ValueError):
    pass


def _required_text(data: dict[str, Any], field: str, path: Path) -> str:
    value = str(data.get(field, "")).strip()
    if not value:
        raise SpecValidationError(f"{path.name}: missing non-empty '{field}'")
    return value


def _validate_prompt(
    data: dict[str, Any],
    field: str,
    allowed_fields: set[str],
    path: Path,
) -> None:
    prompt = _required_text(data, field, path)
    placeholders = {
        name
        for _literal, name, _format_spec, _conversion in Formatter().parse(prompt)
        if name
    }
    unsupported = placeholders - allowed_fields
    if unsupported:
        raise SpecValidationError(
            f"{path.name}: unsupported placeholders in '{field}': "
            f"{', '.join(sorted(unsupported))}"
        )


def _validate_spec(data: Any, path: Path) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecValidationError(f"{path.name}: top-level JSON must be an object")

    experiment_id = _required_text(data, "id", path)
    if experiment_id != path.stem:
        raise SpecValidationError(
            f"{path.name}: id '{experiment_id}' must match filename '{path.stem}'"
        )
    _required_text(data, "name", path)
    _required_text(data, "system_prompt", path)

    strategy = _required_text(data, "strategy", path)
    if strategy not in SUPPORTED_STRATEGIES:
        raise SpecValidationError(
            f"{path.name}: unsupported strategy '{strategy}'"
        )

    if strategy == "cv_reference":
        components = data.get("components")
        if not isinstance(components, list) or not components:
            raise SpecValidationError(f"{path.name}: cv_reference requires components")
        labels: set[str] = set()
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                raise SpecValidationError(
                    f"{path.name}: components[{index}] must be an object"
                )
            label = _required_text(component, "label", path)
            _required_text(component, "name", path)
            _required_text(component, "color", path)
            if label in labels:
                raise SpecValidationError(
                    f"{path.name}: duplicate component label '{label}'"
                )
            labels.add(label)
        _validate_prompt(
            data,
            "prompt_a",
            {"color_legend", "cv_summary", "requirements"},
            path,
        )
        reference = data.get("reference")
        if not isinstance(reference, dict):
            raise SpecValidationError(f"{path.name}: cv_reference requires reference")
        source = _required_text(reference, "source", path)
        if source not in {"file", "database"}:
            raise SpecValidationError(
                f"{path.name}: reference.source must be file or database"
            )
        if source == "file":
            _required_text(reference, "annotated_image_path", path)

    elif strategy == "retrieval":
        retrieval_db = _required_text(data, "retrieval_db", path)
        if retrieval_db not in {"electrolysis_check_data", "safety_check_data"}:
            raise SpecValidationError(
                f"{path.name}: unsupported retrieval_db '{retrieval_db}'"
            )
        _validate_prompt(
            data,
            "prompt",
            {"correct_description", "wrong_description", "top_k_descriptions"},
            path,
        )
        _required_text(data, "empty_result_text", path)

    else:
        _validate_prompt(data, "prompt", set(), path)
        _validate_vlm_reference_images(data, path)

    return data


def _validate_vlm_reference_images(data: dict[str, Any], path: Path) -> None:
    """Validate optional static examples passed directly to a VLM-only check."""
    references = data.get("reference_images")
    if references is None:
        return
    if not isinstance(references, list) or not references:
        raise SpecValidationError(
            f"{path.name}: reference_images must be a non-empty list when provided"
        )

    roles: set[str] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise SpecValidationError(
                f"{path.name}: reference_images[{index}] must be an object"
            )
        role = _required_text(reference, "role", path)
        if role not in {"correct", "wrong"}:
            raise SpecValidationError(
                f"{path.name}: reference_images[{index}].role must be correct or wrong"
            )
        if role in roles:
            raise SpecValidationError(
                f"{path.name}: duplicate reference image role '{role}'"
            )
        roles.add(role)
        _required_text(reference, "path", path)

    if roles != {"correct", "wrong"}:
        raise SpecValidationError(
            f"{path.name}: reference_images must contain one correct and one wrong image"
        )


def _load_all() -> dict[str, dict[str, Any]]:
    global _loaded, _specs
    if _loaded:
        return _specs

    loaded: dict[str, dict[str, Any]] = {}
    if not SPECS_DIR.is_dir():
        raise SpecValidationError(f"Experiment specs directory not found: {SPECS_DIR}")

    for path in sorted(SPECS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpecValidationError(f"{path.name}: invalid JSON: {exc}") from exc
        data = _validate_spec(raw, path)
        experiment_id = data["id"]
        if experiment_id in loaded:
            raise SpecValidationError(
                f"{path.name}: duplicate experiment id '{experiment_id}'"
            )
        loaded[experiment_id] = data

    _specs = loaded
    _loaded = True
    return _specs


def get_spec(experiment_id: str) -> dict[str, Any] | None:
    return _load_all().get(experiment_id)


def list_specs() -> list[dict[str, Any]]:
    return sorted(_load_all().values(), key=lambda spec: spec["id"])


def reload_specs() -> None:
    global _loaded, _specs
    _loaded = False
    _specs = {}
    _load_all()
