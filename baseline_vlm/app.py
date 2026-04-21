from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from ali_cache_client import AliCacheClient, AliCacheError
from openrouter_cache_client import OpenRouterCacheClient, OpenRouterCacheError


app = Flask(__name__, template_folder="templates", static_folder="static")

ACTIVE_PROVIDER = "openrouter"
DEFAULT_MODELS = {
    "openrouter": "z-ai/glm-5v-turbo",
    "ali": "qwen3.6-flash",
}
client: OpenRouterCacheClient | AliCacheClient | None = None


SYSTEM_PROMPT = """
You are a chemistry lab correction assistant.
Always judge the image according to the shared lab guide.
Identify the main issue first, then explain the reason and correction.
If the image is insufficient, say that clearly.
""".strip()


@dataclass(slots=True)
class CacheSession:
    session_id: str
    model: str
    guide_text: str
    warmed_at: float
    warm_usage: dict[str, int]
    warm_elapsed_ms: float


CACHE_SESSIONS: dict[str, CacheSession] = {}


def require_client() -> OpenRouterCacheClient | AliCacheClient:
    if client is None:
        raise RuntimeError("Client has not been initialized.")
    return client


def sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        default_model=DEFAULT_MODELS[ACTIVE_PROVIDER],
        active_provider=ACTIVE_PROVIDER,
    )


@app.post("/api/warm-cache")
def warm_cache():
    payload = request.get_json(silent=True) or request.form
    model = (
        str(payload.get("model", DEFAULT_MODELS[ACTIVE_PROVIDER])).strip()
        or DEFAULT_MODELS[ACTIVE_PROVIDER]
    )
    guide_text = str(payload.get("guide_text", "")).strip()

    if not guide_text:
        return jsonify({"error": "Shared guide text is required."}), 400

    try:
        result = require_client().warm_text_cache(
            model=model,
            guide_text=guide_text,
            system_prompt=SYSTEM_PROMPT,
        )
    except (OpenRouterCacheError, AliCacheError) as exc:
        return jsonify({"error": str(exc)}), 500

    session_id = uuid.uuid4().hex[:12]
    CACHE_SESSIONS[session_id] = CacheSession(
        session_id=session_id,
        model=model,
        guide_text=guide_text,
        warmed_at=time.time(),
        warm_usage=result["usage"],
        warm_elapsed_ms=result["elapsed_ms"],
    )

    return jsonify(
        {
            "session_id": session_id,
            "provider": ACTIVE_PROVIDER,
            "model": model,
            "message": result["content"],
            "elapsed_ms": result["elapsed_ms"],
            "usage": result["usage"],
        }
    )


@app.post("/api/ask-stream")
def ask_stream() -> Response:
    session_id = (request.form.get("session_id") or "").strip()
    prompt = (request.form.get("prompt") or "").strip()
    image = request.files.get("image")

    session = CACHE_SESSIONS.get(session_id)
    if not session:
        return Response(
            sse_event("error", {"message": "Cache session not found. Warm cache first."}),
            mimetype="text/event-stream",
        )
    if not prompt:
        return Response(
            sse_event("error", {"message": "Prompt is required."}),
            mimetype="text/event-stream",
        )
    if image is None:
        return Response(
            sse_event("error", {"message": "Please upload one image."}),
            mimetype="text/event-stream",
        )

    image_bytes = image.read()
    if not image_bytes:
        return Response(
            sse_event("error", {"message": "Uploaded image is empty."}),
            mimetype="text/event-stream",
        )

    def generate():
        yield sse_event(
            "meta",
            {
                "session_id": session.session_id,
                "provider": ACTIVE_PROVIDER,
                "model": session.model,
                "warm_cached_tokens": session.warm_usage.get("cached_tokens", 0),
                "warm_cache_write_tokens": session.warm_usage.get(
                    "cache_write_tokens", 0
                ),
                "warm_cache_creation_input_tokens": session.warm_usage.get(
                    "cache_creation_input_tokens", 0
                ),
            },
        )
        try:
            for item in require_client().stream_image_question(
                model=session.model,
                guide_text=session.guide_text,
                system_prompt=SYSTEM_PROMPT,
                prompt=prompt,
                image_bytes=image_bytes,
            ):
                event = item.pop("event")
                yield sse_event(event, item)
        except (OpenRouterCacheError, AliCacheError) as exc:
            yield sse_event("error", {"message": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baseline VLM cache demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5051)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--provider",
        choices=["openrouter", "ali"],
        default="openrouter",
        help="Select backend provider.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable Flask reloader. Disabled by default so Ctrl+C exits cleanly.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ACTIVE_PROVIDER = args.provider
    if ACTIVE_PROVIDER == "ali":
        client = AliCacheClient()
    else:
        client = OpenRouterCacheClient()
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=args.reload,
    )
