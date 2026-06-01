from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ASSETS_ENV = "CHEM_RUNTIME_ASSETS_DIR"


def runtime_assets_dir() -> Path:
    raw = os.getenv(RUNTIME_ASSETS_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return PROJECT_ROOT / "runtime_assets"


def runtime_asset_path(*parts: str) -> Path:
    return runtime_assets_dir().joinpath(*parts)


def preferred_runtime_dir(asset_name: str, fallback: Path) -> Path:
    env_name = f"CHEM_{asset_name.upper()}_DIR"
    raw = os.getenv(env_name, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    candidate = runtime_asset_path(asset_name)
    if candidate.exists():
        return candidate
    return fallback
