"""Minimal reference-audio check for speaker verification.

This command does not persist embeddings yet; it validates that a reference
audio file can be decoded and compared against itself with the same API used
by verification.
"""

from __future__ import annotations

import argparse

from .compare import compare_speakers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Reference WAV file.")
    parser.add_argument("--embedding-model", default=None, help="sherpa-onnx speaker embedding model.")
    parser.add_argument("--silero-vad-model", default=None, help="Optional Silero VAD ONNX model.")
    parser.add_argument("--gain-db", type=float, default=0.0, help="Gain applied before VAD.")
    parser.add_argument("--min-speech-ms", type=int, default=800, help="Minimum speech segment length kept for embedding.")
    args = parser.parse_args()

    result = compare_speakers(
        args.audio,
        args.audio,
        embedding_model=args.embedding_model,
        silero_vad_model=args.silero_vad_model,
        reference_gain_db=args.gain_db,
        query_gain_db=args.gain_db,
        min_speech_ms=args.min_speech_ms,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
