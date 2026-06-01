import argparse
import base64
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "benchmark" / "temp" / "datasets" / "realgui_1k"
DEFAULT_DATASET = DATASET_DIR / "sampled_140.jsonl"
RESULTS_DIR = Path(__file__).resolve().parent / "qiniu_results"

DEFAULT_API_BASE = "https://api.qnaigc.com"

MODEL_REGISTRY: Dict[str, Dict[str, str]] = {
    "openai/gpt-5.5": {"family": "gpt"},
    "openai/gpt-5.4-pro": {"family": "gpt"},
    "openai/gpt-5.4-nano": {"family": "gpt"},
    "openai/gpt-5.4-mini": {"family": "gpt"},
    "gemini-3.1-pro-preview": {"family": "gemini"},
    "gemini3flash": {"family": "gemini"},
    "anthropic/claude-4.7-opus": {"family": "gpt"},
    "claude-4.6-sonnet": {"family": "gpt"},
    "x-ai/grok-4.2-fast-reasoning": {"family": "gpt"},
    "x-ai/grok-4.2-fast-non-reasoning": {"family": "gpt"},
    "doubao-seed-2.0-pro": {"family": "gpt"},
    "doubao-seed-2.0-lite": {"family": "gpt"},
    "doubao-seed-2.0-mini": {"family": "gpt"},
}
ALL_MODELS = list(MODEL_REGISTRY.keys())

GEMINI_CLICK_TOOL = {
    "type": "function",
    "function": {
        "name": "click_at",
        "description": "Click at the specified x,y coordinates on the screen. Coordinates are normalized 0-999.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {
                    "type": "integer",
                    "description": "X coordinate (0-999 normalized).",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate (0-999 normalized).",
                },
            },
            "required": ["x", "y"],
        },
    },
}


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc


def get_image_size(b64: str) -> Tuple[int, int]:
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        return img.width, img.height
    except Exception:
        return 0, 0


def compress_image_to_jpeg(base64_img: str, quality: int = 80) -> str:
    img = Image.open(io.BytesIO(base64.b64decode(base64_img))).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def is_image_size_error(message: str) -> bool:
    lowered = message.lower()
    needles = [
        "image size",
        "image too large",
        "request entity too large",
        "payload too large",
        "body too large",
        "base64",
        "maximum context",
        "context length",
        "too many tokens",
        "413",
    ]
    return any(needle in lowered for needle in needles)


def image_mime_type(base64_img: str) -> str:
    try:
        raw = base64.b64decode(base64_img[:64], validate=False)
    except Exception:
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def bbox_xywh_to_corners(bbox_xywh: List[float]) -> Tuple[float, float, float, float]:
    left, top, width, height = bbox_xywh
    return (
        max(0.0, left),
        max(0.0, top),
        min(1.0, left + width),
        min(1.0, top + height),
    )


def point_in_bbox(px: float, py: float, corners: Tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = corners
    return left <= px <= right and top <= py <= bottom


def qiniu_chat_url() -> str:
    explicit_url = os.getenv("QINIU_API_URL")
    if explicit_url:
        return explicit_url.rstrip("/")

    env_base = os.getenv("QINIU_API_BASE")
    base = env_base or DEFAULT_API_BASE
    if env_base and not is_qiniu_like_base(env_base):
        raise RuntimeError(f"QINIU_API_BASE points to a non-Qiniu endpoint: {env_base}")

    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def is_qiniu_like_base(url: Optional[str]) -> bool:
    if not url:
        return False
    normalized = url.lower()
    return "qnaigc.com" in normalized or "sufy.com" in normalized


def load_qiniu_api_key() -> str:
    api_key = os.getenv("QINIU_API_KEY")
    if api_key:
        return api_key

    anthropic_base = os.getenv("ANTHROPIC_BASE_URL")
    anthropic_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    if anthropic_token and is_qiniu_like_base(anthropic_base):
        return anthropic_token

    env_path = ROOT / "grounding" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "QINIU_API_KEY":
                return value.strip().strip('"').strip("'")

    raise RuntimeError(
        "QINIU_API_KEY is not set. Refusing to reuse ANTHROPIC_AUTH_TOKEN because "
        f"ANTHROPIC_BASE_URL is {anthropic_base!r}, not a Qiniu/Sufy endpoint."
    )


def qiniu_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Authentication": f"Bearer {api_key}",
        "X-API-Key": api_key,
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


def gpt_prompt(width: int, height: int, instruction: str) -> str:
    coord_instruction = (
        f"The image resolution is {width}px x {height}px.\n"
        "Provide absolute pixel coordinates.\n"
        f"For example: top-left is (0, 0), center is ({width // 2}, {height // 2}), "
        f"bottom-right is ({width}, {height})."
    )
    return (
        "You are a GUI grounding agent. Look at the screenshot and choose the best click point "
        f"for this instruction: {instruction}\n\n"
        f"{coord_instruction}\n\n"
        'Return compact JSON only: {"x": XXXX, "y": YYYY, "confidence": 0.ZZ}'
    )


def gemini_prompt(instruction: str) -> str:
    return (
        "You are a GUI grounding agent. Look at the screenshot and click the best target for "
        f"this instruction: {instruction}\n\n"
        "Use the click_at tool. Coordinates are normalized 0-999."
    )


def call_qiniu_chat(payload: Dict[str, Any], api_key: str, timeout: int, max_retry: int) -> Dict[str, Any]:
    url = qiniu_chat_url()
    for attempt in range(1, max_retry + 1):
        try:
            resp = requests.post(url, headers=qiniu_headers(api_key), json=payload, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504) and attempt < max_retry:
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"Qiniu HTTP {resp.status_code}: {resp.text[:500]}")
        except requests.RequestException as exc:
            if attempt < max_retry:
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"Qiniu request failed: {exc}") from exc
    return {}


