from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.qa.omni_tts import synthesize_text_to_audio


PROMPTS = {
    "assistant_ready": "助手就绪，请说指令。",
    "ask_experiment": "进入实验纠错，请选择具体要纠正的实验。",
    "start_safety": "开始安全检查，请保持装置在视野中央。",
    "start_capture": "开始拍照，请保持画面稳定。",
    "unclear": "我没有听清，请再说一遍。",
    "qa_start": "请开始提问。",
    "recording_start": "开始记录。",
    "recording_stop": "记录已停止。",
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
