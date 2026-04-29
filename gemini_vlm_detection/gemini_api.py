from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


COMPONENTS = {
    "voltage_regulator": {
        "color": "#fff200",
        "description": (
            "The external grey power control box with a dial and switch. "
            "Check whether it is on the bench or on a rack. If it is not visible, "
            "return an empty list for this label."
        ),
    },
    "heating_unit": {
        "color": "#ff0000",
        "description": (
            "The heating mantle and the magnetic stirrer base located directly "
            "under the boiling flask."
        ),
    },
    "distillation_core": {
        "color": "#00b050",
        "description": (
            "The boiling flask, the Claisen distillation head, and the thermometer "
            "bulb area. Do not include the full condenser unless it is necessary "
            "to cover the Claisen head connection."
        ),
    },
    "condenser_unit": {
        "color": "#00a2e8",
        "description": (
            "The glass cooling tube, its water hoses, and the supporting clamp. "
            "Do not include receiving flasks unless they overlap the condenser."
        ),
    },
    "receiving_assembly": {
        "color": "#ff7f27",
        "description": (
            "The multi-tail adapter, receiving flasks, and plastic joint clips. "
            "Do not include the full condenser tube."
        ),
    },
    "vacuum_gauge": {
        "color": "#a349a4",
        "description": (
            "The circular meter showing pressure readings, often near the water "
            "pump or regulator. If no circular pressure meter is visible, return "
            "an empty list for this label."
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and draw bbox labels for a vacuum distillation setup."
    )
    parser.add_argument("--image", required=True, help="Input image path.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output image path. Default: bbox_<input_stem><input_suffix>.",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Output JSON path containing absolute bboxes. Default: bbox_<input_stem>.json.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"), # @param ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview"] {"allow-input"),
        help="Gemini model name.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        help="Gemini API key. Defaults to GEMINI_API_KEY or GOOGLE_API_KEY.",
    )
    return parser.parse_args()


def build_prompt() -> str:
    component_lines = "\n".join(
        f'- "{label}": {config["description"]}' for label, config in COMPONENTS.items()
    )
    labels_json = ",\n".join(f'    "{label}": []' for label in COMPONENTS)
    return f"""Detect and provide bounding boxes for these 6 functional components of the vacuum distillation setup.

Components:
{component_lines}

Return only JSON. Do not return Markdown, segmentation masks, polygons, prose, or code fences.

Use this exact schema:
{{
  "boxes": {{
{labels_json}
  }},
  "voltage_regulator_surface": "bench|rack|not_visible|uncertain"
}}

Rules:
1. Every box must be [ymin, xmin, ymax, xmax], normalized to 0-1000.
2. If a component is not visible, return [] for that label.
3. Return one tight bounding box per label, not multiple boxes.
4. The box should cover the whole functional component, but avoid unrelated background.
5. For overlapping glassware, keep "distillation_core", "condenser_unit", and "receiving_assembly" separate according to their descriptions.
"""


def load_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"Gemini did not return a JSON object: {text}") from None
        data = json.loads(cleaned[start : end + 1])

    if isinstance(data, list):
        return convert_list_response(data)
    if not isinstance(data, dict):
        raise ValueError(f"Gemini JSON response is not an object: {text}")
    return data


def convert_list_response(items: list[Any]) -> dict[str, Any]:
    boxes = {label: [] for label in COMPONENTS}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        if label not in boxes:
            continue
        box = item.get("box_2d") or item.get("bbox") or item.get("box")
        boxes[label] = box if isinstance(box, list) else []
    return {"boxes": boxes, "voltage_regulator_surface": "uncertain"}


def normalize_boxes(data: dict[str, Any], width: int, height: int) -> dict[str, list[int]]:
    raw_boxes = data.get("boxes", data)
    if not isinstance(raw_boxes, dict):
        raise ValueError(f"Missing boxes object in Gemini response: {data}")

    result: dict[str, list[int]] = {}
    for label in COMPONENTS:
        raw_box = raw_boxes.get(label, [])
        if raw_box in (None, "", []):
            result[label] = []
            continue
        raw_box = first_box(raw_box)
        if isinstance(raw_box, dict):
            raw_box = dict_box_to_list(raw_box)
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            result[label] = []
            continue

        ymin, xmin, ymax, xmax = [float(value) for value in raw_box]
        if max(abs(ymin), abs(xmin), abs(ymax), abs(xmax)) <= 1.5:
            ymin, ymax = ymin * 1000, ymax * 1000
            xmin, xmax = xmin * 1000, xmax * 1000

        if ymax < ymin:
            ymin, ymax = ymax, ymin
        if xmax < xmin:
            xmin, xmax = xmax, xmin

        abs_ymin = clamp(round(ymin / 1000 * height), 0, height - 1)
        abs_xmin = clamp(round(xmin / 1000 * width), 0, width - 1)
        abs_ymax = clamp(round(ymax / 1000 * height), abs_ymin + 1, height)
        abs_xmax = clamp(round(xmax / 1000 * width), abs_xmin + 1, width)
        result[label] = [abs_ymin, abs_xmin, abs_ymax, abs_xmax]
    return result