def message_text(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*?\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def normalize_point(x: float, y: float, width: int, height: int, mode: str = "auto") -> Tuple[float, float]:
    if mode == "pixel":
        nx = x / width if width > 0 else 0.0
        ny = y / height if height > 0 else 0.0
    elif mode == "normalized_999":
        nx, ny = x / 999, y / 999
    elif 0 <= x <= 1 and 0 <= y <= 1:
        nx, ny = x, y
    elif width > 0 and height > 0 and (x > 1000 or y > 1000):
        nx, ny = x / width, y / height
    else:
        nx, ny = x / 1000, y / 1000
    return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))


def parse_point(text: str, width: int, height: int, mode: str = "auto") -> Optional[Tuple[float, float]]:
    obj = extract_json_object(text)
    if obj:
        candidates = [
            (obj.get("x"), obj.get("y")),
            (obj.get("cx"), obj.get("cy")),
            (obj.get("left"), obj.get("top")),
        ]
        coord = obj.get("coordinate") or obj.get("point") or obj.get("click")
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            candidates.insert(0, (coord[0], coord[1]))

        for x, y in candidates:
            if x is None or y is None:
                continue
            try:
                return normalize_point(float(x), float(y), width, height, mode)
            except (TypeError, ValueError):
                continue

    patterns = [
        r"left_click\(\s*start_box\s*=\s*[\"']?\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]",
        r"\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]",
        r"\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)",
        r"x\s*[:=]\s*([0-9.]+).*?y\s*[:=]\s*([0-9.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_point(float(match.group(1)), float(match.group(2)), width, height, mode)

    return None


def parse_tool_point(message: Dict[str, Any], width: int, height: int) -> Optional[Tuple[float, float]]:
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function", {})
        raw_args = function.get("arguments", {})
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            continue
        try:
            return normalize_point(float(x), float(y), width, height, mode="normalized_999")
        except (TypeError, ValueError):
            continue
    return None


def parse_partial_json_point(text: str, width: int, height: int, mode: str) -> Optional[Tuple[float, float]]:
    mx = re.search(r'"x"\s*:\s*([0-9.]+)', text)
    my = re.search(r'"y"\s*:\s*([0-9.]+)', text)
    if not mx or not my:
        return None
    return normalize_point(float(mx.group(1)), float(my.group(1)), width, height, mode)


def build_payload(
    model: str,
    family: str,
    screenshot_b64: str,
    instruction: str,
    width: int,
    height: int,
    max_tokens: int,
) -> Dict[str, Any]:
    mime = image_mime_type(screenshot_b64)
    if family == "gpt":
        prompt = gpt_prompt(width, height, instruction)
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise GUI grounding assistant."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"}},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }

    prompt = gemini_prompt(instruction)
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise GUI grounding assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"}},
                ],
            },
        ],
        "tools": [GEMINI_CLICK_TOOL],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": max_tokens,
    }


def call_with_optional_jpeg_fallback(
    model: str,
    family: str,
    screenshot_b64: str,
    instruction: str,
    width: int,
    height: int,
    max_tokens: int,
    api_key: str,
    timeout: int,
    retry: int,
) -> Tuple[Dict[str, Any], bool]:
    payload = build_payload(model, family, screenshot_b64, instruction, width, height, max_tokens)
    try:
        return call_qiniu_chat(payload, api_key, timeout=timeout, max_retry=retry), False
    except RuntimeError as exc:
        if not is_image_size_error(str(exc)):
            raise

    compressed_b64 = compress_image_to_jpeg(screenshot_b64, quality=80)
    payload = build_payload(model, family, compressed_b64, instruction, width, height, max_tokens)
    return call_qiniu_chat(payload, api_key, timeout=timeout, max_retry=retry), True


