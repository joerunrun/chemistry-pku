from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.qa.omni_tts import synthesize_text_to_audio


PROMPTS = {
    "start_experiment_check": "开始实验纠错，请保持画面稳定。",
    "demo_select": "请选择要播放的演示视频。",
    "demo_start": "开始播放演示视频。",
    "demo_stop": "已停止播放。",
}
def default_output_dir() -> Path:
    return Path(r"D:\Android\projects\app\src\main\res\raw")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local prompt WAV files with Qwen Omni.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=default_output_dir(),
        help="Android res/raw output directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate existing WAV files.",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in PROMPTS.items():
        out_path = args.out_dir / f"{name}.wav"
        if out_path.exists() and not args.force:
            print(f"skip {out_path}")
            continue
        print(f"generate {out_path}: {text}")
        result = synthesize_text_to_audio(text)
        out_path.write_bytes(result["audio_bytes"])


if __name__ == "__main__":
    main()
