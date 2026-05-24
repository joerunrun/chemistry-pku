"""Fast local speaker verification.

The main entry point is ``compare_speakers(reference_audio, query_audio)``.
It accepts two WAV files or WAV bytes, extracts speech chunks, computes
sherpa-onnx speaker embeddings, and returns a small decision object.
"""

from __future__ import annotations

import json
import math
import os
import wave
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.signal import resample_poly

SAMPLE_RATE = 16000
MIN_SPEECH_MS = 800
WINDOW_MS = 1500
HOP_MS = 500
SAME_THRESHOLD = 0.70
UNCERTAIN_THRESHOLD = 0.58

AudioInput = str | Path | bytes | bytearray


@dataclass
class SpeakerCompareResult:
    decision: str
    score: float
    speech_ms: int
    num_segments: int
    reference_segments: int
    query_segments: int
    centroid_score: float
    bank_score: float
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def compare_speakers(
    reference_audio: AudioInput,
    query_audio: AudioInput,
    *,
    embedding_model: str | Path | None = None,
    silero_vad_model: str | Path | None = None,
    reference_gain_db: float = 0.0,
    query_gain_db: float = 0.0,
    min_speech_ms: int = MIN_SPEECH_MS,
    same_threshold: float = SAME_THRESHOLD,
    uncertain_threshold: float = UNCERTAIN_THRESHOLD,
) -> SpeakerCompareResult:
    """Compare whether two audio clips are from the same speaker.

    Args:
        reference_audio: WAV path or WAV bytes, ideally about 5 seconds.
        query_audio: WAV path or WAV bytes, ideally 1.5 to 3 seconds.
        embedding_model: sherpa-onnx speaker embedding ONNX model path. If not
            given, reads ``SPEAKER_VERIFY_EMBEDDING_MODEL``.
        silero_vad_model: optional Silero VAD ONNX model path. If not given,
            reads ``SPEAKER_VERIFY_SILERO_VAD_MODEL``. If still missing, a
            lightweight energy VAD fallback is used.
    """

    embedding_model = str(embedding_model or os.getenv("SPEAKER_VERIFY_EMBEDDING_MODEL", "")).strip()
    silero_vad_model = str(silero_vad_model or os.getenv("SPEAKER_VERIFY_SILERO_VAD_MODEL", "")).strip()
    if not embedding_model:
        raise ValueError(
            "Missing speaker embedding model. Set SPEAKER_VERIFY_EMBEDDING_MODEL "
            "or pass embedding_model=..."
        )

    reference_wave = _apply_gain(_read_wav_mono(reference_audio), reference_gain_db)
    query_wave = _apply_gain(_read_wav_mono(query_audio), query_gain_db)

    ref_chunks = _speech_windows(reference_wave, silero_vad_model, min_speech_ms=min_speech_ms)
    query_chunks = _speech_windows(query_wave, silero_vad_model, min_speech_ms=min_speech_ms)
    query_speech_ms = int(sum(len(chunk) for chunk in query_chunks) * 1000 / SAMPLE_RATE)

    if not query_chunks:
        return SpeakerCompareResult(
            decision="no_speech",
            score=0.0,
            speech_ms=0,
            num_segments=0,
            reference_segments=len(ref_chunks),
            query_segments=0,
            centroid_score=0.0,
            bank_score=0.0,
            message="No speech was detected in query audio.",
        )
    if query_speech_ms < MIN_SPEECH_MS:
        return SpeakerCompareResult(
            decision="too_short",
            score=0.0,
            speech_ms=query_speech_ms,
            num_segments=len(query_chunks),
            reference_segments=len(ref_chunks),
            query_segments=len(query_chunks),
            centroid_score=0.0,
            bank_score=0.0,
            message="Query speech is too short for reliable verification.",
        )
    if not ref_chunks:
        return SpeakerCompareResult(
            decision="bad_reference",
            score=0.0,
            speech_ms=query_speech_ms,
            num_segments=len(query_chunks),
            reference_segments=0,
            query_segments=len(query_chunks),
            centroid_score=0.0,
            bank_score=0.0,
            message="No speech was detected in reference audio.",
        )

    model = _get_embedding_model(embedding_model)
    ref_embeddings = _normalize_rows(np.vstack([model.compute(chunk) for chunk in ref_chunks]))
    query_embeddings = _normalize_rows(np.vstack([model.compute(chunk) for chunk in query_chunks]))

    ref_centroid = _normalize(ref_embeddings.mean(axis=0))
    query_centroid = _normalize(query_embeddings.mean(axis=0))
    centroid_score = float(np.dot(ref_centroid, query_centroid))

    pair_scores = query_embeddings @ ref_embeddings.T
    top_k = min(3, pair_scores.size)
    bank_score = float(np.mean(np.partition(pair_scores.ravel(), -top_k)[-top_k:]))
    score = float(0.75 * centroid_score + 0.25 * bank_score)

    if score >= same_threshold:
        decision = "same_speaker"
    elif score >= uncertain_threshold:
        decision = "uncertain"
    else:
        decision = "other_speaker"

    return SpeakerCompareResult(
        decision=decision,
        score=round(score, 4),
        speech_ms=query_speech_ms,
        num_segments=len(query_chunks),
        reference_segments=len(ref_chunks),
        query_segments=len(query_chunks),
        centroid_score=round(centroid_score, 4),
        bank_score=round(bank_score, 4),
    )