def predict_qiniu(
    model: str,
    family: str,
    screenshot_b64: str,
    instruction: str,
    api_key: str,
    max_tokens: int,
    timeout: int,
    empty_retries: int,
    retry: int,
) -> Dict[str, Any]:
    width, height = get_image_size(screenshot_b64)
    response: Dict[str, Any] = {}
    choice: Dict[str, Any] = {}
    message: Dict[str, Any] = {}
    text = ""
    used_jpeg_fallback = False
    for retry_idx in range(empty_retries + 1):
        response, used_jpeg = call_with_optional_jpeg_fallback(
            model,
            family,
            screenshot_b64,
            instruction,
            width,
            height,
            max_tokens,
            api_key,
            timeout,
            retry,
        )
        used_jpeg_fallback = used_jpeg_fallback or used_jpeg
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        text = message_text(message)
        if text or message.get("tool_calls") or retry_idx >= empty_retries:
            break
        time.sleep(1 + retry_idx)

    finish_reason = choice.get("finish_reason")
    usage = response.get("usage")
    if family in {"gemini", "claude"}:
        point = parse_tool_point(message, width, height)
        if point is None:
            point = parse_point(text, width, height, mode="normalized_999")
    else:
        point = parse_tool_point(message, width, height)
        if point is None:
            point = parse_point(text, width, height, mode="pixel")
        if point is None and text:
            point = parse_partial_json_point(text, width, height, mode="pixel")
    return {
        "point": point,
        "raw_response": text[:1000],
        "raw_message": json.dumps(message, ensure_ascii=False)[:4000],
        "finish_reason": finish_reason,
        "usage": usage,
        "request_image_size": [width, height],
        "empty_retries": empty_retries,
        "used_jpeg_fallback": used_jpeg_fallback,
    }


def eval_one(
    obj: Dict[str, Any],
    model: str,
    family: str,
    api_key: str,
    max_tokens: int,
    timeout: int,
    empty_retries: int,
    retry: int,
) -> Dict[str, Any]:
    b64 = obj.get("base64_img", "")
    instruction = obj.get("instruction", "")
    bbox_xywh = obj.get("bbox")

    if not isinstance(b64, str) or not b64:
        return {"hit": 0, "skipped": 1, "error": "missing base64_img"}
    if not isinstance(bbox_xywh, list) or len(bbox_xywh) != 4:
        return {"hit": 0, "skipped": 1, "error": "missing/invalid bbox"}

    bbox_values = [float(v) for v in bbox_xywh]
    gt_left, gt_top, gt_w, gt_h = bbox_values
    gt_center = [gt_left + gt_w / 2, gt_top + gt_h / 2]
    gt_corners = bbox_xywh_to_corners(bbox_values)

    try:
        pred = predict_qiniu(model, family, b64, instruction, api_key, max_tokens, timeout, empty_retries, retry)
    except Exception as exc:
        return {
            "hit": 0,
            "skipped": 1,
            "error": str(exc)[:500],
            "instruction": instruction,
            "bbox_xywh": bbox_values,
            "gt_center": gt_center,
            "pred_center": None,
            "raw_response": "",
            "raw_message": "",
            "finish_reason": None,
            "usage": None,
            "request_image_size": None,
            "empty_retries": empty_retries,
            "used_jpeg_fallback": False,
        }

    point = pred["point"]
    if point is None:
        return {
            "hit": 0,
            "skipped": 1,
            "error": "no coordinate returned",
            "instruction": instruction,
            "bbox_xywh": bbox_values,
            "gt_center": gt_center,
            "pred_center": None,
            "raw_response": pred["raw_response"],
            "raw_message": pred.get("raw_message"),
            "finish_reason": pred.get("finish_reason"),
            "usage": pred.get("usage"),
            "request_image_size": pred.get("request_image_size"),
            "empty_retries": pred.get("empty_retries"),
            "used_jpeg_fallback": pred.get("used_jpeg_fallback"),
        }

    px, py = point
    ok = point_in_bbox(px, py, gt_corners)
    return {
        "hit": 1 if ok else 0,
        "skipped": 0,
        "instruction": instruction,
        "bbox_xywh": bbox_values,
        "gt_center": gt_center,
        "pred_center": [px, py],
        "raw_response": pred["raw_response"],
        "raw_message": pred.get("raw_message"),
        "finish_reason": pred.get("finish_reason"),
        "usage": pred.get("usage"),
        "request_image_size": pred.get("request_image_size"),
        "empty_retries": pred.get("empty_retries"),
        "used_jpeg_fallback": pred.get("used_jpeg_fallback"),
    }