def first_box(value: Any) -> Any:
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], (list, dict))
    ):
        return value[0]
    return value


def dict_box_to_list(value: dict[str, Any]) -> list[Any]:
    if {"ymin", "xmin", "ymax", "xmax"}.issubset(value):
        return [value["ymin"], value["xmin"], value["ymax"], value["xmax"]]
    if {"top", "left", "bottom", "right"}.issubset(value):
        return [value["top"], value["left"], value["bottom"], value["right"]]
    if {"y", "x", "height", "width"}.issubset(value):
        return [
            value["y"],
            value["x"],
            float(value["y"]) + float(value["height"]),
            float(value["x"]) + float(value["width"]),
        ]
    return []


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def draw_boxes(image: Image.Image, boxes: dict[str, list[int]]) -> Image.Image:
    output = image.convert("RGB").copy()
    draw = ImageDraw.Draw(output)
    font = ImageFont.load_default()
    line_width = max(3, round(min(output.size) / 350))

    for label, box in boxes.items():
        if not box:
            continue
        ymin, xmin, ymax, xmax = box
        color = COMPONENTS[label]["color"]
        draw.rectangle((xmin, ymin, xmax, ymax), outline=color, width=line_width)

        text_bbox = draw.textbbox((xmin, ymin), label, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        label_top = max(0, ymin - text_height - 6)
        draw.rectangle(
            (xmin, label_top, xmin + text_width + 8, label_top + text_height + 6),
            fill=color,
        )
        draw.text((xmin + 4, label_top + 3), label, fill="#000000", font=font)
    return output


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY, or pass --api-key.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Missing Gemini SDK. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    image_path = Path(args.image)
    output_path = Path(args.output) if args.output else default_output_path(image_path)
    json_output_path = (
        Path(args.json_output) if args.json_output else default_json_output_path(image_path)
    )
    total_started_at = time.perf_counter()

    load_started_at = time.perf_counter()
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        image_load_ms = elapsed_ms(load_started_at)

        client_started_at = time.perf_counter()
        client = genai.Client(api_key=args.api_key)
        client_init_ms = elapsed_ms(client_started_at)

        request_started_at = time.perf_counter()
        response = client.models.generate_content(
            model=args.model,
            contents=[image, build_prompt()],
            config=types.GenerateContentConfig(response_mime_type="application/json",   thinking_config=types.ThinkingConfig(
          thinking_budget=0
        )),
        )
        gemini_request_ms = elapsed_ms(request_started_at)

        postprocess_started_at = time.perf_counter()
        data = load_json_response(response.text or "")
        boxes = normalize_boxes(data, width, height)
        annotated = draw_boxes(image, boxes)
        annotated.save(output_path)
        postprocess_ms = elapsed_ms(postprocess_started_at)

    timings = {
        "image_load_ms": image_load_ms,
        "client_init_ms": client_init_ms,
        "gemini_request_ms": gemini_request_ms,
        "postprocess_ms": postprocess_ms,
        "total_ms": elapsed_ms(total_started_at),
    }
    result = {
        "image": str(image_path),
        "output": str(output_path),
        "model": args.model,
        "image_size": {"width": width, "height": height},
        "timings": timings,
        "boxes": boxes,
        "voltage_regulator_surface": data.get("voltage_regulator_surface", "uncertain"),
        "raw_response": data,
    }
    json_output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def default_output_path(image_path: Path) -> Path:
    suffix = image_path.suffix or ".jpg"
    return image_path.with_name(f"bbox_{image_path.stem}{suffix}")


def default_json_output_path(image_path: Path) -> Path:
    return image_path.with_name(f"bbox_{image_path.stem}.json")


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 1)


if __name__ == "__main__":
    main()
