"""Published APKs live outside the source tree, beside other runtime assets."""
import json
from flask import Blueprint, jsonify, send_file, url_for
from pipeline.resource_paths import runtime_asset_path

updates = Blueprint("app_updates", __name__)


@updates.get("/api/glasses/updates/<role>")
def latest(role):
    path = runtime_asset_path("app_updates", role, "latest.json")
    if role not in {"teacher", "student"} or not path.is_file():
        return jsonify({"error": "No published update"}), 404
    info = json.loads(path.read_text(encoding="utf-8"))
    info["download_url"] = url_for("app_updates.apk", role=role, code=info["version_code"])
    response = jsonify(info)
    response.headers["Cache-Control"] = "no-store"
    return response


@updates.get("/api/glasses/updates/<role>/<int:code>.apk")
def apk(role, code):
    path = runtime_asset_path("app_updates", role, f"{role}-{code}.apk")
    if role not in {"teacher", "student"} or not path.is_file():
        return jsonify({"error": "APK not found"}), 404
    return send_file(path, mimetype="application/vnd.android.package-archive", conditional=True)