def eval_batch(
    objs: List[Dict[str, Any]],
    model: str,
    family: str,
    api_key: str,
    workers: int,
    max_tokens: int,
    timeout: int,
    empty_retries: int,
    retry: int,
) -> Dict[str, Any]:
    total = len(objs)
    hit = 0
    skipped = 0
    indexed_results: Dict[int, Dict[str, Any]] = {}

    def process(idx: int, obj: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        time.sleep(idx * 0.05)
        return idx, eval_one(obj, model, family, api_key, max_tokens, timeout, empty_retries, retry)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process, idx, obj): idx for idx, obj in enumerate(objs)}
        for future in as_completed(futures):
            idx, result = future.result()
            hit += result["hit"]
            skipped += result["skipped"]
            indexed_results[idx] = result
            status = "HIT" if result["hit"] else "MISS" if result.get("pred_center") else "SKIP"
            print(f"  [{idx + 1}/{total}] {status} {result.get('error', '')}")

    results = [indexed_results[idx] for idx in range(total) if idx in indexed_results]
    used = max(1, total - skipped)
    accuracy_total = hit / max(1, total)
    accuracy_used = hit / used
    return {
        "summary": {
            "total": total,
            "used": used,
            "skipped": skipped,
            "hit": hit,
            "accuracy": accuracy_total,
            "accuracy_total": accuracy_total,
            "accuracy_used": accuracy_used,
            "bbox_format": "normalized_xywh",
            "image_transport": "original_base64",
            "max_tokens": max_tokens,
            "timeout": timeout,
            "empty_retries": empty_retries,
            "retry": retry,
        },
        "results": results,
    }


def model_output_dir(output_dir: Path, model: str) -> Path:
    return output_dir / model.replace("/", "_").replace(":", "_")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GPT/Gemini/Claude via Qiniu OpenAI-compatible API.")
    parser.add_argument("--model", type=str, default=None, help="Model name to evaluate.")
    parser.add_argument("--all", action="store_true", help="Evaluate all registered Qiniu models.")
    parser.add_argument("--list", action="store_true", help="List registered models.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="JSONL dataset path.")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N samples.")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent request count.")
    parser.add_argument("--max-tokens", type=int, default=1024, help="Maximum output tokens for each model response.")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP request timeout in seconds.")
    parser.add_argument("--retry", type=int, default=3, help="HTTP retry count for transient errors.")
    parser.add_argument("--empty-retries", type=int, default=2, help="Retry this many times when the API returns empty content.")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR, help="Directory for result JSON files.")
    args = parser.parse_args()

    if args.list:
        for model, cfg in MODEL_REGISTRY.items():
            print(f"{model:36s} family={cfg['family']}")
        return

    if args.all:
        models = ALL_MODELS
    elif args.model:
        if args.model not in MODEL_REGISTRY:
            print(f"Unknown model: {args.model}")
            print(f"Available models: {ALL_MODELS}")
            sys.exit(1)
        models = [args.model]
    else:
        print("Use --model <MODEL> or --all")
        print(f"Available models: {ALL_MODELS}")
        sys.exit(1)

    if not args.dataset.exists():
        print(f"Dataset not found: {args.dataset}")
        sys.exit(1)

    api_key = load_qiniu_api_key()
    objs = list(iter_jsonl(args.dataset))
    if args.limit:
        objs = objs[: args.limit]

    print(f"Dataset: {args.dataset} ({len(objs)} samples)")
    print(f"Qiniu endpoint: {qiniu_chat_url()}")
    print(f"Workers: {args.workers}")
    print(f"Max tokens: {args.max_tokens}")
    print(f"Timeout: {args.timeout}s")
    print(f"Retry: {args.retry}")
    print(f"Empty retries: {args.empty_retries}")

    for model in models:
        family = MODEL_REGISTRY[model]["family"]
        print(f"\n{'=' * 60}")
        print(f"Model: {model} (family={family})")
        print(f"{'=' * 60}")

        result = eval_batch(
            objs,
            model,
            family,
            api_key,
            workers=args.workers,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            empty_retries=args.empty_retries,
            retry=args.retry,
        )
        summary = result["summary"]
        print(
            f"\nSummary: total={summary['total']} used={summary['used']} "
            f"skipped={summary['skipped']} hit={summary['hit']} "
            f"accuracy={summary['accuracy']:.4f} ({summary['hit']}/{summary['total']}) "
            f"accuracy_used={summary['accuracy_used']:.4f} ({summary['hit']}/{summary['used']})"
        )

        out_dir = model_output_dir(args.output_dir, model)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "results.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
