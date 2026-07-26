from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path
from threading import Lock
import uuid

from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, build_image_part_from_bytes, create_vlm
from pipeline.resource_paths import runtime_assets_dir


_AUDIO_SUFFIXES = {".wav", ".m4a", ".aac", ".mp3"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class MediaAnalysisQueue:
    """Durable, small background queue for uploaded audio and photos."""

    def __init__(self) -> None:
        self._root = runtime_assets_dir() / "media_analysis_jobs"
        self._workers = max(1, int(os.getenv("CHEM_MEDIA_ANALYSIS_WORKERS", "1")))
        self._executor = ThreadPoolExecutor(
            max_workers=self._workers,
            thread_name_prefix="media-analysis",
        )
        self._lock = Lock()
        self._started = False
        self._queued: set[str] = set()
        self._provider = os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER).strip() or DEFAULT_VLM_PROVIDER
        self._model = os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL).strip() or DEFAULT_VLM_MODEL

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._root.mkdir(parents=True, exist_ok=True)
            pending = list(self._root.glob("*.json"))
        for path in pending:
            job = self._read_json(path)
            if job and job.get("status") in {"queued", "running"}:
                job["status"] = "queued"
                job["updated_at"] = self._now()
                self._write_json(path, job)
                self._submit(path)

    def enqueue(self, media_dir: Path, file_path: Path) -> None:
        suffix = file_path.suffix.lower()
        if suffix not in _AUDIO_SUFFIXES | _IMAGE_SUFFIXES:
            return
        self.start()
        kind = "audio" if suffix in _AUDIO_SUFFIXES else "image"
        job_id = uuid.uuid4().hex
        job_path = self._root / f"{job_id}.json"
        job = {
            "job_id": job_id,
            "status": "queued",
            "kind": kind,
            "media_dir": str(media_dir.resolve()),
            "filename": file_path.name,
            "provider": self._provider,
            "model": self._model,
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        self._write_json(job_path, job)
        self._set_analysis_item(media_dir, file_path.name, {
            "kind": kind,
            "status": "queued",
            "updated_at": job["updated_at"],
        })
        self._submit(job_path)

    def _submit(self, job_path: Path) -> None:
        with self._lock:
            if str(job_path) in self._queued:
                return
            self._queued.add(str(job_path))
        self._executor.submit(self._run, job_path)

    def _run(self, job_path: Path) -> None:
        try:
            job = self._read_json(job_path)
            if not job:
                return
            media_dir = Path(str(job["media_dir"])).resolve()
            file_path = media_dir / str(job["filename"])
            if not file_path.exists() or not file_path.is_file():
                raise FileNotFoundError(f"media file not found: {file_path.name}")

            job["status"] = "running"
            job["updated_at"] = self._now()
            self._write_json(job_path, job)
            self._set_analysis_item(media_dir, file_path.name, {
                "kind": job["kind"], "status": "running", "updated_at": job["updated_at"],
            })

            if job["kind"] == "audio":
                result = self._transcribe_audio(file_path, job)
            else:
                result = self._describe_image(file_path, job)

            job.update({"status": "succeeded", "result": result, "updated_at": self._now()})
            self._write_json(job_path, job)
            self._set_analysis_item(media_dir, file_path.name, {
                "kind": job["kind"], "status": "succeeded", "updated_at": job["updated_at"], **result,
            })
        except Exception as exc:
            job = self._read_json(job_path) or {}
            job.update({"status": "failed", "error": str(exc)[:1000], "updated_at": self._now()})
            self._write_json(job_path, job)
            media_dir = Path(str(job.get("media_dir", "")))
            filename = str(job.get("filename", ""))
            if media_dir.exists() and filename:
                self._set_analysis_item(media_dir, filename, {
                    "kind": job.get("kind", ""), "status": "failed", "error": job["error"],
                    "updated_at": job["updated_at"],
                })
        finally:
            with self._lock:
                self._queued.discard(str(job_path))

    def _transcribe_audio(self, path: Path, job: dict) -> dict:
        audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        audio_format = path.suffix.lower().lstrip(".")
        client = create_vlm(job["provider"], job["model"])
        text = client.chat([{
            "role": "user",
            "content": [
                {"type": "text", "text": "请准确转写这段音频中的中文内容。只输出转写文本；没有清晰人声则输出：无清晰语音。"},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": audio_format}},
            ],
        }], temperature=0.0)
        return {"transcript": text.strip()}

    def _describe_image(self, path: Path, job: dict) -> dict:
        image_bytes = path.read_bytes()
        client = create_vlm(job["provider"], job["model"])
        text = client.chat([{
            "role": "user",
            "content": [
                {"type": "text", "text": "用一句简洁中文描述这张图片中可见的实验场景或物体。只描述画面，不推测不可见内容。"},
                build_image_part_from_bytes(image_bytes, client),
            ],
        }], temperature=0.1)
        return {"description": text.strip()}

    def _set_analysis_item(self, media_dir: Path, filename: str, item: dict) -> None:
        path = media_dir / "analysis.json"
        payload = self._read_json(path) or {"items": {}}
        items = payload.setdefault("items", {})
        current = items.get(filename, {})
        current.update(item)
        items[filename] = current
        payload["updated_at"] = self._now()
        self._write_json(path, payload)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