class _EmbeddingModel:
    def __init__(self, model_path: str):
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("Install sherpa-onnx first: pip install sherpa-onnx") from exc

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path,
            num_threads=max(1, min(4, os.cpu_count() or 1)),
            debug=False,
            provider="cpu",
        )
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def compute(self, samples: np.ndarray) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32))
        stream.input_finished()
        embedding = self.extractor.compute(stream)
        return np.asarray(embedding, dtype=np.float32)


@lru_cache(maxsize=4)
def _get_embedding_model(model_path: str) -> _EmbeddingModel:
    return _EmbeddingModel(model_path)


def _read_wav_mono(audio: AudioInput) -> np.ndarray:
    if isinstance(audio, (str, Path)):
        wav_obj = wave.open(str(audio), "rb")
    elif isinstance(audio, (bytes, bytearray)):
        import io

        wav_obj = wave.open(io.BytesIO(audio), "rb")
    else:
        raise TypeError("audio must be a WAV path or WAV bytes")

    with wav_obj as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    samples = _pcm_to_float32(frames, sample_width)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        gcd = math.gcd(sample_rate, SAMPLE_RATE)
        samples = resample_poly(samples, SAMPLE_RATE // gcd, sample_rate // gcd).astype(np.float32)
    return np.clip(samples, -1.0, 1.0)


def _pcm_to_float32(frames: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        return (data - 128.0) / 128.0
    if sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32)
        return data / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        signed = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        signed = np.where(signed & 0x800000, signed | ~0xFFFFFF, signed)
        return signed.astype(np.float32) / 8388608.0
    if sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32)
        return data / 2147483648.0
    raise ValueError(f"Unsupported WAV sample width: {sample_width}")


def _speech_windows(samples: np.ndarray, vad_model: str = "", *, min_speech_ms: int = MIN_SPEECH_MS) -> list[np.ndarray]:
    speech_segments = _silero_vad(samples, vad_model) if vad_model else _energy_vad(samples)
    windows: list[np.ndarray] = []
    win = int(WINDOW_MS * SAMPLE_RATE / 1000)
    hop = int(HOP_MS * SAMPLE_RATE / 1000)
    min_len = int(min_speech_ms * SAMPLE_RATE / 1000)

    for segment in speech_segments:
        if len(segment) < min_len:
            continue
        if len(segment) <= win:
            windows.append(_pad_or_trim(segment, win))
            continue
        for start in range(0, len(segment) - win + 1, hop):
            windows.append(segment[start : start + win])
        tail = segment[-win:]
        if not windows or not np.array_equal(windows[-1], tail):
            windows.append(tail)
    return windows


def _apply_gain(samples: np.ndarray, gain_db: float) -> np.ndarray:
    if abs(gain_db) <= 1e-6:
        return samples
    factor = 10.0 ** (gain_db / 20.0)
    return np.clip(samples * factor, -1.0, 1.0).astype(np.float32)


def _silero_vad(samples: np.ndarray, model_path: str) -> list[np.ndarray]:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError("Install sherpa-onnx first: pip install sherpa-onnx") from exc

    config = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=model_path,
            threshold=0.5,
            min_silence_duration=0.25,
            min_speech_duration=0.25,
            window_size=512,
        ),
        sample_rate=SAMPLE_RATE,
        num_threads=1,
        debug=False,
    )
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=max(10, int(len(samples) / SAMPLE_RATE) + 2))
    vad.accept_waveform(samples.astype(np.float32))

    segments = []
    while not vad.empty():
        segment = vad.front
        segments.append(np.asarray(segment.samples, dtype=np.float32))
        vad.pop()
    return segments


def _energy_vad(samples: np.ndarray) -> list[np.ndarray]:
    frame = int(0.03 * SAMPLE_RATE)
    hop = int(0.01 * SAMPLE_RATE)
    if len(samples) < frame:
        return []

    energies = []
    starts = range(0, len(samples) - frame + 1, hop)
    for start in starts:
        chunk = samples[start : start + frame]
        energies.append(float(np.sqrt(np.mean(chunk * chunk))))

    energy = np.asarray(energies, dtype=np.float32)
    floor = float(np.percentile(energy, 20))
    threshold = max(0.012, floor * 3.0)
    voiced = energy >= threshold
    return _merge_voiced_frames(samples, voiced, frame, hop)


def _merge_voiced_frames(samples: np.ndarray, voiced: np.ndarray, frame: int, hop: int) -> list[np.ndarray]:
    segments: list[tuple[int, int]] = []
    start = None
    for i, is_voiced in enumerate(voiced):
        if is_voiced and start is None:
            start = i * hop
        elif not is_voiced and start is not None:
            segments.append((start, i * hop + frame))
            start = None
    if start is not None:
        segments.append((start, len(samples)))

    merged: list[tuple[int, int]] = []
    max_gap = int(0.25 * SAMPLE_RATE)
    for start, end in segments:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [samples[start:end] for start, end in merged]


def _pad_or_trim(samples: np.ndarray, length: int) -> np.ndarray:
    if len(samples) >= length:
        return samples[:length]
    return np.pad(samples, (0, length - len(samples))).astype(np.float32)


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    return np.vstack([_normalize(row) for row in matrix])


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-8:
        return vector
    return vector / norm
