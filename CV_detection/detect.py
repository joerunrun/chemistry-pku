from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np


COMPONENTS: dict[str, dict[str, Any]] = {
    "voltage_regulator": {"name": "voltage_regulator", "color": (0, 242, 255)},
    "heating_unit": {"name": "heating_unit", "color": (0, 0, 255)},
    "distillation_core": {"name": "distillation_core", "color": (80, 176, 0)},
    "condenser_unit": {"name": "condenser_unit", "color": (232, 162, 0)},
    "receiving_assembly": {"name": "receiving_assembly", "color": (39, 127, 255)},
    "vacuum_gauge": {"name": "vacuum_gauge", "color": (164, 73, 163)},
}


class DetectionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect annotated experiment components in a new image using the "
            "reference image and CV_detection/bbox.json."
        )
    )
    parser.add_argument(
        "--reference",
        default=None,
        help=(
            "Reference image used when drawing bbox.json. If omitted, the script "
            "tries bbox.json['image'] relative to the bbox file."
        ),
    )
    parser.add_argument("--target", required=True, help="New image to detect.")
    parser.add_argument(
        "--bbox",
        default=str(Path(__file__).with_name("bbox.json")),
        help="Annotation JSON path. Default: CV_detection/bbox.json.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Annotated output image. Default: detected_<target_name>.",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Detection JSON output. Default: detected_<target_stem>.json.",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=1600,
        help="Working image max side for matching. Larger is slower but more precise.",
    )
    parser.add_argument(
        "--roi-pad",
        type=float,
        default=0.65,
        help="Padding ratio around homography-projected boxes for local template search.",
    )
    parser.add_argument(
        "--min-template-score",
        type=float,
        default=0.34,
        help="Minimum local template score required to replace the homography box.",
    )
    parser.add_argument(
        "--global-template-score",
        type=float,
        default=0.46,
        help="Minimum global template score when homography cannot be estimated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = time.perf_counter()

    bbox_path = Path(args.bbox)
    annotation = load_annotation(bbox_path)
    reference_path = resolve_reference_path(args.reference, bbox_path, annotation)
    target_path = Path(args.target)
    output_path = (
        Path(args.output)
        if args.output
        else target_path.with_name(f"detected_{target_path.name}")
    )
    json_output_path = (
        Path(args.json_output)
        if args.json_output
        else target_path.with_name(f"detected_{target_path.stem}.json")
    )

    load_started_at = time.perf_counter()
    reference = read_image(reference_path)
    target = read_image(target_path)
    validate_reference_size(reference, annotation, reference_path)
    load_ms = elapsed_ms(load_started_at)

    result = detect_components(
        reference=reference,
        target=target,
        annotation=annotation,
        max_side=args.max_side,
        roi_pad=args.roi_pad,
        min_template_score=args.min_template_score,
        global_template_score=args.global_template_score,
    )
    draw_started_at = time.perf_counter()
    annotated = draw_boxes(target, result["boxes"], result["detections"])
    draw_ms = elapsed_ms(draw_started_at)
    write_started_at = time.perf_counter()
    write_image(output_path, annotated)
    write_ms = elapsed_ms(write_started_at)

    timings = dict(result.get("timings", {}))
    timings.update(
        {
            "image_load_ms": load_ms,
            "draw_ms": draw_ms,
            "write_ms": write_ms,
            "total_ms": elapsed_ms(started_at),
        }
    )
    result.update(
        {
            "reference": str(reference_path),
            "target": str(target_path),
            "bbox": str(bbox_path),
            "output": str(output_path),
            "image_size": {"width": int(target.shape[1]), "height": int(target.shape[0])},
            "timings": timings,
        }
    )
    json_output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_annotation(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DetectionError(f"Annotation file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("boxes"), dict):
        raise DetectionError(f"Missing boxes object in {path}")
    if not isinstance(data.get("image_size"), dict):
        raise DetectionError(f"Missing image_size object in {path}")
    return data


def resolve_reference_path(
    explicit_path: str | None,
    bbox_path: Path,
    annotation: dict[str, Any],
) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise DetectionError(f"Reference image not found: {path}")
        return path

    image_name = str(annotation.get("image", "")).strip()
    candidates = [
        bbox_path.with_name(image_name),
        Path.cwd() / image_name,
    ]
    for path in candidates:
        if image_name and path.exists():
            return path
    raise DetectionError(
        "Reference image was not found. Pass it explicitly with --reference."
    )


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise DetectionError(f"Could not read image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".jpg"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise DetectionError(f"Could not encode output image: {path}")
    encoded.tofile(str(path))


def validate_reference_size(
    reference: np.ndarray,
    annotation: dict[str, Any],
    reference_path: Path,
) -> None:
    expected = annotation["image_size"]
    expected_width = int(expected["width"])
    expected_height = int(expected["height"])
    actual_height, actual_width = reference.shape[:2]
    if (actual_width, actual_height) != (expected_width, expected_height):
        raise DetectionError(
            f"Reference image size mismatch for {reference_path}: "
            f"bbox.json expects {expected_width}x{expected_height}, "
            f"actual image is {actual_width}x{actual_height}."
        )


def detect_components(
    *,
    reference: np.ndarray,
    target: np.ndarray,
    annotation: dict[str, Any],
    max_side: int,
    roi_pad: float,
    min_template_score: float,
    global_template_score: float,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    normalize_started_at = time.perf_counter()
    active_boxes = normalize_annotation_boxes(annotation)
    normalize_ms = elapsed_ms(normalize_started_at)

    resize_started_at = time.perf_counter()
    ref_small, ref_scale = resize_for_work(reference, max_side)
    target_small, target_scale = resize_for_work(target, max_side)
    resize_ms = elapsed_ms(resize_started_at)

    homography_started_at = time.perf_counter()
    homography, homography_info = estimate_homography(reference, target, max_side)
    homography_ms = elapsed_ms(homography_started_at)

    boxes: dict[str, list[int]] = {label: [] for label in COMPONENTS}
    detections: dict[str, dict[str, Any]] = {}
    component_timings: dict[str, float] = {}

    for label, ref_box in active_boxes.items():
        component_started_at = time.perf_counter()
        projected_box = []
        if homography is not None:
            projected_box = project_box(ref_box, homography, target.shape)

        match = refine_with_template(
            label=label,
            ref_small=ref_small,
            target_small=target_small,
            ref_box=scale_box(ref_box, ref_scale),
            projected_box=scale_box(projected_box, target_scale) if projected_box else [],
            target_shape=target.shape,
            target_scale=target_scale,
            roi_pad=roi_pad,
            min_score=min_template_score if projected_box else global_template_score,
        )

        if match["box"]:
            boxes[label] = match["box"]
            method = match["method"]
        elif projected_box:
            boxes[label] = projected_box
            method = "homography"
        else:
            boxes[label] = []
            method = "not_found"

        detections[label] = {
            "method": method,
            "template_score": match["score"],
            "reference_box": ref_box,
            "projected_box": projected_box,
            "box": boxes[label],
        }
        component_timings[label] = elapsed_ms(component_started_at)

    return {
        "boxes": boxes,
        "detections": detections,
        "homography": homography_info,
        "timings": {
            "normalize_ms": normalize_ms,
            "resize_ms": resize_ms,
            "homography_ms": homography_ms,
            "template_total_ms": round(sum(component_timings.values()), 1),
            "component_ms": component_timings,
            "detect_total_ms": elapsed_ms(started_at),
        },
    }


def normalize_annotation_boxes(annotation: dict[str, Any]) -> dict[str, list[int]]:
    image_size = annotation["image_size"]
    width = int(image_size["width"])
    height = int(image_size["height"])
    active: dict[str, list[int]] = {}
    for label, raw_box in annotation["boxes"].items():
        if label not in COMPONENTS or not raw_box:
            continue
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            raise DetectionError(f"Invalid box for {label}: {raw_box}")
        ymin, xmin, ymax, xmax = [int(round(float(value))) for value in raw_box]
        ymin, ymax = sorted((clamp(ymin, 0, height - 1), clamp(ymax, 1, height)))
        xmin, xmax = sorted((clamp(xmin, 0, width - 1), clamp(xmax, 1, width)))
        if ymax <= ymin or xmax <= xmin:
            raise DetectionError(f"Empty box for {label}: {raw_box}")
        active[label] = [ymin, xmin, ymax, xmax]
    if not active:
        raise DetectionError("bbox.json contains no non-empty boxes to detect.")
    return active


def resize_for_work(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    longest = max(width, height)
    if longest <= max_side:
        return image.copy(), 1.0
    scale = max_side / longest
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def estimate_homography(
    reference: np.ndarray,
    target: np.ndarray,
    max_side: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    ref_small, ref_scale = resize_for_work(reference, max_side)
    target_small, target_scale = resize_for_work(target, max_side)
    ref_gray = prepare_gray(ref_small)
    target_gray = prepare_gray(target_small)

    orb = cv2.ORB_create(nfeatures=7000, scaleFactor=1.2, nlevels=8, fastThreshold=7)
    kp_ref, desc_ref = orb.detectAndCompute(ref_gray, None)
    kp_target, desc_target = orb.detectAndCompute(target_gray, None)
    info: dict[str, Any] = {
        "estimated": False,
        "keypoints_reference": len(kp_ref or []),
        "keypoints_target": len(kp_target or []),
        "matches": 0,
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
    }
    if desc_ref is None or desc_target is None or len(kp_ref) < 8 or len(kp_target) < 8:
        info["reason"] = "not_enough_keypoints"
        return None, info

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = matcher.knnMatch(desc_ref, desc_target, k=2)
    good = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < 0.75 * second.distance:
            good.append(first)

    info["matches"] = len(raw_matches)
    info["good_matches"] = len(good)
    if len(good) < 20:
        info["reason"] = "not_enough_good_matches"
        return None, info

    pts_ref = np.float32([kp_ref[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_target = np.float32([kp_target[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_ref[:, :, 0] /= ref_scale
    pts_ref[:, :, 1] /= ref_scale
    pts_target[:, :, 0] /= target_scale
    pts_target[:, :, 1] /= target_scale

    homography, mask = cv2.findHomography(pts_ref, pts_target, cv2.RANSAC, 6.0)
    if homography is None or mask is None:
        info["reason"] = "ransac_failed"
        return None, info

    inliers = int(mask.ravel().sum())
    ratio = inliers / max(1, len(good))
    info["inliers"] = inliers
    info["inlier_ratio"] = round(ratio, 3)
    if inliers < 12 or ratio < 0.25:
        info["reason"] = "weak_homography"
        return None, info

    info["estimated"] = True
    info["matrix"] = [[round(float(value), 6) for value in row] for row in homography]
    return homography, info


def prepare_gray(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.equalizeHist(gray)


def project_box(
    box: list[int],
    homography: np.ndarray,
    target_shape: tuple[int, int, int],
) -> list[int]:
    ymin, xmin, ymax, xmax = box
    corners = np.float32(
        [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
    ).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    xs = projected[:, 0]
    ys = projected[:, 1]
    height, width = target_shape[:2]
    out = [
        clamp(math.floor(float(ys.min())), 0, height - 1),
        clamp(math.floor(float(xs.min())), 0, width - 1),
        clamp(math.ceil(float(ys.max())), 1, height),
        clamp(math.ceil(float(xs.max())), 1, width),
    ]
    if out[2] <= out[0] or out[3] <= out[1]:
        return []
    return out


def refine_with_template(
    *,
    label: str,
    ref_small: np.ndarray,
    target_small: np.ndarray,
    ref_box: list[int],
    projected_box: list[int],
    target_shape: tuple[int, int, int],
    target_scale: float,
    roi_pad: float,
    min_score: float,
) -> dict[str, Any]:
    template = crop_box(ref_small, ref_box)
    if template.size == 0:
        return {"box": [], "score": None, "method": "template_invalid"}

    if projected_box:
        roi = pad_box(projected_box, roi_pad, target_small.shape)
    else:
        roi = [0, 0, target_small.shape[0], target_small.shape[1]]
    search = crop_box(target_small, roi)
    if search.size == 0:
        return {"box": [], "score": None, "method": "search_roi_invalid"}

    target_size = (
        projected_box[3] - projected_box[1],
        projected_box[2] - projected_box[0],
    ) if projected_box else (template.shape[1], template.shape[0])
    base_scale = min(
        max(target_size[0] / max(1, template.shape[1]), 0.35),
        2.6,
    )
    scale_steps = [0.82, 0.92, 1.0, 1.08, 1.2] if projected_box else [
        0.65,
        0.8,
        0.95,
        1.1,
        1.3,
    ]

    best: dict[str, Any] = {"score": -1.0, "box": []}
    search_gray = prepare_gray(search)
    search_edges = cv2.Canny(search_gray, 60, 150)

    for step in scale_steps:
        scale = base_scale * step
        width = max(8, round(template.shape[1] * scale))
        height = max(8, round(template.shape[0] * scale))
        if width >= search.shape[1] or height >= search.shape[0]:
            continue
        resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
        tmpl_gray = prepare_gray(resized)
        gray_score, gray_loc = match_template(search_gray, tmpl_gray)

        tmpl_edges = cv2.Canny(tmpl_gray, 60, 150)
        if float(np.count_nonzero(tmpl_edges)) / tmpl_edges.size > 0.01:
            edge_score, _ = match_template(search_edges, tmpl_edges)
            score = 0.72 * gray_score + 0.28 * edge_score
        else:
            score = gray_score

        if score > best["score"]:
            xmin = roi[1] + gray_loc[0]
            ymin = roi[0] + gray_loc[1]
            best = {
                "score": round(float(score), 4),
                "box": [ymin, xmin, ymin + height, xmin + width],
            }

    if not best["box"] or best["score"] < min_score:
        return {
            "box": [],
            "score": None if best["score"] < 0 else best["score"],
            "method": "template_below_threshold",
        }

    output_box = unscale_box(best["box"], target_scale, target_shape)
    return {
        "box": output_box,
        "score": best["score"],
        "method": f"template_refined:{label}",
    }


def match_template(search_gray: np.ndarray, tmpl_gray: np.ndarray) -> tuple[float, tuple[int, int]]:
    result = cv2.matchTemplate(search_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, max_location = cv2.minMaxLoc(result)
    return float(max_value), max_location


def crop_box(image: np.ndarray, box: list[int]) -> np.ndarray:
    if not box:
        return image[0:0, 0:0]
    ymin, xmin, ymax, xmax = box
    return image[ymin:ymax, xmin:xmax]


def scale_box(box: list[int], scale: float) -> list[int]:
    if not box:
        return []
    return [int(round(value * scale)) for value in box]


def unscale_box(
    box: list[int],
    scale: float,
    target_shape: tuple[int, int, int],
) -> list[int]:
    if not box:
        return []
    height, width = target_shape[:2]
    ymin, xmin, ymax, xmax = [int(round(value / scale)) for value in box]
    ymin = clamp(ymin, 0, height - 1)
    ymax = clamp(ymax, ymin + 1, height)
    xmin = clamp(xmin, 0, width - 1)
    xmax = clamp(xmax, xmin + 1, width)
    return [ymin, xmin, ymax, xmax]


def pad_box(box: list[int], ratio: float, shape: tuple[int, int, int]) -> list[int]:
    ymin, xmin, ymax, xmax = box
    height, width = shape[:2]
    box_width = xmax - xmin
    box_height = ymax - ymin
    pad_x = round(box_width * ratio)
    pad_y = round(box_height * ratio)
    return [
        clamp(ymin - pad_y, 0, height - 1),
        clamp(xmin - pad_x, 0, width - 1),
        clamp(ymax + pad_y, 1, height),
        clamp(xmax + pad_x, 1, width),
    ]


def draw_boxes(
    image: np.ndarray,
    boxes: dict[str, list[int]],
    detections: dict[str, dict[str, Any]],
) -> np.ndarray:
    output = image.copy()
    line_width = max(3, round(min(output.shape[:2]) / 350))
    font_scale = max(0.75, min(output.shape[:2]) / 1600)
    for label, box in boxes.items():
        if not box:
            continue
        ymin, xmin, ymax, xmax = box
        color = COMPONENTS.get(label, {}).get("color", (255, 255, 255))
        cv2.rectangle(output, (xmin, ymin), (xmax, ymax), color, line_width)

        score = detections.get(label, {}).get("template_score")
        score_text = "" if score is None else f" {score:.2f}"
        text = f"{label}{score_text}"
        (text_width, text_height), baseline = cv2.getTextSize(
            text,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            max(1, line_width - 1),
        )
        label_top = max(0, ymin - text_height - baseline - 8)
        cv2.rectangle(
            output,
            (xmin, label_top),
            (xmin + text_width + 10, label_top + text_height + baseline + 8),
            color,
            -1,
        )
        cv2.putText(
            output,
            text,
            (xmin + 5, label_top + text_height + 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            max(1, line_width - 1),
            cv2.LINE_AA,
        )
    return output


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 1)


if __name__ == "__main__":
    try:
        main()
    except DetectionError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
