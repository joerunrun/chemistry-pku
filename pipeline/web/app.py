"""化学实验 Pipeline Web：检测/标注后立刻显示结果，VLM 分析异步进行。"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from threading import Lock, Semaphore
from typing import Any
from zoneinfo import ZoneInfo

import cv2
from dotenv import load_dotenv
from PIL import Image, ImageOps

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(Path(PROJECT_ROOT) / ".env")

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

from pipeline.common.cv_wrapper import (
    CVDetectionParams,
    build_reference_result,
    decode_image,
    list_available_references,
    load_reference_image,
)
from pipeline.common.timing import StepTimer, TimingRecord
from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, PROVIDER_MODELS
from pipeline.experiments import get_experiment_config, list_specs, resolve_experiment_spec
from pipeline.experiments.spec_loader import SPECS_DIR
from pipeline.media_analysis import MediaAnalysisQueue
from pipeline.web.pku_iaaa import client_ip, validate_token
from pipeline.web.identity_store import (
    calibrate_glass,
    find_user,
    glass_for_device,
    init_store,
    list_glasses,
    list_users,
    reset_user_role,
    set_user_role,
    upsert_user,
)
from pipeline.web.claim_store import (
    ClaimConflict,
    claim_item_owners,
    claimed_item_keys,
    create_claim,
    init_claim_store,
    list_claims,
)
from pipeline.web.teacher_binding_store import (
    TeacherBindingConflict,
    active_teacher_binding,
    end_teacher_binding,
    init_teacher_binding_store,
    list_teacher_bindings,
    set_teacher_binding,
    teacher_binding_for_glass_at,
)
from pipeline.resource_paths import runtime_asset_path
from pipeline.web.security import install_security

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
app.secret_key = os.getenv("CHEM_SESSION_SECRET") or os.urandom(32)
install_security(app, lambda: _current_user(), lambda: _admin_is_unlocked(),
                 runtime_asset_path("auth", "device_credentials.json"))
init_store()
init_claim_store()
init_teacher_binding_store()

# 内存存储：session_id → detection 结果
_detect_store: dict[str, dict] = {}
_TTL_SECONDS = 3600
_GLASSES_JOB_TTL_SECONDS = 30 * 60
_GLASSES_DEFAULT_PROVIDER = DEFAULT_VLM_PROVIDER
_GLASSES_DEFAULT_MODEL = DEFAULT_VLM_MODEL
_GLASSES_JOB_WORKERS = int(os.environ.get("CHEM_GLASSES_JOB_WORKERS", "4"))
_GLASSES_MAX_PENDING = int(os.environ.get("CHEM_GLASSES_MAX_PENDING_JOBS", "50"))
_DEMO_VIDEO_EXPERIMENTS = {
    spec["id"]: spec["name"]
    for spec in list_specs()
    if spec.get("supports_demo", False)
}
_ALLOWED_DEMO_VIDEO_SUFFIXES = {".mp4"}
_ALLOWED_RECORD_SUFFIXES = {".mp4", ".wav", ".m4a", ".aac", ".mp3"}
_AUDIO_RECORD_SUFFIXES = {".wav", ".m4a", ".aac", ".mp3"}
_ALLOWED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
_TEACHER_SPEAKER_THRESHOLD = 0.5
_TEACHER_SPEAKER_MIN_SPEECH_MS = 200
_TEACHER_DEFAULT_EMBEDDING_MODEL = runtime_asset_path(
    "speaker_models",
    "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx",
)
_teacher_speaker_lock = Lock()
_teacher_reference_audio: bytes | None = None
_teacher_reference_created_at: float | None = None
_glasses_executor = ThreadPoolExecutor(
    max_workers=_GLASSES_JOB_WORKERS,
    thread_name_prefix="glasses-job",
)
_glasses_jobs: dict[str, dict[str, Any]] = {}
_glasses_lock = Lock()
_vlm_semaphore = Semaphore(int(os.environ.get("CHEM_VLM_CONCURRENCY", "4")))
_omni_semaphore = Semaphore(int(os.environ.get("CHEM_OMNI_CONCURRENCY", "4")))
_media_analysis_queue = MediaAnalysisQueue()
_device_mapping_lock = Lock()
_common_issue_lock = Lock()
_common_issue_submitted: set[str] = set()
_daily_course_lock = Lock()
_daily_course_submitted: set[str] = set()
_workspace_report_lock = Lock()
_workspace_report_submitted: set[str] = set()

_GLASS_NUMBERS = tuple(f"{number:02d}" for number in range(1, 13))
_LEGACY_GLASS_LABELS = {
    "一号": "01",
    "二号": "02",
    "三号": "03",
    "四号": "04",
    "五号": "05",
    "六号": "06",
    "七号": "07",
    "八号": "08",
    "九号": "09",
    "十号": "10",
    "十一号": "11",
    "十二号": "12",
}

TEST_IDENTITIES = {
    "jia": {
        "identity_id": "demo-jia",
        "name": "测试甲",
        "initial_role": "student",
        "description": "测试学生甲",
    },
    "yi": {
        "identity_id": "demo-yi",
        "name": "测试乙",
        "initial_role": "student",
        "description": "已注册学生：直接进入学生工作台",
    },
    "bing": {
        "identity_id": "demo-bing",
        "name": "测试丙",
        "initial_role": "teacher",
        "description": "测试教师丙",
    },
    "ding": {
        "identity_id": "demo-ding",
        "name": "测试丁",
        "initial_role": "teacher",
        "description": "已注册教师：直接进入教师工作台",
    },
}


def _device_mapping_path() -> Path:
    return runtime_asset_path("device_mappings.json")


def _load_device_mappings() -> dict[str, str]:
    path = _device_mapping_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(device_id): str(label).strip()
        for device_id, label in data.items()
        if str(device_id).strip() and str(label).strip()
    }


def _save_device_mappings(mappings: dict[str, str]) -> None:
    path = _device_mapping_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(mappings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _normalize_glass_number(value: Any) -> str:
    text = str(value or "").strip()
    return _LEGACY_GLASS_LABELS.get(text, text if text in _GLASS_NUMBERS else "")


def _save_glass_calibration(glass_number: str, device_id: str | None) -> dict[str, Any]:
    """Keep the display mapping and claim calibration in sync."""
    glass_number = _normalize_glass_number(glass_number)
    if not glass_number:
        raise ValueError("眼镜编号必须是 01 到 12")
    clean_device_id = (device_id or "").strip() or None

    glass = calibrate_glass(glass_number, clean_device_id)
    with _device_mapping_lock:
        mappings = _load_device_mappings()
        for mapped_device_id, mapped_label in list(mappings.items()):
            if mapped_device_id == clean_device_id or _normalize_glass_number(mapped_label) == glass_number:
                mappings.pop(mapped_device_id, None)
        if clean_device_id:
            mappings[clean_device_id] = glass_number
        _save_device_mappings(mappings)
    return glass


def _clear_device_calibration(device_id: str) -> None:
    glass = next(
        (item for item in list_glasses() if str(item.get("device_id") or "") == device_id),
        None,
    )
    if glass:
        calibrate_glass(str(glass["glass_number"]), None)
    with _device_mapping_lock:
        mappings = _load_device_mappings()
        mappings.pop(device_id, None)
        _save_device_mappings(mappings)


def _teacher_device_inventory() -> list[dict[str, Any]]:
    with _device_mapping_lock:
        mappings = _load_device_mappings()
    calibrated = {
        str(item["device_id"]): str(item["glass_number"])
        for item in list_glasses()
        if item.get("device_id")
    }
    counts: dict[str, dict[str, int]] = {
        device_id: {"record_count": 0, "photo_count": 0}
        for device_id in set(mappings) | set(calibrated)
    }
    for root, metadata_name, count_key in (
        (_teacher_record_root(), "record.json", "record_count"),
        (_teacher_photo_root(), "photo.json", "photo_count"),
    ):
        if not root.exists():
            continue
        for metadata_path in root.rglob(metadata_name):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            device_id = str(metadata.get("device_id", "")).strip()
            if not device_id:
                continue
            counts.setdefault(device_id, {"record_count": 0, "photo_count": 0})
            counts[device_id][count_key] += 1
    return [
        {
            "device_id": device_id,
            "label": calibrated.get(device_id) or _normalize_glass_number(mappings.get(device_id, "")),
            "display_name": calibrated.get(device_id) or _normalize_glass_number(mappings.get(device_id, "")) or "未映射",
            **counts[device_id],
        }
        for device_id in sorted(counts)
    ]


def _common_issue_root() -> Path:
    return runtime_asset_path("derived", "common_issues")


def _common_issue_job_root() -> Path:
    return _common_issue_root() / "jobs"


def _common_issue_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_common_issue_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_common_issue_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _common_issue_report_path(report_id: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", report_id or ""):
        return None
    return _common_issue_root() / f"{report_id}.json"


def _daily_course_root() -> Path:
    return runtime_asset_path("derived", "daily_courses")


def _daily_course_job_root() -> Path:
    return _daily_course_root() / "jobs"


def _daily_course_report_path(report_id: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", report_id or ""):
        return None
    return _daily_course_root() / f"{report_id}.json"


def _daily_course_markdown_path(report_id: str) -> Path | None:
    path = _daily_course_report_path(report_id)
    return path.with_suffix(".md") if path else None


def _scoped_course_root() -> Path:
    return runtime_asset_path("derived", "scoped_courses")


def _workspace_report_job_root() -> Path:
    return _scoped_course_root() / "jobs"


def _workspace_report_path(report_id: str, kind: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", report_id or ""):
        return None
    root = _common_issue_root() if kind == "common_issues" else _scoped_course_root()
    return root / f"{report_id}.json"


def _find_workspace_report_path(report_id: str) -> Path | None:
    for kind in ("course", "common_issues"):
        path = _workspace_report_path(report_id, kind)
        if path and path.exists():
            return path
    legacy = _daily_course_report_path(report_id)
    return legacy if legacy and legacy.exists() else None


def _workspace_report_markdown_path(report_id: str, kind: str) -> Path | None:
    path = _workspace_report_path(report_id, kind)
    return path.with_suffix(".md") if path else None


def _write_workspace_report_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = str(report.get("kind", "course"))
    title = str(report.get("title") or ("共性问题与指导建议" if kind == "common_issues" else "课程内容整理"))
    filters = report.get("filters") if isinstance(report.get("filters"), dict) else {}
    lines = [
        f"# {title}",
        "",
        f"- 日期范围：{filters.get('start_date', report.get('date', ''))} 至 {filters.get('end_date', report.get('date', ''))}",
        f"- 实验：{filters.get('experiment_type') or '全部'}",
        f"- 眼镜编号：{filters.get('glass_number') or '全部'}",
        f"- 有效转写：{report.get('transcript_count', 0)} 条",
        f"- 模型：{report.get('model', '')}",
        f"- 生成时间：{report.get('generated_at', '')}",
        "",
    ]
    warnings = [str(item).strip() for item in report.get("warnings", []) if str(item).strip()]
    if warnings:
        lines.extend(["## 提示", "", *[f"- {item}" for item in warnings], ""])
    if kind == "common_issues":
        lines.extend(["## 共性问题", ""])
        issues = report.get("common_issues") if isinstance(report.get("common_issues"), list) else []
        if not issues:
            lines.append("暂无可归纳的共性问题。")
        for index, issue in enumerate(issues, 1):
            lines.extend([
                f"### {index}. {issue.get('problem', '未命名问题')}",
                "",
                str(issue.get("guidance", "")).strip() or "暂无建议。",
                "",
            ])
    else:
        highlights = report.get("highlights") if isinstance(report.get("highlights"), list) else []
        if highlights:
            lines.extend(["## 课程重点", "", *[f"- {item}" for item in highlights], ""])
        lines.extend(["## 课程内容", "", str(report.get("course_content", "")).strip() or "暂无课程内容。", ""])
    lines.extend(["## 来源记录", ""])
    for item in report.get("source_records", []):
        lines.append(
            f"- {item.get('record_id', '')}｜{item.get('created_at', '')}｜"
            f"眼镜 {item.get('glass_number', '') or '未提供'}｜"
            f"{item.get('experiment_type', '') or '未标记实验'}｜{item.get('status', '')}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_daily_course_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        f"# {report.get('title') or '当天课程内容'}",
        "",
        f"- 日期：{report.get('date', '')}",
        f"- 状态：{report.get('status', '')}",
        f"- 记录数：{report.get('transcript_count', 0)}/{report.get('record_count', 0)}",
        f"- 模型：{report.get('model', '')}",
        f"- 生成时间：{report.get('generated_at', '')}",
        "",
    ]
    warnings = [str(item).strip() for item in report.get("warnings", []) if str(item).strip()]
    if warnings:
        content.extend(["## 提示", "", *[f"- {item}" for item in warnings], ""])
    if report.get("highlights"):
        content.extend(["## 重点", "", *[f"- {item}" for item in report["highlights"]], ""])
    content.extend(["## 课程内容", "", str(report.get("course_content", "")).strip() or "暂无课程内容", ""])
    content.extend(["## 来源记录", ""])
    for item in report.get("source_records", []):
        content.append(
            f"- {item.get('record_id', '')}｜{item.get('created_at', '')}｜"
            f"{item.get('device_id', '') or '未提供'}｜{item.get('experiment_type', '') or '未提供'}｜"
            f"{item.get('status', '')}"
        )
    path.write_text("\n".join(content) + "\n", encoding="utf-8")


def _collect_teacher_transcripts(experiment_type: str, date_text: str) -> list[dict[str, str]]:
    root = _teacher_record_root() / date_text.replace("-", "")
    if not root.exists():
        return []
    transcripts: list[dict[str, str]] = []
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent
        metadata = _read_common_issue_json(metadata_path) or {}
        if str(metadata.get("experiment_type", "")).strip() != experiment_type:
            continue
        analysis = _load_analysis(record_dir)
        pieces: list[str] = []
        for item in (analysis.get("items", {}) or {}).values():
            if not isinstance(item, dict):
                continue
            transcript = str(item.get("transcript", "")).strip()
            if transcript:
                pieces.append(transcript)
        if not pieces:
            continue
        transcripts.append({
            "record_id": str(metadata.get("record_id") or record_dir.name),
            "created_at": str(metadata.get("created_at", "")),
            "device_id": str(metadata.get("device_id", "")),
            "transcript": "\n".join(pieces),
        })
    transcripts.sort(key=lambda item: item.get("created_at", ""))
    return transcripts


def _collect_teacher_daily_transcripts(date_text: str) -> dict[str, Any]:
    """Collect every teacher record for a date and retain missing/failed status."""
    root = _teacher_record_root() / date_text.replace("-", "")
    if not root.exists():
        return {"transcripts": [], "source_records": [], "warnings": []}

    transcripts: list[dict[str, str]] = []
    source_records: list[dict[str, Any]] = []
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent
        metadata = _read_common_issue_json(metadata_path) or {}
        record_id = str(metadata.get("record_id") or record_dir.name)
        analysis = _load_analysis(record_dir)
        items = analysis.get("items", {}) if isinstance(analysis, dict) else {}
        if not isinstance(items, dict):
            items = {}
        pieces: list[str] = []
        statuses: list[str] = []
        for item in items.values():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status:
                statuses.append(status)
            transcript = str(item.get("transcript", "")).strip()
            if transcript:
                pieces.append(transcript)

        if pieces:
            status = "succeeded"
            transcripts.append({
                "record_id": record_id,
                "created_at": str(metadata.get("created_at", "")),
                "device_id": str(metadata.get("device_id", "")),
                "experiment_type": str(metadata.get("experiment_type", "")),
                "transcript": "\n".join(pieces),
            })
        elif "running" in statuses or "queued" in statuses:
            status = "pending"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = "missing"
        source_records.append({
            "record_id": record_id,
            "created_at": str(metadata.get("created_at", "")),
            "device_id": str(metadata.get("device_id", "")),
            "experiment_type": str(metadata.get("experiment_type", "")),
            "status": status,
        })

    transcripts.sort(key=lambda item: item.get("created_at", ""))
    source_records.sort(key=lambda item: item.get("created_at", ""))
    incomplete = [item for item in source_records if item["status"] != "succeeded"]
    warnings = []
    if incomplete:
        warnings.append(f"有 {len(incomplete)} 条记录没有纳入课程转述，请确认转写状态。")
    return {
        "transcripts": transcripts,
        "source_records": source_records,
        "warnings": warnings,
    }


def _common_issue_experiment_context(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": cfg.get("id", ""),
        "name": cfg.get("name", ""),
        "experiment_requirements": cfg.get("experiment_requirements", ""),
        "components": cfg.get("components", []),
    }


def _schedule_common_issue_job(job_path: Path) -> None:
    key = str(job_path.resolve())
    with _common_issue_lock:
        if key in _common_issue_submitted:
            return
        _common_issue_submitted.add(key)
    _glasses_executor.submit(_run_common_issue_job, job_path)


def _recover_common_issue_jobs() -> None:
    root = _common_issue_job_root()
    root.mkdir(parents=True, exist_ok=True)
    for job_path in root.glob("*.json"):
        job = _read_common_issue_json(job_path)
        if not job or job.get("status") not in {"queued", "running"}:
            continue
        if job.get("status") == "running":
            job["status"] = "queued"
            job["updated_at"] = _common_issue_now()
            _write_common_issue_json(job_path, job)
        _schedule_common_issue_job(job_path)


def _run_common_issue_job(job_path: Path) -> None:
    from pipeline.derived_analysis import generate_common_issues

    job = _read_common_issue_json(job_path)
    if not job:
        return
    job["status"] = "running"
    job["updated_at"] = _common_issue_now()
    _write_common_issue_json(job_path, job)
    report_path = Path(str(job["report_path"]))
    running_report = _read_common_issue_json(report_path) or {
        "report_id": job["report_id"],
        "experiment_type": job["experiment_type"],
        "date": job["date"],
        "input_record_ids": job["input_record_ids"],
        "transcript_count": job["transcript_count"],
        "common_issues": [],
        "warnings": [],
    }
    running_report.update({"status": "running", "updated_at": job["updated_at"]})
    _write_common_issue_json(report_path, running_report)
    try:
        result = generate_common_issues(
            job["experiment"],
            job["transcripts"],
            provider=job.get("provider"),
            model=job.get("model"),
        )
        report = {
            "report_id": job["report_id"],
            "experiment_type": job["experiment_type"],
            "date": job["date"],
            "input_record_ids": job["input_record_ids"],
            "transcript_count": job["transcript_count"],
            "knowledge_source": f"{job['experiment_type']}.json",
            "provider": result["provider"],
            "model": result["model"],
            "common_issues": result["common_issues"],
            "warnings": ([] if result["common_issues"] else ["未发现至少出现在两条记录中的重复问题。"]),
            "status": "succeeded",
            "generated_at": _common_issue_now(),
        }
        _write_common_issue_json(report_path, report)
        job.update({"status": "succeeded", "updated_at": report["generated_at"]})
    except Exception as exc:
        error = str(exc)[:1000]
        report = {
            "report_id": job["report_id"],
            "experiment_type": job["experiment_type"],
            "date": job["date"],
            "input_record_ids": job["input_record_ids"],
            "transcript_count": job["transcript_count"],
            "knowledge_source": f"{job['experiment_type']}.json",
            "common_issues": [],
            "warnings": [error],
            "status": "failed",
            "generated_at": _common_issue_now(),
        }
        _write_common_issue_json(report_path, report)
        job.update({"status": "failed", "error": error, "updated_at": report["generated_at"]})
    finally:
        _write_common_issue_json(job_path, job)
        with _common_issue_lock:
            _common_issue_submitted.discard(str(job_path.resolve()))


def _schedule_daily_course_job(job_path: Path) -> None:
    key = str(job_path.resolve())
    with _daily_course_lock:
        if key in _daily_course_submitted:
            return
        _daily_course_submitted.add(key)
    _glasses_executor.submit(_run_daily_course_job, job_path)


def _recover_daily_course_jobs() -> None:
    root = _daily_course_job_root()
    root.mkdir(parents=True, exist_ok=True)
    for job_path in root.glob("*.json"):
        job = _read_common_issue_json(job_path)
        if not job or job.get("status") not in {"queued", "running"}:
            continue
        if job.get("status") == "running":
            job["status"] = "queued"
            job["updated_at"] = _common_issue_now()
            _write_common_issue_json(job_path, job)
        _schedule_daily_course_job(job_path)


def _run_daily_course_job(job_path: Path) -> None:
    from pipeline.derived_analysis import generate_daily_course

    job = _read_common_issue_json(job_path)
    if not job:
        return
    job["status"] = "running"
    job["updated_at"] = _common_issue_now()
    _write_common_issue_json(job_path, job)
    report_path = Path(str(job["report_path"]))
    report = _read_common_issue_json(report_path) or {}
    report.update({"status": "running", "updated_at": job["updated_at"]})
    _write_common_issue_json(report_path, report)
    try:
        result = generate_daily_course(
            job["date"],
            job["transcripts"],
            experiment_contexts=job.get("experiment_contexts", []),
            provider=job.get("provider"),
            model=job.get("model"),
        )
        report.update({
            "title": result["title"],
            "course_content": result["course_content"],
            "highlights": result["highlights"],
            "provider": result["provider"],
            "model": result["model"],
            "status": "succeeded",
            "generated_at": _common_issue_now(),
        })
        _write_common_issue_json(report_path, report)
        markdown_path = _daily_course_markdown_path(job["report_id"])
        if markdown_path:
            _write_daily_course_markdown(markdown_path, report)
        job.update({"status": "succeeded", "updated_at": report["generated_at"]})
    except Exception as exc:
        error = str(exc)[:1000]
        report.update({
            "course_content": "",
            "highlights": [],
            "warnings": [*report.get("warnings", []), error],
            "status": "failed",
            "generated_at": _common_issue_now(),
        })
        _write_common_issue_json(report_path, report)
        job.update({"status": "failed", "error": error, "updated_at": report["generated_at"]})
    finally:
        _write_common_issue_json(job_path, job)
        with _daily_course_lock:
            _daily_course_submitted.discard(str(job_path.resolve()))


def _schedule_workspace_report_job(job_path: Path) -> None:
    key = str(job_path.resolve())
    with _workspace_report_lock:
        if key in _workspace_report_submitted:
            return
        _workspace_report_submitted.add(key)
    _glasses_executor.submit(_run_workspace_report_job, job_path)


def _recover_workspace_report_jobs() -> None:
    root = _workspace_report_job_root()
    root.mkdir(parents=True, exist_ok=True)
    for job_path in root.glob("*.json"):
        job = _read_common_issue_json(job_path)
        if not job or job.get("status") not in {"queued", "running"}:
            continue
        if job.get("status") == "running":
            job["status"] = "queued"
            job["updated_at"] = _common_issue_now()
            _write_common_issue_json(job_path, job)
        _schedule_workspace_report_job(job_path)


def _run_workspace_report_job(job_path: Path) -> None:
    from pipeline.derived_analysis import generate_common_issues, generate_daily_course

    job = _read_common_issue_json(job_path)
    if not job:
        return
    report_path = Path(str(job["report_path"]))
    report = _read_common_issue_json(report_path) or {}
    job["status"] = "running"
    job["updated_at"] = _common_issue_now()
    report.update({"status": "running", "updated_at": job["updated_at"]})
    _write_common_issue_json(job_path, job)
    _write_common_issue_json(report_path, report)
    try:
        if job["kind"] == "common_issues":
            result = generate_common_issues(
                job["experiment"],
                job["transcripts"],
                provider=job.get("provider"),
                model=job.get("model"),
            )
            report.update({
                "title": "共性问题与指导建议",
                "common_issues": result["common_issues"],
                "provider": result["provider"],
                "model": result["model"],
                "warnings": ([] if result["common_issues"] else ["未发现至少出现在两条记录中的重复问题。"]),
            })
        else:
            result = generate_daily_course(
                f"{job['filters']['start_date']} 至 {job['filters']['end_date']}",
                job["transcripts"],
                experiment_contexts=job.get("experiment_contexts", []),
                provider=job.get("provider"),
                model=job.get("model"),
            )
            report.update({
                "title": result["title"],
                "course_content": result["course_content"],
                "highlights": result["highlights"],
                "provider": result["provider"],
                "model": result["model"],
            })
        report.update({"status": "succeeded", "generated_at": _common_issue_now()})
        _write_common_issue_json(report_path, report)
        markdown_path = _workspace_report_markdown_path(job["report_id"], job["kind"])
        if markdown_path:
            _write_workspace_report_markdown(markdown_path, report)
        job.update({"status": "succeeded", "updated_at": report["generated_at"]})
    except Exception as exc:
        error = str(exc)[:1000]
        report.update({
            "status": "failed",
            "warnings": [*report.get("warnings", []), error],
            "generated_at": _common_issue_now(),
        })
        _write_common_issue_json(report_path, report)
        job.update({"status": "failed", "error": error, "updated_at": report["generated_at"]})
    finally:
        _write_common_issue_json(job_path, job)
        with _workspace_report_lock:
            _workspace_report_submitted.discard(str(job_path.resolve()))


def _list_daily_course_reports(date_text: str = "") -> list[dict[str, Any]]:
    root = _daily_course_root()
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in root.glob("*.json"):
        report = _read_common_issue_json(path)
        if not report:
            continue
        if date_text and report.get("date") != date_text:
            continue
        reports.append(report)
    reports.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
    return reports


def _list_common_issue_reports(experiment_type: str = "", date_text: str = "") -> list[dict[str, Any]]:
    root = _common_issue_root()
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in root.glob("*.json"):
        report = _read_common_issue_json(path)
        if not report:
            continue
        if experiment_type and report.get("experiment_type") != experiment_type:
            continue
        if date_text and report.get("date") != date_text:
            continue
        reports.append(report)
    reports.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
    return reports


def _cleanup_store():
    now = time.time()
    expired = [k for k, v in _detect_store.items()
               if now - v.get("_created", 0) > _TTL_SECONDS]
    for k in expired:
        _detect_store.pop(k, None)


def _current_user() -> dict[str, Any] | None:
    identity_id = str(session.get("identity_id", "")).strip()
    if not identity_id:
        return None
    return find_user(identity_id)


def _admin_is_unlocked() -> bool:
    return bool(session.get("admin_unlocked"))


def _admin_required():
    if not _admin_is_unlocked():
        return jsonify({"error": "admin login required"}), 401
    return None


def _teacher_or_admin_required():
    if _admin_is_unlocked():
        return None
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先登录"}), 401
    if user.get("role") != "teacher":
        return jsonify({"error": "只有教师或管理员可以查看学生资料"}), 403
    return None


_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _parse_claim_time(value: str, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} 格式应为 YYYY-MM-DDTHH:MM") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _candidate_datetime(value: Any, *, fallback: datetime) -> datetime:
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return fallback
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _auto_claim_teacher_upload(metadata: dict[str, Any], item_type: str) -> dict[str, Any] | None:
    """Attach newly uploaded teacher media to the teacher using that glass."""
    if metadata.get("record_role") != "teacher" or item_type not in {"record", "photo"}:
        return None
    device_id = str(metadata.get("device_id", "")).strip()
    glass_number = glass_for_device(device_id) if device_id else None
    if not glass_number and device_id:
        glass_number = _normalize_glass_number(_load_device_mappings().get(device_id))
    if not glass_number:
        return None

    created_at = _candidate_datetime(
        metadata.get("created_at"), fallback=datetime.now(timezone.utc)
    )
    captured_at = _candidate_datetime(
        metadata.get("started_at") or metadata.get("captured_at"),
        fallback=created_at,
    )
    binding = teacher_binding_for_glass_at(glass_number, captured_at.isoformat())
    if binding is None:
        return None

    ended_at = _candidate_datetime(metadata.get("ended_at"), fallback=captured_at)
    if ended_at < captured_at:
        ended_at = captured_at
    item_id = str(metadata.get("record_id") or metadata.get("photo_id") or "").strip()
    if not item_id:
        return None
    try:
        return create_claim(
            identity_id=str(binding["identity_id"]),
            role="teacher",
            glass_number=glass_number,
            started_at=captured_at.isoformat(),
            ended_at=ended_at.isoformat(),
            items=[{
                "item_type": item_type,
                "item_id": item_id,
                "source_role": "teacher",
                "device_id": device_id,
                "experiment_type": str(metadata.get("experiment_type", "")),
                "item_created_at": created_at.isoformat(),
            }],
        )
    except ClaimConflict:
        return None
    except (KeyError, ValueError, sqlite3.Error) as exc:
        app.logger.warning("teacher upload auto-claim failed: %s", exc)
        return None


def _claim_candidate_items(
    glass_number: str,
    started_at: datetime,
    ended_at: datetime,
    *,
    source_role: str | None = None,
) -> list[dict[str, Any]]:
    glass = next((item for item in list_glasses() if item["glass_number"] == glass_number), None)
    if not glass or not glass.get("device_id"):
        return []
    device_id = str(glass["device_id"])
    candidates: list[dict[str, Any]] = []
    sources = (
        ("student", _list_records(_record_root(), "admin_download_record_file"), "record"),
        ("teacher", _list_records(_teacher_record_root(), "teacher_admin_download_record_file"), "record"),
        ("student", _list_photos(_photo_root(), "admin_photo_file"), "photo"),
        ("teacher", _list_photos(_teacher_photo_root(), "teacher_admin_photo_file"), "photo"),
    )
    for item_source_role, items, item_type in sources:
        if source_role and item_source_role != source_role:
            continue
        for item in items:
            if str(item.get("device_id", "")).strip() != device_id:
                continue
            created_at = _candidate_datetime(item.get("created_at"), fallback=datetime.min.replace(tzinfo=timezone.utc))
            if item_type == "record":
                item_start_raw = item.get("started_at") or item.get("created_at")
                item_end_raw = item.get("ended_at") or item_start_raw
                time_source = "record_interval" if item.get("started_at") and item.get("ended_at") else "created_at_fallback"
                item_id = str(item.get("record_id", ""))
            else:
                item_start_raw = item.get("captured_at") or item.get("created_at")
                item_end_raw = item_start_raw
                time_source = "captured_at" if item.get("captured_at") else "created_at_fallback"
                item_id = str(item.get("photo_id", ""))
            item_start = _candidate_datetime(item_start_raw, fallback=created_at)
            item_end = _candidate_datetime(item_end_raw, fallback=item_start)
            if item_end < item_start:
                item_end = item_start
            if item_end < started_at or item_start > ended_at:
                continue
            candidates.append({
                "item_type": item_type,
                "item_id": item_id,
                "source_role": item_source_role,
                "device_id": device_id,
                "experiment_type": str(item.get("experiment_type", "")),
                "item_created_at": str(item.get("created_at", "")),
                "started_at": item_start.isoformat(),
                "ended_at": item_end.isoformat(),
                "time_source": time_source,
            })
    candidates.sort(key=lambda item: item.get("started_at", ""))
    return candidates


def _backfill_unclaimed_teacher_media(identity_id: str, glass_number: str) -> dict[str, Any]:
    """Assign existing, unclaimed teacher media for a glass to its teacher."""
    candidates = _claim_candidate_items(
        glass_number,
        datetime(1970, 1, 1, tzinfo=timezone.utc),
        datetime.max.replace(tzinfo=timezone.utc),
        source_role="teacher",
    )
    claimed = claimed_item_keys()
    unclaimed = [
        item for item in candidates
        if (item["item_type"], "teacher", item["item_id"]) not in claimed
    ]
    claim_ids: list[str] = []
    item_count = 0
    for offset in range(0, len(unclaimed), 100):
        batch = unclaimed[offset:offset + 100]
        try:
            claim = create_claim(
                identity_id=identity_id,
                role="teacher",
                glass_number=glass_number,
                started_at=min(item["started_at"] for item in batch),
                ended_at=max(item["ended_at"] for item in batch),
                items=batch,
            )
        except ClaimConflict:
            current = claimed_item_keys()
            batch = [
                item for item in batch
                if (item["item_type"], "teacher", item["item_id"]) not in current
            ]
            if not batch:
                continue
            try:
                claim = create_claim(
                    identity_id=identity_id,
                    role="teacher",
                    glass_number=glass_number,
                    started_at=min(item["started_at"] for item in batch),
                    ended_at=max(item["ended_at"] for item in batch),
                    items=batch,
                )
            except ClaimConflict:
                continue
        claim_ids.append(str(claim["claim_id"]))
        item_count += len(claim["items"])
    return {"item_count": item_count, "claim_ids": claim_ids}


def _claim_period(value: datetime) -> tuple[str, str]:
    local = value.astimezone(_BEIJING_TZ)
    if local.hour < 6:
        period = "凌晨"
    elif local.hour < 12:
        period = "上午"
    elif local.hour < 18:
        period = "下午"
    else:
        period = "晚上"
    return local.strftime("%Y-%m-%d"), period


def _pending_claim_groups(source_role: str) -> tuple[list[dict[str, Any]], int]:
    device_glasses = {
        str(item["device_id"]): str(item["glass_number"])
        for item in list_glasses()
        if item.get("device_id")
    }
    claimed = claimed_item_keys()
    sources = (
        ("record", _list_records(_record_root(), "admin_download_record_file")),
        ("photo", _list_photos(_photo_root(), "admin_photo_file")),
    ) if source_role == "student" else (
        ("record", _list_records(_teacher_record_root(), "teacher_admin_download_record_file")),
        ("photo", _list_photos(_teacher_photo_root(), "teacher_admin_photo_file")),
    )

    pending_items: list[dict[str, Any]] = []
    unmapped_count = 0
    for item_type, items in sources:
        for source in items:
            item_id = str(source.get("record_id") or source.get("photo_id") or "")
            if not item_id or (item_type, source_role, item_id) in claimed:
                continue
            device_id = str(source.get("device_id", "")).strip()
            glass_number = device_glasses.get(device_id)
            if not glass_number:
                unmapped_count += 1
                continue
            created_at = _candidate_datetime(
                source.get("created_at"),
                fallback=datetime.min.replace(tzinfo=timezone.utc),
            )
            if item_type == "record":
                start_raw = source.get("started_at") or source.get("created_at")
                end_raw = source.get("ended_at") or start_raw
                time_source = "record_interval" if source.get("started_at") and source.get("ended_at") else "created_at_fallback"
            else:
                start_raw = source.get("captured_at") or source.get("created_at")
                end_raw = start_raw
                time_source = "captured_at" if source.get("captured_at") else "created_at_fallback"
            started_at = _candidate_datetime(start_raw, fallback=created_at)
            ended_at = _candidate_datetime(end_raw, fallback=started_at)
            if started_at.year < 2000:
                continue
            if ended_at < started_at:
                ended_at = started_at
            date_key, period = _claim_period(started_at)
            pending_items.append({
                "item_type": item_type,
                "item_id": item_id,
                "source_role": source_role,
                "device_id": device_id,
                "glass_number": glass_number,
                "experiment_type": str(source.get("experiment_type", "")).strip(),
                "item_created_at": str(source.get("created_at", "")),
                "started_at": started_at,
                "ended_at": ended_at,
                "time_source": time_source,
                "date_key": date_key,
                "period": period,
            })

    pending_items.sort(key=lambda item: item["started_at"])
    sessions: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in pending_items:
        key = (
            item["date_key"],
            item["period"],
            item["glass_number"],
            item["experiment_type"],
        )
        key_sessions = sessions.setdefault(key, [])
        if not key_sessions or item["started_at"] - key_sessions[-1]["ended_at"] > timedelta(minutes=60):
            key_sessions.append({
                "started_at": item["started_at"],
                "ended_at": item["ended_at"],
                "items": [],
            })
        session_item = key_sessions[-1]
        session_item["ended_at"] = max(session_item["ended_at"], item["ended_at"])
        session_item["items"].append(item)

    result: list[dict[str, Any]] = []
    for (date_key, period, glass_number, experiment_type), key_sessions in sessions.items():
        date_value = datetime.strptime(date_key, "%Y-%m-%d")
        for index, session_item in enumerate(key_sessions, start=1):
            started_at = session_item["started_at"].astimezone(_BEIJING_TZ)
            ended_at = session_item["ended_at"].astimezone(_BEIJING_TZ)
            if ended_at <= started_at:
                ended_at = started_at + timedelta(minutes=1)
            public_items = [{
                "item_type": item["item_type"],
                "item_id": item["item_id"],
                "experiment_type": item["experiment_type"],
                "item_created_at": item["item_created_at"],
                "started_at": item["started_at"].astimezone(_BEIJING_TZ).isoformat(),
                "ended_at": item["ended_at"].astimezone(_BEIJING_TZ).isoformat(),
                "time_source": item["time_source"],
            } for item in session_item["items"]]
            result.append({
                "group_id": f"{date_key}-{period}-{glass_number}-{index}",
                "date": date_key,
                "period": period,
                "date_label": f"{date_value.month}月{date_value.day}日{period}",
                "glass_number": glass_number,
                "experiment_type": experiment_type,
                "source_role": source_role,
                "started_at": started_at.strftime("%Y-%m-%dT%H:%M"),
                "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M"),
                "record_count": sum(item["item_type"] == "record" for item in session_item["items"]),
                "photo_count": sum(item["item_type"] == "photo" for item in session_item["items"]),
                "count": len(session_item["items"]),
                "items": public_items,
            })
    result.sort(key=lambda item: item["started_at"], reverse=True)
    return result, unmapped_count


@app.get("/")
def index():
    if request.args.get("token", "").strip():
        return pku_callback()
    return redirect(url_for("login_page"))


@app.get("/pipeline")
def pipeline_index():
    return render_template("index.html")


@app.get("/login")
def login_page():
    return render_template(
        "login.html",
        pku_app_id=os.getenv("PKU_IAAA_APP_ID", "hxb001").strip() or "hxb001",
        pku_redirect_url=os.getenv("PKU_IAAA_REDIRECT_URL", "").strip(),
    )


def _clear_user_session() -> None:
    for key in (
        "identity_id",
        "role",
        "pku_pending_identity_id",
        "pku_pending_identity_display",
    ):
        session.pop(key, None)


def _masked_identity_for_page(identity_id: str) -> str:
    text = str(identity_id)
    if text.startswith("demo-"):
        return text
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def _begin_user_session(user: dict[str, Any], identity_display: str) -> str:
    _clear_user_session()
    session.pop("admin_unlocked", None)
    role = str(user.get("role") or "teacher")
    session["identity_id"] = user["identity_id"]
    session["role"] = role if role in {"student", "teacher"} else "teacher"
    return url_for("workspace_index")


def _role_from_portal_profile(profile: dict[str, Any]) -> str:
    identity_type = str(profile.get("identityType", "")).strip()
    detail_type = str(profile.get("detailType", "")).strip()
    return "student" if "学生" in identity_type or "学生" in detail_type else "teacher"


def pku_callback():
    payload = request.get_json(silent=True) or {}
    token = (
        request.args.get("token", "")
        or request.form.get("token", "")
        or payload.get("token", "")
    )
    result = validate_token(
        str(token),
        client_ip(
            remote_addr=request.remote_addr,
            x_real_ip=None,  # remote_addr is normalized only by the trusted proxy middleware.
        ),
    )
    if not result.get("ok"):
        if request.is_json:
            return jsonify({"ok": False, "error": result.get("error", "北大认证失败")}), 400
        return render_template("pku_callback.html", **result)

    profile = result.get("user_info") if isinstance(result.get("user_info"), dict) else {}
    user = upsert_user(
        str(result["identity_id"]),
        profile=profile,
        portal_role=_role_from_portal_profile(profile),
    )
    next_url = _begin_user_session(user, str(result.get("identity_display") or "***"))
    if request.is_json:
        return jsonify({
            "ok": True,
            "next_url": next_url,
            "needs_role": False,
        })
    return redirect(next_url)


@app.route("/auth/pku/callback", methods=["GET", "POST"])
def pku_callback_route():
    return pku_callback()


@app.get("/onboarding")
def onboarding_page():
    if _current_user() is not None or _admin_is_unlocked():
        return redirect(url_for("workspace_index"))
    return redirect(url_for("login_page"))


@app.post("/auth/role")
def complete_role_registration():
    message = {"error": "角色由北大门户自动判断；如需调整，请联系管理员。"}
    return jsonify(message), 410


@app.get("/workspace")
def workspace_index():
    user = _current_user()
    if user is None and not _admin_is_unlocked():
        return redirect(url_for("login_page"))
    if user is not None and user.get("role") not in {"student", "teacher"}:
        profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
        user = upsert_user(user["identity_id"], portal_role=_role_from_portal_profile(profile))
        session["role"] = user["role"]
    return render_template(
        "workspace.html",
        user=user,
        glasses=list_glasses(),
        admin_unlocked=_admin_is_unlocked(),
    )


@app.get("/api/auth/me")
def auth_me():
    user = _current_user()
    return jsonify({
        "authenticated": user is not None,
        "user": user,
        "admin_unlocked": _admin_is_unlocked(),
    })


@app.get("/api/auth/profile")
def auth_profile():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成身份登录"}), 401
    return jsonify({
        "identity_id": user["identity_id"],
        "role": user.get("role"),
        "profile": user.get("profile"),
        "profile_updated_at": user.get("profile_updated_at"),
    })


@app.post("/auth/admin/login")
def admin_login():
    configured_password = os.getenv("CHEM_ADMIN_PASSWORD", "").strip()
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or request.form.get("password", ""))
    if not configured_password:
        return jsonify({"error": "服务端尚未配置 CHEM_ADMIN_PASSWORD"}), 503
    if password != configured_password:
        return jsonify({"error": "管理员密码不正确"}), 401
    _clear_user_session()
    session["admin_unlocked"] = True
    return redirect(url_for("workspace_index") + "#admin")


@app.post("/auth/logout")
def auth_logout():
    _clear_user_session()
    return redirect(url_for("login_page"))


@app.post("/auth/admin/logout")
def admin_logout():
    session.pop("admin_unlocked", None)
    return redirect(url_for("login_page"))


@app.get("/admin")
def admin_index():
    if not _admin_is_unlocked():
        return redirect(url_for("login_page") + "#admin")
    return redirect(url_for("workspace_index") + "#admin")


def _test_identity_user(key: str) -> dict[str, Any] | None:
    spec = TEST_IDENTITIES.get(key)
    if spec is None:
        return None
    existing = find_user(spec["identity_id"])
    profile = {
            "identityId": spec["identity_id"],
            "name": spec["name"],
            "identityType": "学生" if spec["initial_role"] == "student" else "职工",
            "source": "local-test",
            "testIdentity": True,
        }
    if existing is not None and existing.get("role_source") == "admin":
        return upsert_user(spec["identity_id"], profile=profile)
    return upsert_user(
        spec["identity_id"],
        role=spec["initial_role"],
        profile=profile,
        portal_role=spec["initial_role"],
        role_source="test",
    )


@app.get("/admin/users")
def admin_users_page():
    if not _admin_is_unlocked():
        return redirect(url_for("login_page") + "#admin")
    return redirect(url_for("workspace_index") + "#admin-identities")


@app.get("/admin/api/auth/users")
def admin_auth_users():
    required = _admin_required()
    if required:
        return required
    return jsonify({
        "users": list_users(),
        "teacher_bindings": list_teacher_bindings(active_only=True),
    })


@app.post("/admin/api/auth/users/<identity_id>/reset-role")
def admin_reset_user_role(identity_id: str):
    required = _admin_required()
    if required:
        return required
    user = reset_user_role(identity_id)
    if user is None:
        return jsonify({"error": "身份不存在"}), 404
    if session.get("identity_id") == identity_id:
        _clear_user_session()
    return jsonify({"user": user})


@app.put("/api/workspace/admin/users/<identity_id>/role")
def workspace_admin_set_user_role(identity_id: str):
    required = _admin_required()
    if required:
        return required
    payload = request.get_json(silent=True) or {}
    raw_role = payload.get("role")
    role = None if raw_role in {None, "", "portal"} else str(raw_role).strip()
    if role not in {None, "student", "teacher"}:
        return jsonify({"error": "role 必须是 student、teacher 或 portal"}), 400
    try:
        user = set_user_role(identity_id, role)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if user is None:
        return jsonify({"error": "身份不存在"}), 404
    if user.get("role") != "teacher":
        end_teacher_binding(identity_id)
    return jsonify({"user": user})


@app.put("/api/workspace/admin/users/<identity_id>/glass")
def workspace_admin_set_teacher_glass(identity_id: str):
    required = _admin_required()
    if required:
        return required
    user = find_user(identity_id)
    if user is None:
        return jsonify({"error": "身份不存在"}), 404
    payload = request.get_json(silent=True) or {}
    raw_glass_number = str(payload.get("glass_number") or "").strip()
    if not raw_glass_number:
        ended = end_teacher_binding(identity_id)
        return jsonify({"binding": None, "ended_binding": ended})
    glass_number = _normalize_glass_number(raw_glass_number)
    if not glass_number:
        return jsonify({"error": "眼镜编号必须是 01 到 12"}), 400
    if user.get("role") != "teacher":
        return jsonify({"error": "只有教师身份可以绑定眼镜"}), 400
    glass = next((item for item in list_glasses() if item["glass_number"] == glass_number), None)
    if not glass or not glass.get("device_id"):
        return jsonify({"error": "该眼镜尚未完成硬件校准"}), 400
    try:
        binding = set_teacher_binding(identity_id, glass_number)
    except TeacherBindingConflict:
        return jsonify({"error": "该眼镜已绑定给其他教师，请先解除原绑定"}), 409
    backfill = _backfill_unclaimed_teacher_media(identity_id, glass_number)
    return jsonify({"binding": binding, "backfill": backfill})


@app.get("/admin/test-identities")
def test_login_page():
    if not _admin_is_unlocked():
        return redirect(url_for("login_page") + "#admin")
    return redirect(url_for("workspace_index") + "#admin-tests")


@app.get("/api/workspace/admin/test-identities")
def workspace_admin_test_identities():
    required = _admin_required()
    if required:
        return required
    identities = []
    for key, spec in TEST_IDENTITIES.items():
        user = _test_identity_user(key)
        identities.append({"key": key, **spec, "current_role": user.get("role") if user else None})
    return jsonify({"identities": identities})


@app.post("/admin/test-identities/<key>/login")
def test_identity_login(key: str):
    required = _admin_required()
    if required:
        return required
    user = _test_identity_user(key)
    if user is None:
        return jsonify({"error": "未知测试身份"}), 404
    next_url = _begin_user_session(user, user["identity_id"])
    return redirect(next_url)


@app.post("/admin/test-identities/<key>/reset")
def reset_test_identity(key: str):
    required = _admin_required()
    if required:
        return required
    spec = TEST_IDENTITIES.get(key)
    if spec is None:
        return jsonify({"error": "未知测试身份"}), 404
    reset_user_role(spec["identity_id"])
    if session.get("identity_id") == spec["identity_id"]:
        _clear_user_session()
    return redirect(url_for("test_login_page"))


@app.get("/admin/api/auth/glasses")
def admin_auth_glasses():
    required = _admin_required()
    if required:
        return required
    return jsonify({"glasses": list_glasses()})


@app.put("/admin/api/auth/glasses/<glass_number>")
def admin_auth_glass_update(glass_number: str):
    required = _admin_required()
    if required:
        return required
    payload = request.get_json(silent=True) or {}
    try:
        glass = _save_glass_calibration(glass_number, payload.get("device_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"glass": glass})


@app.post("/api/claims/match")
def claims_match():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成北大认证"}), 401
    payload = request.get_json(silent=True) or {}
    glass_number = str(payload.get("glass_number", "")).strip()
    if glass_number not in _GLASS_NUMBERS:
        return jsonify({"error": "glass_number 必须是 01 到 12"}), 400
    try:
        started_at = _parse_claim_time(payload.get("started_at"), "开始时间")
        ended_at = _parse_claim_time(payload.get("ended_at"), "结束时间")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ended_at <= started_at:
        return jsonify({"error": "结束时间必须晚于开始时间"}), 400
    glass = next((item for item in list_glasses() if item["glass_number"] == glass_number), None)
    if not glass or not glass.get("device_id"):
        return jsonify({"error": f"眼镜 {glass_number} 还没有完成硬件校准"}), 400
    items = _claim_candidate_items(
        glass_number,
        started_at,
        ended_at,
        source_role=str(user.get("role") or ""),
    )
    return jsonify({
        "glass_number": glass_number,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "items": items,
        "count": len(items),
    })


@app.get("/api/claims/pending")
def claims_pending():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成北大认证"}), 401
    groups, unmapped_count = _pending_claim_groups(str(user.get("role") or ""))
    return jsonify({
        "groups": groups,
        "count": sum(group["count"] for group in groups),
        "unmapped_count": unmapped_count,
    })


def _calendar_month(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        return datetime.now(_BEIJING_TZ).strftime("%Y-%m")
    try:
        datetime.strptime(f"{text}-01", "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("month 格式应为 YYYY-MM") from exc
    return text


def _calendar_claim_entries(identity_id: str, month: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for claim in list_claims(identity_id):
        started_at = _candidate_datetime(
            claim.get("started_at"),
            fallback=datetime.min.replace(tzinfo=timezone.utc),
        ).astimezone(_BEIJING_TZ)
        if started_at.year < 2000 or started_at.strftime("%Y-%m") != month:
            continue
        ended_at = _candidate_datetime(
            claim.get("ended_at"),
            fallback=started_at.astimezone(timezone.utc),
        ).astimezone(_BEIJING_TZ)
        date_key, period = _claim_period(started_at.astimezone(timezone.utc))
        items = claim.get("items") if isinstance(claim.get("items"), list) else []
        entries.append({
            "entry_id": f"claim:{claim.get('claim_id', '')}",
            "status": "claimed",
            "date": date_key,
            "date_label": f"{started_at.month}月{started_at.day}日{period}",
            "period": period,
            "glass_number": str(claim.get("glass_number", "")),
            "experiment_type": next(
                (str(item.get("experiment_type", "")).strip() for item in items if item.get("experiment_type")),
                "",
            ),
            "source_role": str(claim.get("role", "")),
            "started_at": started_at.strftime("%Y-%m-%dT%H:%M"),
            "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M"),
            "count": len(items),
            "items": [{
                "item_type": str(item.get("item_type", "")),
                "experiment_type": str(item.get("experiment_type", "")),
                "item_created_at": str(item.get("item_created_at", "")),
            } for item in items if isinstance(item, dict)],
        })
    entries.sort(key=lambda item: item["started_at"])
    return entries


@app.get("/api/claims/calendar")
def claims_calendar():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成北大认证"}), 401
    try:
        month = _calendar_month(request.args.get("month", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    pending, unmapped_count = _pending_claim_groups(str(user.get("role") or ""))
    pending_entries = [
        {
            **group,
            "entry_id": f"pending:{group['group_id']}",
            "status": "pending",
        }
        for group in pending
        if group.get("date", "").startswith(f"{month}-")
    ]
    claimed_entries = _calendar_claim_entries(str(user["identity_id"]), month)
    entries = sorted(
        [*pending_entries, *claimed_entries],
        key=lambda item: (item.get("date", ""), item.get("started_at", "")),
    )
    return jsonify({
        "month": month,
        "entries": entries,
        "unmapped_count": unmapped_count,
    })


@app.post("/api/claims")
def claims_create():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成北大认证"}), 401
    payload = request.get_json(silent=True) or {}
    glass_number = str(payload.get("glass_number", "")).strip()
    try:
        started_at = _parse_claim_time(payload.get("started_at"), "开始时间")
        ended_at = _parse_claim_time(payload.get("ended_at"), "结束时间")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ended_at <= started_at:
        return jsonify({"error": "结束时间必须晚于开始时间"}), 400
    candidates = _claim_candidate_items(
        glass_number,
        started_at,
        ended_at,
        source_role=str(user.get("role") or ""),
    )
    candidate_map = {
        (item["item_type"], item["source_role"], item["item_id"]): item
        for item in candidates
    }
    requested_items = payload.get("items")
    if not isinstance(requested_items, list) or not requested_items:
        requested_items = candidates
    selected = []
    selected_keys = set()
    for requested in requested_items:
        if not isinstance(requested, dict):
            continue
        key = (
            str(requested.get("item_type", "")).strip(),
            str(requested.get("source_role", "")).strip(),
            str(requested.get("item_id", "")).strip(),
        )
        if key in candidate_map and key not in selected_keys:
            selected.append(candidate_map[key])
            selected_keys.add(key)
    if not selected:
        return jsonify({"error": "没有选择任何匹配数据"}), 400
    try:
        claim = create_claim(
            identity_id=str(user["identity_id"]),
            role=str(user["role"]),
            glass_number=glass_number,
            started_at=started_at.isoformat(),
            ended_at=ended_at.isoformat(),
            items=selected,
        )
    except ClaimConflict as exc:
        return jsonify({"error": "其中一些数据已经被认领", "items": exc.items}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"claim": claim}), 201


@app.get("/api/claims")
def claims_list():
    user = _current_user()
    if user is None and not _admin_is_unlocked():
        return jsonify({"error": "请先登录"}), 401
    if user and user.get("role") == "teacher":
        identity_id = str(user["identity_id"])
        claims = [
            claim for claim in list_claims()
            if claim.get("role") == "student" or claim.get("identity_id") == identity_id
        ]
    elif user:
        claims = list_claims(str(user["identity_id"]))
    else:
        claims = list_claims()
    return jsonify({"claims": claims})


def _workspace_data_catalog() -> list[dict[str, Any]]:
    sources = (
        ("student", "record", _list_records(_record_root(), "admin_download_record_file")),
        ("teacher", "record", _list_records(_teacher_record_root(), "teacher_admin_download_record_file")),
        ("student", "photo", _list_photos(_photo_root(), "admin_photo_file")),
        ("teacher", "photo", _list_photos(_teacher_photo_root(), "teacher_admin_photo_file")),
    )
    owners = claim_item_owners()
    catalog: list[dict[str, Any]] = []
    for source_role, item_type, items in sources:
        for source in items:
            item = dict(source)
            item.pop("path", None)
            item_id = str(item.get("record_id") or item.get("photo_id") or "")
            owner = owners.get((item_type, source_role, item_id))
            if owner:
                owner = dict(owner)
                owner["identity_display"] = _masked_identity_for_page(owner["identity_id"])
            item.update({
                "item_id": item_id,
                "item_type": item_type,
                "source_role": source_role,
                "claim": owner,
            })
            catalog.append(item)
    catalog.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return catalog


@app.get("/api/workspace/data")
def workspace_data():
    user = _current_user()
    if user is None:
        return jsonify({"error": "请先完成身份登录"}), 401
    catalog = _workspace_data_catalog()
    identity_id = str(user["identity_id"])
    own_items = [
        item for item in catalog
        if item.get("claim") and item["claim"].get("identity_id") == identity_id
    ]
    payload: dict[str, Any] = {"own_items": own_items}
    if user.get("role") == "teacher":
        payload["student_items"] = [
            item for item in catalog if item.get("source_role") == "student"
        ]
    return jsonify(payload)


def _teacher_user_required() -> tuple[dict[str, Any] | None, Any | None]:
    user = _current_user()
    if user is None:
        return None, (jsonify({"error": "请先完成身份登录"}), 401)
    if user.get("role") != "teacher":
        return None, (jsonify({"error": "只有教师可以使用该功能"}), 403)
    return user, None


def _teacher_binding_payload(identity_id: str) -> dict[str, Any]:
    binding = active_teacher_binding(identity_id=identity_id)
    occupied = {
        str(item["glass_number"]): str(item["identity_id"])
        for item in list_teacher_bindings(active_only=True)
    }
    glasses = []
    for glass in list_glasses():
        number = str(glass["glass_number"])
        owner = occupied.get(number)
        glasses.append({
            "glass_number": number,
            "calibrated": bool(glass.get("device_id")),
            "available": owner in {None, identity_id},
            "current": bool(binding and binding.get("glass_number") == number),
        })
    history = [
        item for item in list_teacher_bindings()
        if item.get("identity_id") == identity_id
    ]
    return {"binding": binding, "glasses": glasses, "history": history}


@app.get("/api/workspace/teacher/binding")
def workspace_teacher_binding():
    user, error = _teacher_user_required()
    if error:
        return error
    identity_id = str(user["identity_id"])
    binding = active_teacher_binding(identity_id=identity_id)
    if binding:
        _backfill_unclaimed_teacher_media(identity_id, str(binding["glass_number"]))
    return jsonify(_teacher_binding_payload(identity_id))


@app.put("/api/workspace/teacher/binding")
def workspace_teacher_set_binding():
    user, error = _teacher_user_required()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    raw_glass_number = str(payload.get("glass_number") or "").strip()
    glass_number = _normalize_glass_number(raw_glass_number)
    if not glass_number:
        return jsonify({"error": "请选择 01 到 12 号眼镜"}), 400
    glass = next((item for item in list_glasses() if item["glass_number"] == glass_number), None)
    if not glass or not glass.get("device_id"):
        return jsonify({"error": "该眼镜尚未完成硬件校准"}), 400
    try:
        identity_id = str(user["identity_id"])
        set_teacher_binding(identity_id, glass_number)
    except TeacherBindingConflict:
        return jsonify({"error": "该眼镜已绑定给其他教师，请选择其他眼镜"}), 409
    backfill = _backfill_unclaimed_teacher_media(identity_id, glass_number)
    payload = _teacher_binding_payload(identity_id)
    payload["backfill"] = backfill
    return jsonify(payload)


@app.delete("/api/workspace/teacher/binding")
def workspace_teacher_end_binding():
    user, error = _teacher_user_required()
    if error:
        return error
    end_teacher_binding(str(user["identity_id"]))
    return jsonify(_teacher_binding_payload(str(user["identity_id"])))


def _parse_report_range(start_value: Any, end_value: Any) -> tuple[str, str, datetime, datetime]:
    start_text = str(start_value or "").strip()
    end_text = str(end_value or "").strip()
    try:
        start_day = datetime.strptime(start_text, "%Y-%m-%d").replace(tzinfo=_BEIJING_TZ)
        end_day = datetime.strptime(end_text, "%Y-%m-%d").replace(tzinfo=_BEIJING_TZ)
    except ValueError as exc:
        raise ValueError("开始日期和结束日期必须是 YYYY-MM-DD") from exc
    if end_day < start_day:
        raise ValueError("结束日期不能早于开始日期")
    if (end_day.date() - start_day.date()).days >= 31:
        raise ValueError("一份报告最多覆盖 31 天")
    range_end = end_day + timedelta(days=1) - timedelta(microseconds=1)
    return start_text, end_text, start_day.astimezone(timezone.utc), range_end.astimezone(timezone.utc)


def _analysis_transcript(analysis: dict[str, Any]) -> tuple[str, str]:
    items = analysis.get("items") if isinstance(analysis, dict) else {}
    if not isinstance(items, dict):
        items = {}
    pieces: list[str] = []
    statuses: list[str] = []
    for item in items.values():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip()
        if status:
            statuses.append(status)
        transcript = str(item.get("transcript", "")).strip()
        if transcript:
            pieces.append(transcript)
    if pieces:
        return "\n".join(pieces), "succeeded"
    if "running" in statuses or "queued" in statuses:
        return "", "pending"
    if "failed" in statuses:
        return "", "failed"
    return "", "missing"


def _teacher_report_sources(
    identity_id: str,
    start_value: Any,
    end_value: Any,
    *,
    experiment_type: str = "",
    glass_number: str = "",
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    start_text, end_text, range_start, range_end = _parse_report_range(start_value, end_value)
    experiment_type = str(experiment_type or "").strip()
    glass_number = str(glass_number or "").strip()
    if glass_number and glass_number not in _GLASS_NUMBERS:
        raise ValueError("眼镜编号必须是 01 到 12")

    claimed: dict[str, dict[str, Any]] = {}
    for claim in list_claims(identity_id):
        if claim.get("role") != "teacher":
            continue
        for item in claim.get("items", []):
            if item.get("item_type") != "record" or item.get("source_role") != "teacher":
                continue
            claimed[str(item.get("item_id", ""))] = {
                "claim_id": str(claim.get("claim_id", "")),
                "glass_number": str(claim.get("glass_number", "")),
            }

    sources: list[dict[str, Any]] = []
    for record in _list_records(_teacher_record_root(), "teacher_admin_download_record_file"):
        record_id = str(record.get("record_id", ""))
        claim = claimed.get(record_id)
        if claim is None:
            continue
        if not any(Path(str(item.get("filename", ""))).suffix.lower() in _AUDIO_RECORD_SUFFIXES
                   for item in record.get("files", [])):
            continue
        record_experiment = str(record.get("experiment_type", "")).strip()
        if experiment_type and record_experiment != experiment_type:
            continue
        if glass_number and claim["glass_number"] != glass_number:
            continue
        created = _candidate_datetime(
            record.get("started_at") or record.get("created_at"),
            fallback=datetime.min.replace(tzinfo=timezone.utc),
        )
        if created < range_start or created > range_end:
            continue
        transcript, status = _analysis_transcript(record.get("analysis") or {})
        sources.append({
            "record_id": record_id,
            "claim_id": claim["claim_id"],
            "glass_number": claim["glass_number"],
            "device_id": str(record.get("device_id", "")),
            "experiment_type": record_experiment,
            "created_at": str(record.get("created_at", "")),
            "started_at": str(record.get("started_at", "")),
            "ended_at": str(record.get("ended_at", "")),
            "status": status,
            "eligible": status == "succeeded",
            "transcript": transcript,
        })
    sources.sort(key=lambda item: item.get("started_at") or item.get("created_at") or "")
    return {
        "start_date": start_text,
        "end_date": end_text,
        "experiment_type": experiment_type,
        "glass_number": glass_number,
    }, sources


@app.get("/api/workspace/teacher/report-sources")
def workspace_teacher_report_sources():
    user, error = _teacher_user_required()
    if error:
        return error
    try:
        filters, sources = _teacher_report_sources(
            str(user["identity_id"]),
            request.args.get("start_date"),
            request.args.get("end_date"),
            experiment_type=request.args.get("experiment_type", ""),
            glass_number=request.args.get("glass_number", ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    public_sources = [{key: value for key, value in item.items() if key != "transcript"} for item in sources]
    return jsonify({"filters": filters, "sources": public_sources})


@app.post("/api/workspace/teacher/reports")
def workspace_teacher_create_report():
    user, error = _teacher_user_required()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind", "course")).strip()
    if kind not in {"course", "common_issues"}:
        return jsonify({"error": "kind 必须是 course 或 common_issues"}), 400
    experiment_type = str(payload.get("experiment_type", "")).strip()
    if kind == "common_issues" and not experiment_type:
        return jsonify({"error": "生成共性问题时必须选择实验"}), 400
    cfg = get_experiment_config(experiment_type) if experiment_type else None
    if experiment_type and cfg is None:
        return jsonify({"error": "未知实验类型"}), 400
    try:
        filters, sources = _teacher_report_sources(
            str(user["identity_id"]),
            payload.get("start_date"),
            payload.get("end_date"),
            experiment_type=experiment_type,
            glass_number=payload.get("glass_number", ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    requested_ids = payload.get("record_ids")
    if requested_ids is not None and not isinstance(requested_ids, list):
        return jsonify({"error": "record_ids 必须是数组"}), 400
    source_map = {item["record_id"]: item for item in sources}
    if requested_ids:
        normalized_ids = list(dict.fromkeys(str(item).strip() for item in requested_ids if str(item).strip()))
        unknown = [record_id for record_id in normalized_ids if record_id not in source_map]
        if unknown:
            return jsonify({"error": "包含不属于当前教师或筛选范围的记录", "record_ids": unknown}), 403
        selected = [source_map[record_id] for record_id in normalized_ids]
    else:
        selected = sources
    unavailable = [item["record_id"] for item in selected if not item["eligible"]]
    if unavailable:
        return jsonify({"error": "所选记录中有尚未完成转写的内容", "record_ids": unavailable}), 400
    selected = [item for item in selected if item["eligible"]]

    minimum = 2 if kind == "common_issues" else 1
    now = _common_issue_now()
    prefix = "issues" if kind == "common_issues" else "course"
    report_id = f"range_{filters['start_date'].replace('-', '')}_{filters['end_date'].replace('-', '')}_{prefix}_{uuid.uuid4().hex[:8]}"
    report_path = _workspace_report_path(report_id, kind)
    if report_path is None:
        return jsonify({"error": "无法创建报告 ID"}), 500
    public_sources = [{key: value for key, value in item.items() if key != "transcript"} for item in selected]
    report = {
        "report_id": report_id,
        "kind": kind,
        "owner_identity_id": str(user["identity_id"]),
        "filters": {**filters, "record_ids": [item["record_id"] for item in selected]},
        "input_record_ids": [item["record_id"] for item in selected],
        "record_count": len(selected),
        "transcript_count": len(selected),
        "source_records": public_sources,
        "title": "共性问题与指导建议" if kind == "common_issues" else "课程内容整理",
        "course_content": "",
        "highlights": [],
        "common_issues": [],
        "warnings": [],
        "status": "queued",
        "generated_at": now,
        "updated_at": now,
    }
    if len(selected) < minimum:
        report["status"] = "insufficient_data"
        report["warnings"] = [
            "共性问题至少需要两条已完成转写的记录。"
            if kind == "common_issues" else "所选范围没有可用于整理的已完成转写。"
        ]
        _write_common_issue_json(report_path, report)
        markdown_path = _workspace_report_markdown_path(report_id, kind)
        if markdown_path:
            _write_workspace_report_markdown(markdown_path, report)
        return jsonify({"report": report}), 200

    transcripts = [{
        "record_id": item["record_id"],
        "created_at": item["created_at"],
        "device_id": item["device_id"],
        "experiment_type": item["experiment_type"],
        "transcript": item["transcript"],
    } for item in selected]
    provider = os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER).strip() or DEFAULT_VLM_PROVIDER
    model = os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL).strip() or DEFAULT_VLM_MODEL
    experiment_contexts = []
    seen_experiments: set[str] = set()
    for item in transcripts:
        item_experiment = item["experiment_type"]
        if not item_experiment or item_experiment in seen_experiments:
            continue
        seen_experiments.add(item_experiment)
        item_cfg = get_experiment_config(item_experiment) or {}
        experiment_contexts.append({"id": item_experiment, "name": item_cfg.get("name", item_experiment)})
    job = {
        "job_id": report_id,
        "report_id": report_id,
        "report_path": str(report_path.resolve()),
        "kind": kind,
        "filters": filters,
        "transcripts": transcripts,
        "experiment": _common_issue_experiment_context(cfg or {}),
        "experiment_contexts": experiment_contexts,
        "provider": provider,
        "model": model,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    job_path = _workspace_report_job_root() / f"{report_id}.json"
    _write_common_issue_json(job_path, job)
    _write_common_issue_json(report_path, report)
    _schedule_workspace_report_job(job_path)
    return jsonify({"report": report}), 202


def _workspace_report_visible(report: dict[str, Any], identity_id: str) -> bool:
    owner = str(report.get("owner_identity_id", "")).strip()
    return owner == identity_id or (not owner and bool(report.get("date")))


@app.get("/api/workspace/teacher/reports")
def workspace_teacher_reports():
    user, error = _teacher_user_required()
    if error:
        return error
    identity_id = str(user["identity_id"])
    reports: list[dict[str, Any]] = []
    for root in (_scoped_course_root(), _common_issue_root()):
        root.mkdir(parents=True, exist_ok=True)
        for path in root.glob("*.json"):
            report = _read_common_issue_json(path)
            if report and str(report.get("owner_identity_id", "")) == identity_id:
                reports.append(report)
    for legacy in _list_daily_course_reports():
        reports.append({**legacy, "kind": "course", "legacy": True})
    reports.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
    return jsonify({"reports": reports})


@app.get("/api/workspace/teacher/reports/<report_id>")
def workspace_teacher_report(report_id: str):
    user, error = _teacher_user_required()
    if error:
        return error
    path = _find_workspace_report_path(report_id)
    report = _read_common_issue_json(path) if path else None
    if not report or not _workspace_report_visible(report, str(user["identity_id"])):
        return jsonify({"error": "报告不存在"}), 404
    return jsonify({"report": report})


@app.get("/api/workspace/teacher/reports/<report_id>/download")
def workspace_teacher_report_download(report_id: str):
    user, error = _teacher_user_required()
    if error:
        return error
    path = _find_workspace_report_path(report_id)
    report = _read_common_issue_json(path) if path else None
    if not report or not _workspace_report_visible(report, str(user["identity_id"])):
        return jsonify({"error": "报告不存在"}), 404
    if report.get("legacy") or (not report.get("owner_identity_id") and report.get("date")):
        markdown_path = _daily_course_markdown_path(report_id)
    else:
        markdown_path = _workspace_report_markdown_path(report_id, str(report.get("kind", "course")))
    if markdown_path is None or not markdown_path.exists():
        return jsonify({"error": "报告文件不存在"}), 404
    return send_file(markdown_path, as_attachment=True, download_name=markdown_path.name)


@app.get("/teacher-admin")
def teacher_admin_index():
    if _current_user() is None and not _admin_is_unlocked():
        return redirect(url_for("login_page"))
    return redirect(url_for("workspace_index") + "#teacher-tools")


@app.get("/teacher-admin/api/devices")
def teacher_admin_devices():
    return jsonify({"devices": _teacher_device_inventory()})


@app.put("/teacher-admin/api/devices/<device_id>")
def teacher_admin_update_device(device_id: str):
    required = _admin_required()
    if required:
        return required
    device_id = _safe_text_field(device_id, "")
    if not device_id:
        return jsonify({"error": "invalid device_id"}), 400
    payload = request.get_json(silent=True) or {}
    raw_label = str(payload.get("label", "")).strip()
    label = _normalize_glass_number(raw_label)
    if raw_label and not label:
        return jsonify({"error": "眼镜编号必须是 01 到 12"}), 400
    if label:
        _save_glass_calibration(label, device_id)
    else:
        _clear_device_calibration(device_id)
    mappings = _load_device_mappings()
    device = next(
        (item for item in _teacher_device_inventory() if item["device_id"] == device_id),
        {
            "device_id": device_id,
            "label": mappings.get(device_id, ""),
            "display_name": mappings.get(device_id, "未映射"),
            "record_count": 0,
            "photo_count": 0,
        },
    )
    return jsonify({"device": device})


@app.post("/teacher-admin/api/derived/common-issues")
def teacher_admin_create_common_issues():
    payload = request.get_json(silent=True) or {}
    experiment_type = str(payload.get("experiment_type", "")).strip()
    date_text = str(payload.get("date", "")).strip()
    cfg = get_experiment_config(experiment_type)
    if cfg is None:
        return jsonify({"error": "未知实验类型"}), 400
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "date 必须是 YYYY-MM-DD"}), 400

    transcripts = _collect_teacher_transcripts(experiment_type, date_text)
    experiment_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", experiment_type)[:40] or "experiment"
    report_id = f"{date_text.replace('-', '')}_{experiment_slug}_{uuid.uuid4().hex[:8]}"
    report_path = _common_issue_report_path(report_id)
    if report_path is None:
        return jsonify({"error": "无法创建报告 ID"}), 500
    now = _common_issue_now()
    base_report = {
        "report_id": report_id,
        "experiment_type": experiment_type,
        "date": date_text,
        "input_record_ids": [item["record_id"] for item in transcripts],
        "transcript_count": len(transcripts),
        "knowledge_source": f"{experiment_type}.json",
        "common_issues": [],
        "warnings": [],
        "status": "queued",
        "generated_at": now,
        "updated_at": now,
    }
    if len(transcripts) < 2:
        base_report["status"] = "insufficient_data"
        base_report["warnings"] = ["至少需要两条含转写的不同记录，才能提炼共性问题。"]
        _write_common_issue_json(report_path, base_report)
        return jsonify({"report": base_report}), 200

    provider = os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER).strip() or DEFAULT_VLM_PROVIDER
    model = os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL).strip() or DEFAULT_VLM_MODEL
    job = {
        "job_id": report_id,
        "report_id": report_id,
        "report_path": str(report_path.resolve()),
        "experiment_type": experiment_type,
        "date": date_text,
        "experiment": _common_issue_experiment_context(cfg),
        "transcripts": transcripts,
        "input_record_ids": base_report["input_record_ids"],
        "transcript_count": len(transcripts),
        "provider": provider,
        "model": model,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    _write_common_issue_json(_common_issue_job_root() / f"{report_id}.json", job)
    _write_common_issue_json(report_path, base_report)
    _schedule_common_issue_job(_common_issue_job_root() / f"{report_id}.json")
    return jsonify({"report": base_report}), 202


@app.get("/teacher-admin/api/derived/common-issues")
def teacher_admin_common_issue_reports():
    return jsonify({
        "reports": _list_common_issue_reports(
            request.args.get("experiment_type", "").strip(),
            request.args.get("date", "").strip(),
        ),
    })


@app.get("/teacher-admin/api/derived/common-issues/<report_id>")
def teacher_admin_common_issue_report(report_id: str):
    path = _common_issue_report_path(report_id)
    report = _read_common_issue_json(path) if path else None
    if not report:
        return jsonify({"error": "报告不存在"}), 404
    return jsonify({"report": report})


@app.post("/teacher-admin/api/derived/daily-course")
def teacher_admin_create_daily_course():
    payload = request.get_json(silent=True) or {}
    date_text = str(payload.get("date", "")).strip()
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "date 必须是 YYYY-MM-DD"}), 400

    collected = _collect_teacher_daily_transcripts(date_text)
    report_id = f"{date_text.replace('-', '')}_daily_course_{uuid.uuid4().hex[:8]}"
    report_path = _daily_course_report_path(report_id)
    if report_path is None:
        return jsonify({"error": "无法创建报告 ID"}), 500
    now = _common_issue_now()
    transcripts = collected["transcripts"]
    source_records = collected["source_records"]
    base_report = {
        "report_id": report_id,
        "date": date_text,
        "input_record_ids": [item["record_id"] for item in transcripts],
        "record_count": len(source_records),
        "transcript_count": len(transcripts),
        "source_records": source_records,
        "title": "当天课程内容",
        "course_content": "",
        "highlights": [],
        "warnings": collected["warnings"],
        "status": "queued",
        "generated_at": now,
        "updated_at": now,
    }
    if not transcripts:
        base_report["status"] = "insufficient_data"
        if not base_report["warnings"]:
            base_report["warnings"] = ["当天没有可用于整理的已完成转写。"]
        _write_common_issue_json(report_path, base_report)
        _write_daily_course_markdown(_daily_course_markdown_path(report_id), base_report)
        return jsonify({"report": base_report}), 200

    experiment_contexts = []
    seen_experiments: set[str] = set()
    for item in transcripts:
        experiment_type = str(item.get("experiment_type", "")).strip()
        if not experiment_type or experiment_type in seen_experiments:
            continue
        seen_experiments.add(experiment_type)
        cfg = get_experiment_config(experiment_type)
        experiment_contexts.append({
            "id": experiment_type,
            "name": str((cfg or {}).get("name", experiment_type)),
        })

    provider = os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER).strip() or DEFAULT_VLM_PROVIDER
    model = os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL).strip() or DEFAULT_VLM_MODEL
    job = {
        "job_id": report_id,
        "report_id": report_id,
        "report_path": str(report_path.resolve()),
        "date": date_text,
        "transcripts": transcripts,
        "experiment_contexts": experiment_contexts,
        "input_record_ids": base_report["input_record_ids"],
        "record_count": base_report["record_count"],
        "transcript_count": base_report["transcript_count"],
        "provider": provider,
        "model": model,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    _write_common_issue_json(_daily_course_job_root() / f"{report_id}.json", job)
    _write_common_issue_json(report_path, base_report)
    _schedule_daily_course_job(_daily_course_job_root() / f"{report_id}.json")
    return jsonify({"report": base_report}), 202


@app.get("/teacher-admin/api/derived/daily-course")
def teacher_admin_daily_course_reports():
    date_text = request.args.get("date", "").strip()
    if date_text:
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "date 必须是 YYYY-MM-DD"}), 400
    return jsonify({"reports": _list_daily_course_reports(date_text)})


@app.get("/teacher-admin/api/derived/daily-course/<report_id>")
def teacher_admin_daily_course_report(report_id: str):
    path = _daily_course_report_path(report_id)
    report = _read_common_issue_json(path) if path else None
    if not report:
        return jsonify({"error": "报告不存在"}), 404
    return jsonify({"report": report})


@app.get("/teacher-admin/api/derived/daily-course/<report_id>/download")
def teacher_admin_daily_course_download(report_id: str):
    path = _daily_course_markdown_path(report_id)
    if not path or not path.exists():
        return jsonify({"error": "课程报告尚未生成"}), 404
    return send_file(path, as_attachment=True, download_name=f"{report_id}.md", mimetype="text/markdown; charset=utf-8")


@app.before_request
def start_media_analysis_queue():
    _media_analysis_queue.start()
    _recover_common_issue_jobs()
    _recover_daily_course_jobs()
    _recover_workspace_report_jobs()


@app.get("/experiment-lab")
def experiment_lab():
    return render_template("experiment_lab.html")


@app.get("/api/experiment-lab/specs")
def experiment_lab_specs():
    specs = [
        spec
        for spec in list_specs()
        if spec.get("expose_in_experiment_lab", True)
    ]
    return jsonify({"experiments": [
        {
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "strategy": s.get("strategy", "vlm_only"),
            "has_components": bool(s.get("components")),
        }
        for s in specs
    ]})


@app.post("/api/experiment-lab/run")
def experiment_lab_run():
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传图片。"}), 400
        experiment_id = request.form.get("experiment_id", "").strip()
        if resolve_experiment_spec(experiment_id) is None:
            return jsonify({"error": f"未知实验: {experiment_id}"}), 400
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or None
        image_bytes = uploaded.read()

        from pipeline.experiments.runner import run_experiment_check
        result = run_experiment_check(experiment_id, image_bytes, provider=provider, model=model)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": f"实验纠错失败: {exc}"}), 500


@app.get("/api/references")
def references():
    try:
        experiment_type = request.args.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400
        if not cfg.get("reference_db", False):
            return jsonify({"references": []})
        return jsonify({"references": _list_experiment_references(cfg)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/models")
def models():
    return jsonify({"provider_models": PROVIDER_MODELS})


@app.get("/api/glasses/health")
def glasses_health():
    return jsonify({
        "status": "ok",
        "default_provider": _GLASSES_DEFAULT_PROVIDER,
        "default_model": _GLASSES_DEFAULT_MODEL,
        "modes": ["experiment", "safety"],
        "features": [
            "experiment",
            "safety",
            "qa",
            "qa_audio",
            "voice_intent",
            "tts_audio",
            "demo_video",
            "teacher_speaker_verify",
        ],
        "experiments": [
            {"value": spec["id"], "label": spec["name"]}
            for spec in list_specs()
            if spec["id"] != "safety" and spec.get("expose_in_glasses", True)
        ],
    })


@app.post("/api/glasses/teacher/enroll")
def glasses_teacher_enroll():
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model_path = _teacher_embedding_model_path()
        from pipeline.speaker_verify.compare import compare_speakers

        # Self-compare once to validate that the reference clip contains speech
        # and that the speaker embedding model can load before entering the loop.
        result = compare_speakers(
            audio_bytes,
            audio_bytes,
            embedding_model=model_path,
            min_speech_ms=_TEACHER_SPEAKER_MIN_SPEECH_MS,
            same_threshold=_TEACHER_SPEAKER_THRESHOLD,
            uncertain_threshold=_TEACHER_SPEAKER_THRESHOLD,
        )
        if result.decision in {"no_speech", "too_short", "bad_reference"}:
            return jsonify({
                "ok": False,
                "error": result.message or result.decision,
                "decision": result.to_dict(),
            }), 400

        global _teacher_reference_audio, _teacher_reference_created_at
        with _teacher_speaker_lock:
            _teacher_reference_audio = audio_bytes
            _teacher_reference_created_at = time.time()

        return jsonify({
            "ok": True,
            "message": "teacher reference enrolled",
            "threshold": _TEACHER_SPEAKER_THRESHOLD,
            "decision": result.to_dict(),
        })
    except Exception as exc:
        return jsonify({"error": f"Teacher enroll failed: {exc}"}), 500


@app.post("/api/glasses/teacher/verify")
def glasses_teacher_verify():
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        query_bytes = uploaded.read()
        if not query_bytes:
            return jsonify({"error": "audio is empty."}), 400

        with _teacher_speaker_lock:
            reference_bytes = _teacher_reference_audio
            enrolled_at = _teacher_reference_created_at
        if reference_bytes is None:
            return jsonify({"error": "teacher reference is not enrolled."}), 409

        threshold = float(request.form.get("threshold", str(_TEACHER_SPEAKER_THRESHOLD)))
        model_path = _teacher_embedding_model_path()
        from pipeline.speaker_verify.compare import compare_speakers

        result = compare_speakers(
            reference_bytes,
            query_bytes,
            embedding_model=model_path,
            min_speech_ms=_TEACHER_SPEAKER_MIN_SPEECH_MS,
            same_threshold=threshold,
            uncertain_threshold=max(0.0, threshold - 0.08),
        )
        return jsonify({
            "match": result.decision == "same_speaker",
            "threshold": threshold,
            "enrolled_at": enrolled_at,
            **result.to_dict(),
        })
    except Exception as exc:
        return jsonify({"error": f"Teacher verify failed: {exc}"}), 500


@app.get("/api/glasses/demo/videos")
def glasses_demo_videos():
    try:
        experiment_type = _normalize_demo_experiment_type(
            request.args.get("experiment_type", "distillation")
        )
        videos = _list_demo_videos(experiment_type)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "videos": [
            {
                "id": item["id"],
                "label": item["label"],
                "filename": item["filename"],
                "experiment_type": item["experiment_type"],
                "stream_url": f"/api/glasses/demo/videos/{item['id']}/stream",
            }
            for item in videos
        ],
    })


@app.get("/api/glasses/demo/videos/<video_id>/stream")
def glasses_demo_video_stream(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    return send_file(video_path, mimetype="video/mp4", conditional=True)


@app.get("/admin/api/demo/videos")
def admin_demo_videos():
    try:
        experiment_type = request.args.get("experiment_type", "").strip()
        videos = _list_demo_videos(_normalize_demo_experiment_type(experiment_type) if experiment_type else None)
        return jsonify({
            "experiments": [
                {"value": key, "label": label}
                for key, label in _DEMO_VIDEO_EXPERIMENTS.items()
            ],
            "videos": videos,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/admin/api/demo/videos")
def admin_upload_demo_video():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "missing video file"}), 400
    try:
        experiment_type = _normalize_demo_experiment_type(request.form.get("experiment_type", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    original_name = _safe_upload_filename(uploaded.filename)
    if Path(original_name).suffix.lower() not in _ALLOWED_DEMO_VIDEO_SUFFIXES:
        return jsonify({"error": "only .mp4 demo videos are supported"}), 400

    target_dir = _demo_video_dir(experiment_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = _unique_path(target_dir / original_name)
    uploaded.save(target_path)

    videos = _list_demo_videos(experiment_type)
    created = next((item for item in videos if Path(item["path"]) == target_path), None)
    return jsonify({"status": "ok", "video": created}), 201


@app.get("/admin/api/demo/videos/<video_id>/download")
def admin_download_demo_video(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=video_path.name)


@app.delete("/admin/api/demo/videos/<video_id>")
def admin_delete_demo_video(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    try:
        video_path.unlink()
    except PermissionError:
        return jsonify({"error": "video is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/records/upload")
def glasses_upload_record():
    return _store_uploaded_record(_record_root(), record_role="student")


@app.post("/api/glasses/teacher/records/upload")
def glasses_upload_teacher_record():
    return _store_uploaded_record(_teacher_record_root(), record_role="teacher")


def _store_uploaded_record(record_root: Path, *, record_role: str):
    video_file = request.files.get("video")
    audio_file = request.files.get("audio")
    if (video_file is None or not video_file.filename) and (audio_file is None or not audio_file.filename):
        return jsonify({"error": "missing record file"}), 400

    now = datetime.now(timezone.utc)
    device_id = _safe_text_field(request.form.get("device_id"), "unknown_device")
    experiment_type = _safe_text_field(request.form.get("experiment_type"), "unknown_experiment")
    record_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    record_dir = record_root / now.strftime("%Y%m%d") / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    try:
        if video_file is not None and video_file.filename:
            files.append(_save_record_upload(video_file, record_dir, "record_video"))
        if audio_file is not None and audio_file.filename:
            files.append(_save_record_upload(audio_file, record_dir, "record_audio"))
    except ValueError as exc:
        shutil.rmtree(record_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400

    client_metadata = _parse_metadata_field(request.form.get("metadata"))
    metadata = {
        "record_id": record_id,
        "record_role": record_role,
        "device_id": device_id,
        "experiment_type": experiment_type,
        "started_at": request.form.get("started_at", ""),
        "ended_at": request.form.get("ended_at", ""),
        "created_at": now.isoformat(),
        "files": files,
        "metadata": client_metadata,
    }
    metadata_path = record_dir / "record.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    automatic_claim = _auto_claim_teacher_upload(metadata, "record")
    if automatic_claim:
        metadata["owner_identity_id"] = automatic_claim["identity_id"]
        metadata["automatic_claim_id"] = automatic_claim["claim_id"]
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    for file_info in files:
        file_path = record_dir / file_info["filename"]
        if file_path.suffix.lower() in {".wav", ".m4a", ".aac", ".mp3"}:
            _media_analysis_queue.enqueue(record_dir, file_path)

    return jsonify({"status": "ok", "record": _record_summary(record_dir)}), 201


@app.get("/admin/api/records")
def admin_records():
    required = _teacher_or_admin_required()
    if required:
        return required
    return jsonify({"records": _list_records(_record_root(), "admin_download_record_file")})


@app.get("/api/workspace/admin/media")
def workspace_admin_media():
    required = _admin_required()
    if required:
        return required
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(50, max(1, int(request.args.get("page_size", "20"))))
    except ValueError:
        return jsonify({"error": "page 和 page_size 必须是整数"}), 400

    source_role = request.args.get("source_role", "all").strip().lower()
    media_type = request.args.get("media_type", "all").strip().lower()
    if source_role not in {"all", "student", "teacher"}:
        return jsonify({"error": "source_role 必须是 all、student 或 teacher"}), 400
    if media_type not in {"all", "audio", "video", "photo"}:
        return jsonify({"error": "media_type 必须是 all、audio、video 或 photo"}), 400

    try:
        start_date = _parse_optional_media_date(request.args.get("start_date", ""))
        end_date = _parse_optional_media_date(request.args.get("end_date", ""))
    except ValueError:
        return jsonify({"error": "日期格式应为 YYYY-MM-DD"}), 400
    if start_date and end_date and end_date < start_date:
        return jsonify({"error": "结束日期不能早于开始日期"}), 400

    raw_glass_number = request.args.get("glass_number", "").strip()
    glass_number = _normalize_glass_number(raw_glass_number)
    if raw_glass_number and not glass_number:
        return jsonify({"error": "眼镜编号无效"}), 400

    items = _list_workspace_admin_media(
        source_role=source_role,
        media_type=media_type,
        start_date=start_date,
        end_date=end_date,
        glass_number=glass_number,
        experiment_type=request.args.get("experiment_type", "").strip(),
    )
    total = len(items)
    start = (page - 1) * page_size
    return jsonify({
        "items": items[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "total": total,
    })


@app.get("/api/workspace/teacher/media")
def workspace_teacher_media():
    user, error = _teacher_user_required()
    if error:
        return error
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(50, max(1, int(request.args.get("page_size", "20"))))
    except ValueError:
        return jsonify({"error": "page 和 page_size 必须是整数"}), 400

    media_type = request.args.get("media_type", "all").strip().lower()
    if media_type not in {"all", "audio", "video", "photo"}:
        return jsonify({"error": "media_type 必须是 all、audio、video 或 photo"}), 400
    try:
        start_date = _parse_optional_media_date(request.args.get("start_date", ""))
        end_date = _parse_optional_media_date(request.args.get("end_date", ""))
    except ValueError:
        return jsonify({"error": "日期格式应为 YYYY-MM-DD"}), 400
    if start_date and end_date and end_date < start_date:
        return jsonify({"error": "结束日期不能早于开始日期"}), 400
    raw_glass_number = request.args.get("glass_number", "").strip()
    glass_number = _normalize_glass_number(raw_glass_number)
    if raw_glass_number and not glass_number:
        return jsonify({"error": "眼镜编号无效"}), 400

    identity_id = str(user["identity_id"])
    owners = claim_item_owners()
    items = _list_workspace_admin_media(
        source_role="teacher",
        media_type=media_type,
        start_date=start_date,
        end_date=end_date,
        glass_number=glass_number,
        experiment_type=request.args.get("experiment_type", "").strip(),
    )
    visible = []
    for item in items:
        item_id = str(item.get("photo_id") if item.get("item_type") == "photo" else item.get("record_id"))
        item_kind = "photo" if item.get("item_type") == "photo" else "record"
        owner = owners.get((item_kind, "teacher", item_id)) or {}
        if owner.get("identity_id") != identity_id:
            continue
        if item_kind == "photo":
            item["thumbnail_url"] = url_for(
                "workspace_teacher_photo_thumbnail", photo_id=item_id
            )
        visible.append(item)

    total = len(visible)
    start = (page - 1) * page_size
    return jsonify({
        "items": visible[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "total": total,
        "binding": active_teacher_binding(identity_id=identity_id),
    })


@app.get("/admin/api/records/<record_id>/files/<filename>")
def admin_download_record_file(record_id: str, filename: str):
    required = _teacher_or_admin_required()
    if required:
        return required
    record_dir = _resolve_record_dir(_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    safe_name = Path(filename).name
    file_path = record_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return send_file(
        file_path,
        mimetype=mime,
        as_attachment=request.args.get("download", "1") != "0",
        download_name=file_path.name,
    )


@app.delete("/admin/api/records/<record_id>")
def admin_delete_record(record_id: str):
    required = _admin_required()
    if required:
        return required
    record_dir = _resolve_record_dir(_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    try:
        shutil.rmtree(record_dir)
    except PermissionError:
        return jsonify({"error": "record is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/teacher-admin/api/records")
def teacher_admin_records():
    if not _admin_is_unlocked():
        user, error = _teacher_user_required()
        if error:
            return error
        identity_id = str(user["identity_id"])
        owners = claim_item_owners()
        records = [
            item for item in _list_records(_teacher_record_root(), "teacher_admin_download_record_file")
            if (owners.get(("record", "teacher", str(item.get("record_id", "")))) or {}).get("identity_id") == identity_id
        ]
    else:
        records = _list_records(_teacher_record_root(), "teacher_admin_download_record_file")
    return jsonify({"records": records})


def _teacher_media_access_allowed(item_type: str, item_id: str) -> bool:
    if _admin_is_unlocked():
        return True
    user = _current_user()
    if user is None or user.get("role") != "teacher":
        return False
    owner = claim_item_owners().get((item_type, "teacher", item_id)) or {}
    return owner.get("identity_id") == user.get("identity_id")


@app.get("/teacher-admin/api/records/<record_id>/files/<filename>")
def teacher_admin_download_record_file(record_id: str, filename: str):
    if not _teacher_media_access_allowed("record", record_id):
        return jsonify({"error": "无权访问该记录"}), 403
    record_dir = _resolve_record_dir(_teacher_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    safe_name = Path(filename).name
    file_path = record_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return send_file(
        file_path,
        mimetype=mime,
        as_attachment=request.args.get("download", "1") != "0",
        download_name=file_path.name,
    )


@app.delete("/teacher-admin/api/records/<record_id>")
def teacher_admin_delete_record(record_id: str):
    if not _teacher_media_access_allowed("record", record_id):
        return jsonify({"error": "无权删除该记录"}), 403
    record_dir = _resolve_record_dir(_teacher_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    try:
        shutil.rmtree(record_dir)
    except PermissionError:
        return jsonify({"error": "record is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/photos/upload")
def glasses_upload_photo():
    return _store_uploaded_photo(_photo_root(), record_role="student")


@app.post("/api/glasses/teacher/photos/upload")
def glasses_upload_teacher_photo():
    return _store_uploaded_photo(_teacher_photo_root(), record_role="teacher")


def _store_uploaded_photo(photo_root: Path, *, record_role: str):
    photo_file = request.files.get("photo")
    if photo_file is None or not photo_file.filename:
        return jsonify({"error": "missing photo file"}), 400

    original_name = _safe_upload_filename(photo_file.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_PHOTO_SUFFIXES:
        return jsonify({"error": f"unsupported photo file type: {suffix}"}), 400

    now = datetime.now(timezone.utc)
    photo_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    photo_dir = photo_root / now.strftime("%Y%m%d") / photo_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"photo{suffix}"
    try:
        photo_file.save(photo_path)
    except Exception:
        shutil.rmtree(photo_dir, ignore_errors=True)
        raise

    metadata = {
        "photo_id": photo_id,
        "record_role": record_role,
        "device_id": _safe_text_field(request.form.get("device_id"), "unknown_device"),
        "experiment_type": _safe_text_field(
            request.form.get("experiment_type"),
            "unknown_experiment",
        ),
        "captured_at": request.form.get("captured_at", ""),
        "created_at": now.isoformat(),
        "filename": photo_path.name,
        "original_filename": original_name,
        "size": photo_path.stat().st_size,
    }
    metadata_path = photo_dir / "photo.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    automatic_claim = _auto_claim_teacher_upload(metadata, "photo")
    if automatic_claim:
        metadata["owner_identity_id"] = automatic_claim["identity_id"]
        metadata["automatic_claim_id"] = automatic_claim["claim_id"]
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _media_analysis_queue.enqueue(photo_dir, photo_path)
    return jsonify({"status": "ok", "photo": _photo_summary(photo_dir)}), 201


@app.get("/admin/api/photos")
def admin_photos():
    required = _teacher_or_admin_required()
    if required:
        return required
    return jsonify({"photos": _list_photos(_photo_root(), "admin_photo_file")})


@app.get("/admin/api/photos/<photo_id>/file")
def admin_photo_file(photo_id: str):
    required = _teacher_or_admin_required()
    if required:
        return required
    photo_dir = _resolve_photo_dir(_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        return jsonify({"error": "photo file not found"}), 404
    return send_file(
        photo_path,
        mimetype=mimetypes.guess_type(photo_path.name)[0] or "image/jpeg",
        as_attachment=request.args.get("download") == "1",
        download_name=photo_path.name,
    )


@app.delete("/admin/api/photos/<photo_id>")
def admin_delete_photo(photo_id: str):
    required = _admin_required()
    if required:
        return required
    photo_dir = _resolve_photo_dir(_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    try:
        shutil.rmtree(photo_dir)
    except PermissionError:
        return jsonify({"error": "photo is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/teacher-admin/api/photos")
def teacher_admin_photos():
    if not _admin_is_unlocked():
        user, error = _teacher_user_required()
        if error:
            return error
        identity_id = str(user["identity_id"])
        owners = claim_item_owners()
        photos = [
            item for item in _list_photos(_teacher_photo_root(), "teacher_admin_photo_file")
            if (owners.get(("photo", "teacher", str(item.get("photo_id", "")))) or {}).get("identity_id") == identity_id
        ]
    else:
        photos = _list_photos(_teacher_photo_root(), "teacher_admin_photo_file")
    return jsonify({"photos": photos})


@app.get("/teacher-admin/api/photos/<photo_id>/file")
def teacher_admin_photo_file(photo_id: str):
    if not _teacher_media_access_allowed("photo", photo_id):
        return jsonify({"error": "无权访问该照片"}), 403
    return _send_photo_file(_teacher_photo_root(), photo_id)


@app.get("/api/workspace/admin/media/photos/<source_role>/<photo_id>/thumbnail")
def workspace_admin_photo_thumbnail(source_role: str, photo_id: str):
    required = _admin_required()
    if required:
        return required
    roots = {"student": _photo_root(), "teacher": _teacher_photo_root()}
    root = roots.get(source_role)
    if root is None:
        return jsonify({"error": "invalid source role"}), 400
    return _send_photo_thumbnail(root, photo_id)


@app.get("/api/workspace/teacher/media/photos/<photo_id>/thumbnail")
def workspace_teacher_photo_thumbnail(photo_id: str):
    if not _teacher_media_access_allowed("photo", photo_id):
        return jsonify({"error": "无权访问该照片"}), 403
    return _send_photo_thumbnail(_teacher_photo_root(), photo_id)


def _send_photo_thumbnail(root: Path, photo_id: str):
    photo_dir = _resolve_photo_dir(root, photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    photo_path = next(
        (path for path in photo_dir.iterdir() if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES),
        None,
    )
    if photo_path is None:
        return jsonify({"error": "photo file not found"}), 404
    try:
        with Image.open(photo_path) as source:
            preview = ImageOps.exif_transpose(source)
            preview.thumbnail((480, 320), Image.Resampling.LANCZOS)
            if preview.mode != "RGB":
                preview = preview.convert("RGB")
            output = BytesIO()
            preview.save(output, format="JPEG", quality=78, optimize=True)
            output.seek(0)
    except (OSError, ValueError):
        return jsonify({"error": "unable to create thumbnail"}), 422
    return send_file(output, mimetype="image/jpeg", max_age=86400)


@app.delete("/teacher-admin/api/photos/<photo_id>")
def teacher_admin_delete_photo(photo_id: str):
    if not _teacher_media_access_allowed("photo", photo_id):
        return jsonify({"error": "无权删除该照片"}), 403
    photo_dir = _resolve_photo_dir(_teacher_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    try:
        shutil.rmtree(photo_dir)
    except PermissionError:
        return jsonify({"error": "photo is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/admin/api/feedback")
def admin_feedback():
    return jsonify({"feedback": _list_feedback()})


@app.post("/admin/api/feedback")
def admin_create_feedback():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not content:
        return jsonify({"error": "content is required"}), 400
    if len(title) > 120:
        return jsonify({"error": "title is too long"}), 400
    if len(content) > 5000:
        return jsonify({"error": "content is too long"}), 400

    now = datetime.now(timezone.utc)
    feedback_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    item = {
        "feedback_id": feedback_id,
        "title": title,
        "content": content,
        "created_at": now.isoformat(),
    }
    root = _feedback_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{feedback_id}.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify({"status": "ok", "feedback": item}), 201


@app.delete("/admin/api/feedback/<feedback_id>")
def admin_delete_feedback(feedback_id: str):
    feedback_path = _resolve_feedback_path(feedback_id)
    if feedback_path is None:
        return jsonify({"error": "feedback not found"}), 404
    try:
        feedback_path.unlink()
    except PermissionError:
        return jsonify({"error": "feedback is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/qa")
def glasses_qa():
    started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question", "")).strip()
        if not question:
            return jsonify({"error": "question is required."}), 400

        provider = str(payload.get("provider", _GLASSES_DEFAULT_PROVIDER)).strip() or _GLASSES_DEFAULT_PROVIDER
        model = str(payload.get("model", _GLASSES_DEFAULT_MODEL)).strip() or _GLASSES_DEFAULT_MODEL

        from pipeline.qa.llm_qa import answer_question

        if not _vlm_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            answer, vlm_ms = answer_question(
                question,
                provider=provider,
                model=model,
            )
        finally:
            _vlm_semaphore.release()
        return jsonify({
            "question": question,
            "answer": answer,
            "timings": {
                "vlm": vlm_ms,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
        })
    except Exception as exc:
        return jsonify({"error": f"QA failed: {exc}"}), 500


@app.post("/api/glasses/qa/audio")
def glasses_qa_audio():
    started = time.perf_counter()
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model = request.form.get("model", "qwen3.5-omni-plus-realtime").strip()
        timeout_raw = request.form.get("timeout_seconds", "").strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.qa.omni_audio_qa import answer_audio_question

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = answer_audio_question(
                audio_bytes,
                model=model or None,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        return jsonify({
            "transcript": result.get("transcript", ""),
            "answer_text": result.get("answer_text", ""),
            "answer_audio_data_url": result.get("answer_audio_data_url"),
            "timings": timings,
        })
    except Exception as exc:
        return jsonify({"error": f"Audio QA failed: {exc}"}), 500


@app.post("/api/glasses/tts")
def glasses_tts():
    started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required."}), 400

        model = str(payload.get("model", "qwen3.5-omni-plus-realtime")).strip() or None
        timeout_raw = str(payload.get("timeout_seconds", "")).strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.qa.omni_tts import synthesize_text_to_audio

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = synthesize_text_to_audio(
                text,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        return jsonify({
            "audio_data_url": result.get("audio_data_url"),
            "transcript": result.get("transcript", ""),
            "timings": timings,
        })
    except Exception as exc:
        return jsonify({"error": f"TTS failed: {exc}"}), 500


@app.post("/api/glasses/voice/intent")
def glasses_voice_intent():
    started = time.perf_counter()
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model = request.form.get("model", "qwen3.5-omni-flash-realtime").strip()
        context = request.form.get("context", "").strip() or None
        demo_options = request.form.get("demo_options", "").strip() or None
        timeout_raw = request.form.get("timeout_seconds", "").strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.voice_control.intent_omni import classify_audio_intent

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = classify_audio_intent(
                audio_bytes,
                context=context,
                demo_options=demo_options,
                model=model or None,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        result["timings"] = timings
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": f"Voice intent failed: {exc}"}), 500


@app.post("/api/glasses/overlay-test")
def glasses_overlay_test():
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Missing image file."}), 400

        image_bytes = uploaded.read()
        if not image_bytes:
            return jsonify({"error": "Uploaded image is empty."}), 400

        experiment_type = request.form.get("experiment_type", "other").strip() or "other"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"Unknown experiment_type: {experiment_type}"}), 400

        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
        components = list(cfg.get("components", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} is missing component config."}), 400

        image = decode_image(image_bytes)
        from pipeline.common.cv_wrapper import detect_wrong_image, annotate_image, encode_annotated_image

        base_params = {
            "max_side": 1600,
            "lowe_ratio": 0.75,
            "min_good_matches": 3,
            "min_inliers": 2,
            "min_inlier_ratio": 0.05,
            "top_k_per_label": 6,
            "overlap_weight": 0.75,
            "component_labels": component_labels,
        }

        result = None
        detection_feature = "sift"
        last_error = None
        for feature in ("sift", "akaze"):
            try:
                candidate = detect_wrong_image(image, CVDetectionParams(**base_params, feature=feature))
            except Exception as exc:
                last_error = exc
                continue
            if result is None:
                result = candidate
                detection_feature = feature
            if any(candidate.get("boxes", {}).get(label) for label in component_labels):
                result = candidate
                detection_feature = feature
                break
        if result is None:
            raise last_error or RuntimeError("CV detection failed.")

        image_size = {"width": int(image.shape[1]), "height": int(image.shape[0])}
        overlay_boxes = _build_overlay_boxes(result, components, image_size)
        preview_data_url = encode_annotated_image(annotate_image(image, result))

        return jsonify({
            "experiment_type": experiment_type,
            "detection_feature": detection_feature,
            "overlay_image_size": image_size,
            "overlay_boxes": overlay_boxes,
            "preview_data_url": preview_data_url,
            "message": "Overlay test detection finished.",
        })
    except Exception as exc:
        return jsonify({"error": f"Overlay test failed: {exc}"}), 500


@app.post("/api/glasses/jobs")
def create_glasses_job():
    _cleanup_glasses_jobs()
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Missing image file."}), 400

        mode = request.form.get("mode", "experiment").strip() or "experiment"
        if mode not in {"experiment", "safety"}:
            return jsonify({"error": "mode must be experiment or safety."}), 400

        experiment_type = request.form.get("experiment_type", "distillation").strip() or "distillation"
        if mode == "experiment" and resolve_experiment_spec(experiment_type) is None:
            return jsonify({"error": f"Unknown experiment_type: {experiment_type}"}), 400

        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", _GLASSES_DEFAULT_MODEL).strip() or _GLASSES_DEFAULT_MODEL
        image_bytes = uploaded.read()
        if not image_bytes:
            return jsonify({"error": "Uploaded image is empty."}), 400

        job_id = _submit_glasses_job(
            image_bytes=image_bytes,
            mode=mode,
            experiment_type=experiment_type,
            provider=provider,
            model=model,
        )
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "poll_url": f"/api/glasses/jobs/{job_id}",
        }), 202
    except Exception as exc:
        return jsonify({"error": f"Failed to create job: {exc}"}), 500


@app.get("/api/glasses/jobs/<job_id>")
def get_glasses_job(job_id: str):
    _cleanup_glasses_jobs()
    job = _snapshot_glasses_job(job_id)
    if job is None:
        return jsonify({
            "job_id": job_id,
            "status": "expired",
            "stage": "done",
            "message": "Job not found or expired.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": "Job not found or expired.",
        }), 404
    return jsonify(job)


@app.get("/api/experiment-configs")
def experiment_configs():
    try:
        configs = _load_experiment_ui_config()
        enriched: dict[str, dict] = {}
        for experiment_type, item in configs.items():
            cfg = get_experiment_config(experiment_type)
            refs = _list_experiment_references(cfg) if cfg is not None else []
            ref_by_id = {str(ref.get("id", "")): ref for ref in refs}
            ref = ref_by_id.get(str(item.get("reference_id", "")))
            configured_file = str(item.get("annotated_image_path", "")).strip()
            enriched[experiment_type] = {
                **item,
                "reference_name": Path(configured_file).name if configured_file else (ref.get("original_filename", "") if ref else ""),
            }
        return jsonify({"experiment_configs": enriched})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/config-reference-image")
def config_reference_image():
    try:
        experiment_type = request.args.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400
        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
        ui_config = _get_experiment_ui_config(experiment_type)
        from pipeline.common.cv_wrapper import encode_annotated_image
        if str(ui_config.get("correct_source", "")).strip() == "file":
            image_path = _resolve_configured_image_path(ui_config)
            annotated = decode_image(image_path.read_bytes())
            return jsonify({"data_url": encode_annotated_image(annotated)})

        reference_id = str(ui_config.get("reference_id", "")).strip()
        if not reference_id:
            return jsonify({"error": "未配置参考图。"}), 400

        image = load_reference_image(reference_id)
        result = build_reference_result(reference_id, component_labels)
        annotated = cv_core.annotate_image(image, result)
        return jsonify({"data_url": encode_annotated_image(annotated)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/safety/check")
def safety_check():
    """JSON-configured safety retrieval and analysis."""
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待检查图片。"}), 400

        image_bytes = uploaded.read()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or None
        from pipeline.experiments.runner import run_experiment_check
        return jsonify(
            run_experiment_check(
                "safety",
                image_bytes,
                provider=provider,
                model=model,
            )
        )
    except Exception as exc:
        return jsonify({"error": f"安全检查失败: {exc}"}), 500


@app.post("/api/electrolysis/check")
def electrolysis_check():
    """JSON-configured electrolysis retrieval and analysis."""
    try:
        uploaded = request.files.get("image") or request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待纠错图片。"}), 400

        image_bytes = uploaded.read()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or _GLASSES_DEFAULT_MODEL
        from pipeline.experiments.runner import run_experiment_check
        return jsonify(
            run_experiment_check(
                "electrolysis",
                image_bytes,
                provider=provider,
                model=model,
            )
        )
    except Exception as exc:
        return jsonify({"error": f"电解实验纠错失败: {exc}"}), 500


@app.post("/api/detect")
def detect():
    """Step 1: CV 检测，立即返回标注图，同时缓存结果供后续 VLM 调用。"""
    _cleanup_store()
    timing = TimingRecord()

    try:
        uploaded = request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "缺少待纠错图片。"}), 400

        with StepTimer(timing, "图片解码"):
            wrong_image = decode_image(uploaded.read())

        experiment_type = request.form.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400

        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
        components = list(cfg.get("components", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} 实验缺少组件配置。"}), 400

        ui_config = _get_experiment_ui_config(experiment_type)
        reference_id = str(ui_config.get("reference_id", "")).strip()
        experiment_requirements = str(ui_config.get("experiment_requirements", "")).strip()
        correct_source = str(ui_config.get("correct_source", "database")).strip() or "database"

        cv_params = CVDetectionParams(
            max_side=int(request.form.get("max_side", "1600")),
            lowe_ratio=float(request.form.get("lowe_ratio", "0.75")),
            min_good_matches=int(request.form.get("min_good_matches", "3")),
            min_inliers=int(request.form.get("min_inliers", "2")),
            min_inlier_ratio=float(request.form.get("min_inlier_ratio", "0.08")),
            top_k_per_label=int(request.form.get("top_k_per_label", "6")),
            overlap_weight=float(request.form.get("overlap_weight", "0.75")),
            feature=request.form.get("feature", "akaze").strip(),
            component_labels=component_labels,
        )

        from pipeline.common.cv_wrapper import detect_wrong_image

        with StepTimer(timing, "CV检测"):
            wrong_result = detect_wrong_image(wrong_image, cv_params)

        if correct_source == "file":
            with StepTimer(timing, "加载正确标注图"):
                image_path = _resolve_configured_image_path(ui_config)
                correct_annotated = decode_image(image_path.read_bytes())
            correct_result = _build_uploaded_result(components)
        elif correct_source == "upload":
            uploaded_correct = request.files.get("correct_image")
            if uploaded_correct is None or not uploaded_correct.filename:
                return jsonify({"error": "选择了上传模式但未上传正确图。"}), 400
            with StepTimer(timing, "解码正确图"):
                correct_annotated = decode_image(uploaded_correct.read())

            correct_result = _build_uploaded_result(components)
        else:
            refs = _list_experiment_references(cfg)
            if not reference_id and refs:
                reference_id = refs[0]["id"]
            if not reference_id:
                return jsonify({"error": "没有可用的参考图。"}), 400

            with StepTimer(timing, "加载参考图"):
                correct_image = load_reference_image(reference_id)
                correct_result = build_reference_result(reference_id, component_labels)
                correct_annotated = cv_core.annotate_image(correct_image, correct_result)

        with StepTimer(timing, "构建标注图"):
            from pipeline.common.cv_wrapper import annotate_image, encode_annotated_image
            from pipeline.common.image_builder import build_cv_structured_summary

            wrong_annotated = annotate_image(wrong_image, wrong_result)

            # label_summary
            label_summary = _build_label_summary_from_results(correct_result, wrong_result, components)
            cv_summary = build_cv_structured_summary(
                label_summary,
                correct_is_uploaded=(correct_source == "upload"),
            )

            correct_data_url = encode_annotated_image(correct_annotated)
            wrong_data_url = encode_annotated_image(wrong_annotated)

            # 给 VLM 用的 JPEG
            _, c_jpg = cv2.imencode(".jpg", correct_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            _, w_jpg = cv2.imencode(".jpg", wrong_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

        session_id = uuid.uuid4().hex[:12]
        _detect_store[session_id] = {
            "experiment_type": experiment_type,
            "correct_source": correct_source,
            "label_summary": label_summary,
            "cv_summary": cv_summary,
            "jpeg_pair": (c_jpg.tobytes(), w_jpg.tobytes()),
            "experiment_requirements": experiment_requirements,
            "_created": time.time(),
        }

        timing.record(
            "总计",
            timing.to_dict().get("CV检测", 0)
            + timing.to_dict().get("加载正确标注图", 0)
            + timing.to_dict().get("构建标注图", 0),
        )
        return jsonify({
            "session_id": session_id,
            "correct_data_url": correct_data_url,
            "wrong_data_url": wrong_data_url,
            "label_summary": label_summary,
            "timings": timing.to_dict(),
        })

    except Exception as exc:
        return jsonify({"error": f"检测失败: {exc}"}), 500


@app.post("/api/analyze")
def analyze():
    """Step 2: VLM 分析。传入 session_id + approach + provider/model。"""
    _cleanup_store()
    timing = TimingRecord()

    try:
        session_id = request.form.get("session_id", "").strip()
        approach = request.form.get("approach", "a").strip()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip()
        model = request.form.get("model", "").strip() or None

        stored = _detect_store.get(session_id)
        if stored is None:
            return jsonify({"error": "会话已过期，请重新检测。"}), 400
        cfg = get_experiment_config(stored.get("experiment_type", "distillation"))
        if cfg is None:
            return jsonify({"error": "实验配置已失效，请重新检测。"}), 400

        jpeg_pair = stored["jpeg_pair"]
        cv_summary = stored["cv_summary"]
        label_summary = stored["label_summary"]
        experiment_requirements = stored["experiment_requirements"]

        result: dict = {}

        # 当前生产路径只触发 Approach A。Approach B 的实现保留在代码里，
        # 但不会被 Web 流程调用，避免两次 VLM 调用。
        need_a = True
        need_b = False

        if need_a:
            from pipeline.approach_a.pipeline_a import run_pipeline_a
            with StepTimer(timing, "方案A_VLM"):
                a_result = run_pipeline_a(
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider,
                    vlm_model=model,
                    label_summary=label_summary,
                    cv_summary=cv_summary,
                    prebuilt_jpeg=jpeg_pair,
                    experiment_config=cfg,
                )
            result["approach_a"] = {"analysis_text": a_result.analysis_text}

        if need_b:
            with StepTimer(timing, "方案B_找不同"):
                diff_text = _run_b_step1(
                    vlm_provider=provider, vlm_model=model,
                    cv_summary=cv_summary, prebuilt_jpeg=jpeg_pair,
                )
            with StepTimer(timing, "方案B_分析"):
                analysis_text = _run_b_step2(
                    diff_text=diff_text, cv_summary=cv_summary,
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider, vlm_model=model,
                )
            result["approach_b"] = {"diff_text": diff_text, "analysis_text": analysis_text}

        result["timings"] = timing.to_dict()
        return jsonify(result)

    except Exception as exc:
        return jsonify({"error": f"分析失败: {exc}"}), 500


def _cleanup_glasses_jobs():
    cutoff = time.time() - (_GLASSES_JOB_TTL_SECONDS * 2)
    with _glasses_lock:
        expired = [
            job_id for job_id, job in _glasses_jobs.items()
            if job.get("created_at", 0) < cutoff
        ]
        for job_id in expired:
            _glasses_jobs.pop(job_id, None)


def _submit_glasses_job(
    *,
    image_bytes: bytes,
    mode: str,
    experiment_type: str,
    provider: str,
    model: str,
) -> str:
    with _glasses_lock:
        pending = sum(1 for j in _glasses_jobs.values() if j.get("status") in ("queued", "running"))
        if pending >= _GLASSES_MAX_PENDING:
            raise RuntimeError(f"Too many pending jobs ({pending}), try again later.")
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _glasses_lock:
        _glasses_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "upload",
            "message": "Job queued.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
    _glasses_executor.submit(
        _run_glasses_job,
        job_id,
        image_bytes,
        mode,
        experiment_type,
        provider,
        model,
    )
    return job_id


def _update_glasses_job(job_id: str, **updates: Any) -> None:
    updates["updated_at"] = time.time()
    with _glasses_lock:
        job = _glasses_jobs.get(job_id)
        if job is not None:
            job.update(updates)


def _snapshot_glasses_job(job_id: str) -> dict[str, Any] | None:
    with _glasses_lock:
        job = _glasses_jobs.get(job_id)
        if job is None:
            return None
        snapshot = dict(job)

    if time.time() - snapshot.get("created_at", 0) > _GLASSES_JOB_TTL_SECONDS:
        snapshot.update({
            "status": "expired",
            "stage": "done",
            "message": "Job expired.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": "Job expired.",
        })

    return {
        "job_id": snapshot.get("job_id", job_id),
        "status": snapshot.get("status", "expired"),
        "stage": snapshot.get("stage", "done"),
        "message": snapshot.get("message", ""),
        "preview_data_url": snapshot.get("preview_data_url"),
        "overlay_image_size": snapshot.get("overlay_image_size"),
        "overlay_boxes": snapshot.get("overlay_boxes", []),
        "result": snapshot.get("result"),
        "error": snapshot.get("error"),
    }


def _run_glasses_job(
    job_id: str,
    image_bytes: bytes,
    mode: str,
    experiment_type: str,
    provider: str,
    model: str,
) -> None:
    _update_glasses_job(job_id, status="running", stage="upload", message="Image accepted.")
    try:
        from pipeline.experiments.runner import run_experiment_check
        exp_id = experiment_type if mode == "experiment" else "safety"
        _update_glasses_job(job_id, stage="processing", message="Running check.")
        result = run_experiment_check(exp_id, image_bytes, provider=provider, model=model)
        _attach_analysis_audio(job_id, result)
        _update_glasses_job(
            job_id,
            status="succeeded",
            stage="done",
            message="Done.",
            result=result,
            error=None,
        )
    except Exception as exc:
        _update_glasses_job(
            job_id,
            status="failed",
            stage="done",
            message="Failed.",
            result=None,
            error=str(exc),
        )


def _attach_analysis_audio(job_id: str, result: dict[str, Any]) -> None:
    analysis_text = str(result.get("analysis_text", "")).strip()
    if not analysis_text:
        result["analysis_audio_data_url"] = None
        return

    _update_glasses_job(job_id, stage="tts", message="Preparing voice summary.")
    timings = result.setdefault("timings", {})
    try:
        from pipeline.qa.omni_tts import synthesize_text_to_audio

        tts_text = analysis_text[:160]
        audio = synthesize_text_to_audio(tts_text, timeout_seconds=8)
        result["analysis_audio_data_url"] = audio.get("audio_data_url")
        if isinstance(timings, dict):
            timings.update(audio.get("timings", {}))
    except Exception as exc:
        result["analysis_audio_data_url"] = None
        if isinstance(timings, dict):
            timings["tts_error"] = str(exc)


def _demo_video_label(
    index: int,
    path: Path,
    experiment_type: str,
    *,
    include_experiment: bool = False,
) -> str:
    if include_experiment:
        return f"{index}. {_DEMO_VIDEO_EXPERIMENTS.get(experiment_type, experiment_type)} / {path.name}"
    return f"{index}. {path.name}"


def _run_b_step1(*, vlm_provider, vlm_model, cv_summary, prebuilt_jpeg):
    from pipeline.approach_b.pipeline_b import build_diff_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm
    client = create_vlm(vlm_provider, vlm_model)
    messages = build_diff_messages(prebuilt_jpeg[0], prebuilt_jpeg[1], cv_summary, client)
    result, _ = call_vlm(client, messages)
    return result


def _run_b_step2(*, diff_text, cv_summary, experiment_requirements, vlm_provider, vlm_model):
    from pipeline.approach_b.pipeline_b import build_analysis_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm
    client = create_vlm(vlm_provider, vlm_model)
    messages = build_analysis_messages(diff_text, cv_summary, experiment_requirements)
    result, _ = call_vlm(client, messages)
    return result


def _build_uploaded_result(components):
    return {
        "boxes": {c["label"]: [0, 0, 1, 1] for c in components},
        "detections": {c["label"]: {"score": None} for c in components},
    }



def _build_overlay_boxes(
    detection_result: dict[str, Any],
    components: list[dict[str, Any]],
    image_size: dict[str, int],
) -> list[dict[str, Any]]:
    width = max(1, int(image_size.get("width", 1)))
    height = max(1, int(image_size.get("height", 1)))
    component_by_label = {str(item.get("label", "")): item for item in components}
    boxes = detection_result.get("boxes", {}) or {}
    detections = detection_result.get("detections", {}) or {}
    overlay: list[dict[str, Any]] = []

    for label, raw_box in boxes.items():
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in raw_box]
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue

        comp = component_by_label.get(str(label), {})
        detection = detections.get(label, {}) if isinstance(detections, dict) else {}
        overlay.append({
            "label": str(label),
            "name": str(comp.get("name", label)),
            "color": str(comp.get("color", "#FFD54F")),
            "box": [
                round(max(0.0, min(1.0, x1 / width)), 6),
                round(max(0.0, min(1.0, y1 / height)), 6),
                round(max(0.0, min(1.0, x2 / width)), 6),
                round(max(0.0, min(1.0, y2 / height)), 6),
            ],
            "score": detection.get("score") if isinstance(detection, dict) else None,
        })
    return overlay



def _load_experiment_ui_config() -> dict[str, dict]:
    configs: dict[str, dict] = {}
    for spec in list_specs():
        if spec.get("strategy") != "cv_reference":
            continue
        reference = spec.get("reference", {})
        configs[spec["id"]] = {
            "correct_source": reference.get("source", "database"),
            "reference_id": reference.get("reference_id", ""),
            "annotated_image_path": reference.get("annotated_image_path", ""),
            "experiment_requirements": spec.get("experiment_requirements", ""),
        }
    return configs



def _get_experiment_ui_config(experiment_type: str) -> dict:
    return _load_experiment_ui_config().get(experiment_type, {})



def _resolve_configured_image_path(ui_config: dict) -> Path:
    raw_path = str(ui_config.get("annotated_image_path", "")).strip()
    if not raw_path:
        raise FileNotFoundError("未配置 annotated_image_path。")
    if raw_path.startswith("assets://"):
        path = runtime_asset_path(*[part for part in raw_path[len("assets://"):].split("/") if part])
    else:
        path = Path(raw_path)
    if not path.is_absolute():
        path = (SPECS_DIR / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置参考图不存在: {path}")
    return path



def _list_experiment_references(cfg):
    component_labels = [
        component["label"]
        for component in cfg.get("components", [])
    ]
    refs = list_available_references(component_labels or None)
    if cfg.get("require_reference_labels", False):
        refs = [ref for ref in refs if ref.get("annotated_labels")]
    return refs



def _demo_video_root() -> Path:
    return runtime_asset_path("demo_videos")



def _demo_video_dir(experiment_type: str) -> Path:
    return _demo_video_root() / experiment_type



def _record_root() -> Path:
    return runtime_asset_path("user_records")


def _teacher_record_root() -> Path:
    return runtime_asset_path("teacher_records")


def _photo_root() -> Path:
    return runtime_asset_path("user_photos")


def _teacher_photo_root() -> Path:
    return runtime_asset_path("teacher_photos")



def _feedback_root() -> Path:
    return runtime_asset_path("feedback")



def _normalize_demo_experiment_type(value: str | None) -> str:
    experiment_type = (value or "").strip()
    if experiment_type not in _DEMO_VIDEO_EXPERIMENTS:
        raise ValueError(f"未知演示视频实验类型: {experiment_type or '(empty)'}")
    return experiment_type



def _path_demo_experiment_type(path: Path, root: Path) -> str:
    rel_parts = path.relative_to(root).parts
    if rel_parts and rel_parts[0] in _DEMO_VIDEO_EXPERIMENTS:
        return rel_parts[0]
    # 兼容旧资源：之前直接放在 demo_videos/ 根目录的视频默认按减压蒸馏处理。
    return "distillation"



def _list_demo_videos(experiment_type: str | None = None) -> list[dict[str, str]]:
    video_dir = _demo_video_root()
    video_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        path for path in video_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in _ALLOWED_DEMO_VIDEO_SUFFIXES
    ]
    if experiment_type is not None:
        paths = [
            path for path in paths
            if _path_demo_experiment_type(path, video_dir) == experiment_type
        ]
    paths.sort(key=lambda item: item.relative_to(video_dir).as_posix().lower())

    videos = []
    for index, path in enumerate(paths, start=1):
        item_experiment_type = _path_demo_experiment_type(path, video_dir)
        videos.append({
            "id": _demo_video_id(path),
            "label": _demo_video_label(index, path, item_experiment_type, include_experiment=experiment_type is None),
            "filename": path.name,
            "experiment_type": item_experiment_type,
            "experiment_label": _DEMO_VIDEO_EXPERIMENTS.get(item_experiment_type, item_experiment_type),
            "relative_path": path.relative_to(video_dir).as_posix(),
            "path": str(path),
            "size": path.stat().st_size,
        })
    return videos



def _resolve_demo_video(video_id: str) -> Path | None:
    try:
        rel = _decode_urlsafe_id(video_id)
    except ValueError:
        return None
    path = (_demo_video_root() / rel).resolve()
    root = _demo_video_root().resolve()
    if not _is_relative_to(path, root):
        return None
    if not path.exists() or not path.is_file() or path.suffix.lower() not in _ALLOWED_DEMO_VIDEO_SUFFIXES:
        return None
    return path


def _demo_video_id(path: Path) -> str:
    relative_path = path.relative_to(_demo_video_root()).as_posix()
    return _encode_urlsafe_id(relative_path)



def _teacher_embedding_model_path() -> Path:
    raw = os.getenv("SPEAKER_VERIFY_EMBEDDING_MODEL", "").strip()
    path = Path(raw) if raw else _TEACHER_DEFAULT_EMBEDDING_MODEL
    if not path.exists():
        raise FileNotFoundError(
            "声纹模型不存在。请设置 SPEAKER_VERIFY_EMBEDDING_MODEL，"
            f"当前路径: {path}"
        )
    return path



def _build_label_summary_from_results(correct_result, wrong_result, components):
    from pipeline.common.cv_wrapper import get_component_info
    wrong_boxes = wrong_result.get("boxes", {})
    wrong_detections = wrong_result.get("detections", {})
    correct_boxes = correct_result.get("boxes", {})
    result = {}
    for comp in get_component_info(components):
        label = comp["label"]
        c_box = correct_boxes.get(label, [])
        w_box = wrong_boxes.get(label, [])
        sentinel = [0, 0, 1, 1]
        result[label] = {
            "name": comp["name"], "color": comp["color"],
            "in_correct": bool(c_box),
            "in_wrong": bool(w_box),
            "correct_box": c_box if c_box != sentinel else [],
            "wrong_box": w_box,
            "wrong_score": wrong_detections.get(label, {}).get("score"),
            "wrong_raw_score": wrong_detections.get(label, {}).get("raw_score"),
            "wrong_failure_reason": wrong_detections.get(label, {}).get("failure_reason"),
        }
    return result


# 延迟导入，避免循环
import pipeline.common.cv_wrapper as cv_core





def _encode_urlsafe_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_urlsafe_id(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("invalid id") from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_upload_filename(filename: str) -> str:
    raw = Path(filename).name.strip()
    raw = re.sub(r"[\x00-\x1f\x7f]+", "", raw)
    raw = re.sub(r'[<>:"/\\|?*]+', "_", raw)
    raw = raw.strip(" .")
    return raw or f"upload_{uuid.uuid4().hex[:8]}"


def _safe_text_field(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[\x00-\x1f\x7f]+", "", text)
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    return text[:80] or fallback


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{timestamp}_{uuid.uuid4().hex[:6]}{path.suffix}")


def _parse_metadata_field(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _save_record_upload(uploaded, record_dir: Path, prefix: str) -> dict[str, Any]:
    original_name = _safe_upload_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_RECORD_SUFFIXES:
        raise ValueError(f"unsupported record file type: {suffix}")
    target = _unique_path(record_dir / f"{prefix}{suffix}")
    uploaded.save(target)
    return {
        "filename": target.name,
        "original_filename": original_name,
        "size": target.stat().st_size,
        "content_type": uploaded.mimetype or mimetypes.guess_type(target.name)[0] or "",
    }


def _parse_optional_media_date(value: str) -> date | None:
    text = str(value or "").strip()
    return datetime.strptime(text, "%Y-%m-%d").date() if text else None


def _workspace_media_local_date(item: dict[str, Any], *, photo: bool = False) -> date:
    raw = item.get("captured_at") if photo else item.get("started_at")
    raw = raw or item.get("created_at")
    fallback = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return _candidate_datetime(raw, fallback=fallback).astimezone(_BEIJING_TZ).date()


def _record_file_analysis(record: dict[str, Any], filename: str) -> tuple[str, str]:
    items = (record.get("analysis") or {}).get("items") or {}
    analysis = items.get(filename) if isinstance(items, dict) else None
    if not isinstance(analysis, dict):
        return "", "not_started"
    return str(analysis.get("transcript", "")).strip(), str(analysis.get("status", "not_started"))


def _list_workspace_admin_media(
    *,
    source_role: str,
    media_type: str,
    start_date: date | None,
    end_date: date | None,
    glass_number: str,
    experiment_type: str,
) -> list[dict[str, Any]]:
    device_glasses = {
        str(glass.get("device_id", "")): str(glass.get("glass_number", ""))
        for glass in list_glasses()
        if glass.get("device_id")
    }
    roles = ("student", "teacher") if source_role == "all" else (source_role,)
    items: list[dict[str, Any]] = []

    for role in roles:
        record_root = _record_root() if role == "student" else _teacher_record_root()
        record_endpoint = "admin_download_record_file" if role == "student" else "teacher_admin_download_record_file"
        if media_type != "photo":
            for record in _list_records(record_root, record_endpoint):
                item_date = _workspace_media_local_date(record)
                item_glass = device_glasses.get(str(record.get("device_id", "")), "")
                if start_date and item_date < start_date:
                    continue
                if end_date and item_date > end_date:
                    continue
                if glass_number and item_glass != glass_number:
                    continue
                if experiment_type and record.get("experiment_type") != experiment_type:
                    continue
                for file_info in record.get("files") or []:
                    filename = str(file_info.get("filename", ""))
                    suffix = Path(filename).suffix.lower()
                    if suffix not in _ALLOWED_RECORD_SUFFIXES:
                        continue
                    item_type = "video" if suffix == ".mp4" else "audio"
                    if media_type != "all" and media_type != item_type:
                        continue
                    transcript, analysis_status = _record_file_analysis(record, filename)
                    items.append({
                        "item_type": item_type,
                        "source_role": role,
                        "record_id": record.get("record_id", ""),
                        "device_id": record.get("device_id", ""),
                        "glass_number": item_glass,
                        "experiment_type": record.get("experiment_type", ""),
                        "created_at": record.get("created_at", ""),
                        "started_at": record.get("started_at", ""),
                        "ended_at": record.get("ended_at", ""),
                        "filename": filename,
                        "size": file_info.get("size", 0),
                        "file_url": file_info.get("file_url", ""),
                        "download_url": file_info.get("download_url", ""),
                        "analysis_status": analysis_status,
                        "transcript": transcript,
                        "sort_at": record.get("started_at") or record.get("created_at", ""),
                    })

        if media_type in {"all", "photo"}:
            photo_root = _photo_root() if role == "student" else _teacher_photo_root()
            photo_endpoint = "admin_photo_file" if role == "student" else "teacher_admin_photo_file"
            for photo in _list_photos(photo_root, photo_endpoint):
                item_date = _workspace_media_local_date(photo, photo=True)
                item_glass = device_glasses.get(str(photo.get("device_id", "")), "")
                if start_date and item_date < start_date:
                    continue
                if end_date and item_date > end_date:
                    continue
                if glass_number and item_glass != glass_number:
                    continue
                if experiment_type and photo.get("experiment_type") != experiment_type:
                    continue
                photo_id = str(photo.get("photo_id", ""))
                items.append({
                    "item_type": "photo",
                    "source_role": role,
                    "photo_id": photo_id,
                    "device_id": photo.get("device_id", ""),
                    "glass_number": item_glass,
                    "experiment_type": photo.get("experiment_type", ""),
                    "created_at": photo.get("created_at", ""),
                    "captured_at": photo.get("captured_at", ""),
                    "filename": photo.get("filename", ""),
                    "size": photo.get("size", 0),
                    "file_url": photo.get("file_url", ""),
                    "download_url": photo.get("download_url", ""),
                    "thumbnail_url": url_for(
                        "workspace_admin_photo_thumbnail",
                        source_role=role,
                        photo_id=photo_id,
                    ),
                    "sort_at": photo.get("captured_at") or photo.get("created_at", ""),
                })

    items.sort(key=lambda item: str(item.get("sort_at", "")), reverse=True)
    for item in items:
        item.pop("sort_at", None)
    return items


def _list_records(root: Path, download_endpoint: str) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent
        try:
            records.append(_record_summary(record_dir, download_endpoint))
        except Exception:
            continue
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records


def _record_summary(record_dir: Path, download_endpoint: str = "admin_download_record_file") -> dict[str, Any]:
    metadata_path = record_dir / "record.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    files = []
    for file_path in sorted(record_dir.iterdir(), key=lambda item: item.name.lower()):
        if not file_path.is_file() or file_path.name == "record.json":
            continue
        file_url = url_for(
            download_endpoint,
            record_id=record_dir.name,
            filename=file_path.name,
        )
        files.append({
            "filename": file_path.name,
            "size": file_path.stat().st_size,
            "file_url": f"{file_url}?download=0",
            "download_url": f"{file_url}?download=1",
        })
    return {
        "record_id": record_dir.name,
        "device_id": metadata.get("device_id", ""),
        "experiment_type": metadata.get("experiment_type", ""),
        "created_at": metadata.get("created_at", ""),
        "started_at": metadata.get("started_at", ""),
        "ended_at": metadata.get("ended_at", ""),
        "files": files,
        "analysis": _load_analysis(record_dir),
        "path": str(record_dir),
    }


def _resolve_record_dir(root: Path, record_id: str) -> Path | None:
    safe_id = _safe_text_field(record_id, "")
    if not safe_id or safe_id != record_id:
        return None
    root = root.resolve()
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent.resolve()
        if record_dir.name == record_id and _is_relative_to(record_dir, root):
            return record_dir
    return None


def _list_photos(root: Path, file_endpoint: str) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    photos = []
    for metadata_path in root.rglob("photo.json"):
        try:
            photos.append(_photo_summary(metadata_path.parent, file_endpoint))
        except Exception:
            continue
    photos.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return photos


def _photo_summary(photo_dir: Path, file_endpoint: str = "admin_photo_file") -> dict[str, Any]:
    metadata_path = photo_dir / "photo.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        raise FileNotFoundError("photo file not found")
    photo_id = str(metadata.get("photo_id") or photo_dir.name)
    file_url = url_for(file_endpoint, photo_id=photo_id)
    return {
        "photo_id": photo_id,
        "device_id": metadata.get("device_id", ""),
        "experiment_type": metadata.get("experiment_type", ""),
        "captured_at": metadata.get("captured_at", ""),
        "created_at": metadata.get("created_at", ""),
        "filename": photo_path.name,
        "size": photo_path.stat().st_size,
        "file_url": file_url,
        "download_url": f"{file_url}?download=1",
        "analysis": _load_analysis(photo_dir),
    }


def _resolve_photo_dir(root: Path, photo_id: str) -> Path | None:
    safe_id = _safe_text_field(photo_id, "")
    if not safe_id or safe_id != photo_id:
        return None
    root = root.resolve()
    for metadata_path in root.rglob("photo.json"):
        photo_dir = metadata_path.parent.resolve()
        if photo_dir.name == photo_id and _is_relative_to(photo_dir, root):
            return photo_dir
    return None


def _send_photo_file(photo_root: Path, photo_id: str):
    photo_dir = _resolve_photo_dir(photo_root, photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        return jsonify({"error": "photo file not found"}), 404
    return send_file(
        photo_path,
        mimetype=mimetypes.guess_type(photo_path.name)[0] or "image/jpeg",
        as_attachment=request.args.get("download") == "1",
        download_name=photo_path.name,
    )


def _load_analysis(media_dir: Path) -> dict[str, Any]:
    path = media_dir / "analysis.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"items": {}}
    except (OSError, json.JSONDecodeError):
        return {"items": {}}


def _list_feedback() -> list[dict[str, Any]]:
    root = _feedback_root()
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path in root.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        item.setdefault("feedback_id", path.stem)
        item.setdefault("title", "")
        item.setdefault("content", "")
        item.setdefault("created_at", "")
        items.append(item)
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def _resolve_feedback_path(feedback_id: str) -> Path | None:
    safe_id = _safe_text_field(feedback_id, "")
    if not safe_id or safe_id != feedback_id:
        return None
    root = _feedback_root().resolve()
    path = (root / f"{feedback_id}.json").resolve()
    if not _is_relative_to(path, root):
        return None
    if not path.exists() or not path.is_file():
        return None
    return path
# 延迟导入，避免循环
import pipeline.common.cv_wrapper as cv_core

def parse_args():
    p = argparse.ArgumentParser(description="Distillation Pipeline Web")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5080)
    p.add_argument("--debug", action="store_true")
    return p.parse_args()

if __name__ == "__main__":
    try:
        from pipeline.safety_check.retriever import reload_db
        reload_db()
    except Exception as exc:
        print(f"[safety_check] warmup skipped: {exc}")
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
