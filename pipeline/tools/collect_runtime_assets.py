from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from pipeline.resource_paths import PROJECT_ROOT, runtime_asset_path, runtime_assets_dir


LEGACY_DATA_ROOT = PROJECT_ROOT.parent
SPEAKER_MODEL_NAME = "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx"


def copy_file(src: Path, dst: Path, *, required: bool = False) -> bool:
    if not src.exists():
        message = f"missing: {src}"
        if required:
            raise FileNotFoundError(message)
        print(f"[skip] {message}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"[copy] {src} -> {dst}")
    return True


def copy_tree(src: Path, dst: Path, *, required: bool = False) -> bool:
    if not src.exists():
        message = f"missing: {src}"
        if required:
            raise FileNotFoundError(message)
        print(f"[skip] {message}")
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"[copytree] {src} -> {dst}")
    return True


def collect_reference_images() -> None:
    copy_file(
        LEGACY_DATA_ROOT / "减压蒸馏操作图片" / "人工正确.png",
        runtime_asset_path("reference_images", "distillation_correct_annotated.png"),
    )


def collect_demo_videos() -> None:
    src_dir = LEGACY_DATA_ROOT / "减压蒸馏"
    dst_dir = runtime_asset_path("demo_videos", "distillation")
    dst_dir.mkdir(parents=True, exist_ok=True)
    if not src_dir.exists():
        print(f"[skip] missing: {src_dir}")
        return
    copied = 0
    for src in sorted(src_dir.glob("*.mp4"), key=lambda item: item.name.lower()):
        if copy_file(src, dst_dir / src.name):
            copied += 1
    print(f"[demo] copied {copied} mp4 files")


def collect_cv_detection2_data() -> None:
    copy_tree(
        PROJECT_ROOT / "CV_detection2" / "data",
        runtime_asset_path("cv_detection2_data"),
    )


def collect_safety_data() -> None:
    copy_tree(
        PROJECT_ROOT / "pipeline" / "safety_check" / "data",
        runtime_asset_path("safety_check_data"),
    )


def collect_electrolysis_data() -> None:
    src_data = PROJECT_ROOT / "pipeline" / "electrolysis_check" / "data"
    dst_data = runtime_asset_path("electrolysis_check_data")
    copy_tree(src_data, dst_data)

    metadata_path = dst_data / "metadata.json"
    if not metadata_path.exists():
        return
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    copied = 0
    for sample in metadata.get("samples", []):
        label = str(sample.get("label", "")).strip()
        image_file = str(sample.get("image_file", "")).strip()
        image_path = Path(str(sample.get("image_path", "")).strip())
        if not label or not image_file or not image_path.exists():
            continue
        if copy_file(image_path, dst_data / "images" / label / image_file):
            copied += 1
    print(f"[electrolysis] copied {copied} reference images")


def collect_speaker_model() -> None:
    candidates = []
    env_path = os.getenv("SPEAKER_VERIFY_EMBEDDING_MODEL", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend([
        Path(r"D:\chemistry\speaker_verify_test\models") / SPEAKER_MODEL_NAME,
        PROJECT_ROOT / "pipeline" / "speaker_verify" / "models" / SPEAKER_MODEL_NAME,
    ])
    for src in candidates:
        if src.exists():
            copy_file(src, runtime_asset_path("speaker_models", SPEAKER_MODEL_NAME))
            return
    print("[skip] speaker embedding model not found")


def main() -> None:
    root = runtime_assets_dir()
    root.mkdir(parents=True, exist_ok=True)
    print(f"[runtime_assets] {root}")
    collect_reference_images()
    collect_demo_videos()
    collect_cv_detection2_data()
    collect_safety_data()
    collect_electrolysis_data()
    collect_speaker_model()
    print("[done] runtime assets collected")


if __name__ == "__main__":
    main()
