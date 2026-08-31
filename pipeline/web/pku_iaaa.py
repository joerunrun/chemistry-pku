"""PKU IAAA short-lived token validation."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import requests


DEFAULT_VALIDATE_URL = "https://iaaa.pku.edu.cn/iaaa/svc/token/validate.do"


def _masked_identity(identity_id: str) -> str:
    if len(identity_id) <= 4:
        return "***"
    return f"{identity_id[:2]}***{identity_id[-2:]}"


def client_ip(*, remote_addr: str | None, x_real_ip: str | None) -> str:
    """Use the address supplied by the trusted Nginx proxy when available."""
    forwarded = (x_real_ip or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return (remote_addr or "0.0.0.0").strip() or "0.0.0.0"


def validate_token(token: str, remote_addr: str) -> dict[str, Any]:
    token = token.strip()
    app_id = os.getenv("PKU_IAAA_APP_ID", "").strip()
    api_key = os.getenv("PKU_IAAA_API_KEY", "").strip()
    validate_url = os.getenv("PKU_IAAA_VALIDATE_URL", DEFAULT_VALIDATE_URL).strip()

    if not token:
        return {"ok": False, "error": "缺少北大认证 token"}
    if not app_id or not api_key:
        return {"ok": False, "error": "服务端尚未配置 PKU_IAAA_APP_ID 或 PKU_IAAA_API_KEY"}

    payload = f"appId={app_id}&remoteAddr={remote_addr}&token={token}"
    msg_abs = hashlib.md5((payload + api_key).encode("utf-8")).hexdigest()
    params = {
        "remoteAddr": remote_addr,
        "appId": app_id,
        "token": token,
        "msgAbs": msg_abs,
    }

    try:
        response = requests.get(validate_url, params=params, timeout=15)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return {"ok": False, "error": "无法完成北大认证校验，请检查网络和服务端配置"}

    if not result.get("success"):
        return {"ok": False, "error": str(result.get("errMsg") or "北大认证校验失败")[:200]}

    user_info = result.get("userInfo")
    if not isinstance(user_info, dict):
        return {"ok": False, "error": "北大返回成功，但缺少 userInfo"}
    identity_id = str(user_info.get("identityId") or "").strip()
    if not identity_id:
        return {"ok": False, "error": "北大返回成功，但缺少 identityId"}

    return {
        "ok": True,
        "identity_id": identity_id,
        "identity_display": _masked_identity(identity_id),
        "user_info": user_info,
    }
