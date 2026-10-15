"""Read-only external knowledge search shared by QA and video lookup."""
from __future__ import annotations

from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(os.getenv("CHEM_KNOWLEDGE_URL") and os.getenv("CHEM_KNOWLEDGE_TOKEN"))


def _request(method: str, path: str, **kwargs):
    allowed_status = kwargs.pop("allowed_status", ())
    response = requests.request(
        method, os.environ["CHEM_KNOWLEDGE_URL"].rstrip("/") + path,
        headers={"Authorization": "Bearer " + os.environ["CHEM_KNOWLEDGE_TOKEN"], **kwargs.pop("headers", {})},
        timeout=(3, 15), **kwargs,
    )
    if response.status_code not in allowed_status:
        response.raise_for_status()
    return response


@lru_cache(maxsize=1)
def _libraries() -> list[dict]:
    return _request("GET", "/api/v1/knowledge-bases?limit=200").json()["items"]


def search(query: str, *, videos: bool = False, top_k: int = 5) -> list[dict]:
    if not query.strip() or not enabled():
        return []
    try:
        selected = set(filter(None, os.getenv("CHEM_KNOWLEDGE_IDS", "").split(",")))
        scopes = [
            {"knowledge_base_id": item["knowledge_base_id"], "document_types": ["视频"] if videos else []}
            for item in _libraries()
            if (not selected or item["knowledge_base_id"] in selected)
            and (not videos or "视频" in item["document_types"])
        ][:20]
        if not scopes:
            return []
        return _request("POST", "/api/v1/query", json={
            "query": query.strip()[:4000], "knowledge_scopes": scopes, "top_k": top_k,
        }).json()["results"]
    except (requests.RequestException, ValueError, KeyError):
        log.warning("Knowledge search unavailable; continue with existing capabilities")
        return []


def document_path(knowledge_base_id: str, document_id: str) -> str:
    return f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}/documents/{quote(document_id, safe='')}"


def video_candidates(query: str) -> list[dict]:
    mapping_path = os.getenv("CHEM_KNOWLEDGE_VIDEO_MAP", "")
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8")) if mapping_path else {}
    candidates = []
    seen = set()
    for hit in search(query, videos=True):
        kb, doc = hit["knowledge_base_id"], hit["document_id"]
        if (kb, doc) in seen:
            continue
        seen.add((kb, doc))
        stream_url = mapping.get(doc, "")
        if not stream_url and hit.get("download_path"):
            try:
                detail = _request("GET", document_path(kb, doc)).json()
                filename = detail.get("files", {}).get("original", {}).get("file_name", "")
                if filename.lower().endswith(".mp4"):
                    stream_url = f"/api/glasses/demo/videos/knowledge/{quote(kb, safe='')}/{quote(doc, safe='')}/stream"
            except (requests.RequestException, ValueError, KeyError):
                log.warning("Knowledge video file unavailable")
        candidates.append({
            "id": f"knowledge:{kb}:{doc}", "label": hit["title"],
            "filename": hit["title"], "source": "knowledge",
            "knowledge_base_id": kb, "document_id": doc,
            "stream_url": stream_url or None, "playable": bool(stream_url),
        })
    return candidates


def open_video(knowledge_base_id: str, document_id: str, *, method: str, byte_range: str = ""):
    path = document_path(knowledge_base_id, document_id)
    detail = _request("GET", path).json()
    filename = detail.get("files", {}).get("original", {}).get("file_name", "")
    if not filename.lower().endswith(".mp4"):
        raise ValueError("Video media is not available")
    return _request(method, path + "/download?variant=original", stream=True, allowed_status=(416,),
                    headers={"Range": byte_range} if byte_range else {})
