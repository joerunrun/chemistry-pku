from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


POINT_NAMES = [
    "top_left",
    "top_center",
    "top_right",
    "middle_left",
    "middle_center",
    "middle_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
]


def load_board_points(path: Path) -> list[list[float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    points = data.get("display_points_normalized")
    if not isinstance(points, list) or len(points) != 9:
        raise ValueError(f"{path} must contain 9 display_points_normalized.")
    return [[float(x), float(y)] for x, y in points]


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise ValueError(f"Could not encode image: {path}")
    path.write_bytes(encoded.tobytes())


def collect_points_interactive(image: np.ndarray, window_max_side: int = 1400) -> list[list[float]]:
    height, width = image.shape[:2]
    scale = min(1.0, window_max_side / max(width, height))
    view = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    points: list[list[float]] = []
    window = "manual nine-point calibration"

    def redraw() -> None:
        canvas = view.copy()
        for index, (x, y) in enumerate(points):
            sx, sy = int(round(x * scale)), int(round(y * scale))
            cv2.drawMarker(
                canvas,
                (sx, sy),
                (0, 255, 255),
                markerType=cv2.MARKER_CROSS,
                markerSize=28,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                str(index + 1),
                (sx + 10, sy - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
        next_name = POINT_NAMES[len(points)] if len(points) < len(POINT_NAMES) else "done"
        cv2.putText(
            canvas,
            f"Click {len(points) + 1}/9: {next_name}.  Backspace=undo, Enter=finish, Esc=quit",
            (20, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.imshow(window, canvas)

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or len(points) >= 9:
            return
        points.append([float(x / scale), float(y / scale)])
        redraw()

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    redraw()
    while True:
        key = cv2.waitKey(50) & 0xFF
        if key in (13, 10) and len(points) == 9:
            break
        if key == 27:
            raise SystemExit("Cancelled.")
        if key in (8, 127) and points:
            points.pop()
            redraw()
    cv2.destroyWindow(window)
    return points


def collect_single_point_interactive(
    image: np.ndarray,
    *,
    title: str,
    point_name: str,
    window_max_side: int = 1400,
) -> list[float]:
    height, width = image.shape[:2]
    scale = min(1.0, window_max_side / max(width, height))
    view = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    point: list[float] | None = None
    window = "manual capture calibration"

    def redraw() -> None:
        canvas = view.copy()
        if point is not None:
            sx, sy = int(round(point[0] * scale)), int(round(point[1] * scale))
            cv2.drawMarker(
                canvas,
                (sx, sy),
                (0, 255, 255),
                markerType=cv2.MARKER_CROSS,
                markerSize=34,
                thickness=3,
                line_type=cv2.LINE_AA,
            )
        cv2.putText(
            canvas,
            f"{title}: click {point_name}. Enter=accept, Backspace=undo, Esc=quit",
            (20, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.imshow(window, canvas)

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        nonlocal point
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        point = [float(x / scale), float(y / scale)]
        redraw()

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    redraw()
    while True:
        key = cv2.waitKey(50) & 0xFF
        if key in (13, 10) and point is not None:
            cv2.destroyWindow(window)
            return point
        if key == 27:
            cv2.destroyWindow(window)
            raise SystemExit("Cancelled.")
        if key in (8, 127):
            point = None
            redraw()


def capture_file_order(path: Path) -> tuple[int, int, str]:
    match = re.match(r"^(left|right)_(\d+)_.*\.(jpg|jpeg|png|webp|bmp)$", path.name, re.IGNORECASE)
    if not match:
        return (99, 99, path.name.lower())
    eye_order = 0 if match.group(1).lower() == "left" else 1
    return (eye_order, int(match.group(2)), path.name.lower())


def list_capture_files(capture_dir: Path) -> dict[str, list[Path]]:
    files = sorted(
        [
            path
            for path in capture_dir.iterdir()
            if path.is_file()
            and re.match(r"^(left|right)_(\d+)_.*\.(jpg|jpeg|png|webp|bmp)$", path.name, re.IGNORECASE)
        ],
        key=capture_file_order,
    )
    grouped = {"left": [], "right": []}
    for path in files:
        eye = path.name.split("_", 1)[0].lower()
        index = int(path.name.split("_", 2)[1])
        if 1 <= index <= 9:
            grouped[eye].append(path)
    for eye in ("left", "right"):
        indices = [int(path.name.split("_", 2)[1]) for path in grouped[eye]]
        if indices != list(range(1, 10)):
            raise ValueError(
                f"{capture_dir} must contain {eye}_1_*.jpg ... {eye}_9_*.jpg; got indices {indices}"
            )
    return grouped


def collect_capture_dir_points(
    capture_dir: Path,
    *,
    window_max_side: int,
) -> tuple[list[list[float]], list[list[float]], int, int, list[str], list[str]]:
    grouped = list_capture_files(capture_dir)
    points_by_eye: dict[str, list[list[float]]] = {"left": [], "right": []}
    files_by_eye: dict[str, list[str]] = {"left": [], "right": []}
    image_width = 0
    image_height = 0
    for eye in ("left", "right"):
        for index, path in enumerate(grouped[eye]):
            image = read_image(path)
            image_height, image_width = image.shape[:2]
            point = collect_single_point_interactive(
                image,
                title=f"{eye} {index + 1}/9 - {path.name}",
                point_name=POINT_NAMES[index],
                window_max_side=window_max_side,
            )
            points_by_eye[eye].append(point)
            files_by_eye[eye].append(path.name)
    return (
        points_by_eye["left"],
        points_by_eye["right"],
        image_width,
        image_height,
        files_by_eye["left"],
        files_by_eye["right"],
    )


def load_points_json(path: Path) -> list[list[float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    points = data.get("camera_points", data)
    if not isinstance(points, list) or len(points) != 9:
        raise ValueError(f"{path} must contain 9 points.")
    return [[float(x), float(y)] for x, y in points]


def save_points_json(path: Path, points: list[list[float]]) -> None:
    payload = {
        "camera_points": points,
        "point_order": POINT_NAMES,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def solve_homography(camera_points_px: list[list[float]], display_points: list[list[float]], width: int, height: int) -> dict[str, Any]:
    src = np.array([[x / width, y / height] for x, y in camera_points_px], dtype=np.float64)
    dst = np.array(display_points, dtype=np.float64)
    h, mask = cv2.findHomography(src, dst, method=0)
    if h is None:
        raise ValueError("Could not solve homography.")
    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), h).reshape(-1, 2)
    errors = np.linalg.norm(projected - dst, axis=1)
    return {
        "H": h,
        "src": src,
        "dst": dst,
        "projected": projected,
        "errors": errors,
        "rms_error": float(math.sqrt(float(np.mean(errors * errors)))),
        "max_error": float(np.max(errors)),
        "inliers": int(mask.sum()) if mask is not None else len(camera_points_px),
    }


def draw_debug(
    image: np.ndarray,
    camera_points_px: list[list[float]],
    result: dict[str, Any],
    output_path: Path,
) -> None:
    canvas = image.copy()
    height, width = image.shape[:2]
    display_points = result["dst"]
    h_inv = np.linalg.inv(result["H"])
    expected_camera = cv2.perspectiveTransform(display_points.reshape(-1, 1, 2), h_inv).reshape(-1, 2)
    for index, ((x, y), expected, err) in enumerate(zip(camera_points_px, expected_camera, result["errors"])):
        px, py = int(round(x)), int(round(y))
        ex, ey = int(round(expected[0] * width)), int(round(expected[1] * height))
        cv2.drawMarker(canvas, (px, py), (0, 255, 255), cv2.MARKER_CROSS, 34, 3, cv2.LINE_AA)
        cv2.circle(canvas, (ex, ey), 13, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(canvas, (px, py), (ex, ey), (0, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"{index + 1} {err:.4f}",
            (px + 12, py - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
    write_image(output_path, canvas)


def matrix_json(matrix: np.ndarray) -> list[list[float]]:
    return [[float(matrix[row, col]) for col in range(3)] for row in range(3)]


def build_calibration_payload(
    *,
    image_path: Path,
    image_width: int,
    image_height: int,
    camera_points_px: list[list[float]],
    display_points: list[list[float]],
    result: dict[str, Any],
) -> dict[str, Any]:
    samples = []
    for point, display, name in zip(camera_points_px, display_points, POINT_NAMES):
        samples.append(
            {
                "name": name,
                "camera_px": [float(point[0]), float(point[1])],
                "camera": [float(point[0] / image_width), float(point[1] / image_height)],
                "display": [float(display[0]), float(display[1])],
                "capture_file": image_path.name,
            }
        )
    eye = {
        "H": matrix_json(result["H"]),
        "rms_error": result["rms_error"],
        "max_error": result["max_error"],
        "inliers": result["inliers"],
        "samples": samples,
    }
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "camera_width": image_width,
        "camera_height": image_height,
        "coordinate_system": "normalized_camera_to_normalized_eye_display",
        "source": "manual_single_image",
        "source_image": str(image_path),
        "point_order": POINT_NAMES,
        "left": eye,
        "right": eye,
    }


def build_dual_calibration_payload(
    *,
    capture_dir: Path,
    image_width: int,
    image_height: int,
    left_points_px: list[list[float]],
    right_points_px: list[list[float]],
    left_files: list[str],
    right_files: list[str],
    display_points: list[list[float]],
    left_result: dict[str, Any],
    right_result: dict[str, Any],
) -> dict[str, Any]:
    def eye_payload(points_px: list[list[float]], files: list[str], result: dict[str, Any]) -> dict[str, Any]:
        samples = []
        for point, display, name, filename in zip(points_px, display_points, POINT_NAMES, files):
            samples.append(
                {
                    "name": name,
                    "camera_px": [float(point[0]), float(point[1])],
                    "camera": [float(point[0] / image_width), float(point[1] / image_height)],
                    "display": [float(display[0]), float(display[1])],
                    "capture_file": filename,
                }
            )
        return {
            "H": matrix_json(result["H"]),
            "rms_error": result["rms_error"],
            "max_error": result["max_error"],
            "inliers": result["inliers"],
            "samples": samples,
        }

    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "camera_width": image_width,
        "camera_height": image_height,
        "coordinate_system": "normalized_camera_to_normalized_eye_display",
        "source": "manual_capture_dir",
        "source_capture_dir": str(capture_dir),
        "point_order": POINT_NAMES,
        "left": eye_payload(left_points_px, left_files, left_result),
        "right": eye_payload(right_points_px, right_files, right_result),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual 9-point RayNeo calibration from one camera image.")
    parser.add_argument("--image", help="One calibration image containing the printed 9 crosses.")
    parser.add_argument("--capture-dir", help="Directory containing left_1_*.jpg ... right_9_*.jpg from the glasses app.")
    parser.add_argument("--points", help="Optional JSON file containing previously clicked camera_points.")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent), help="Output directory.")
    parser.add_argument("--board", default=str(Path(__file__).resolve().parent / "board" / "board_coordinates.json"))
    parser.add_argument("--window-max-side", type=int, default=1400)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    display_points = load_board_points(Path(args.board))

    if args.capture_dir:
        capture_dir = Path(args.capture_dir).resolve()
        (
            left_points,
            right_points,
            width,
            height,
            left_files,
            right_files,
        ) = collect_capture_dir_points(capture_dir, window_max_side=args.window_max_side)
        points_path = output_dir / "manual_capture_points.json"
        points_path.write_text(
            json.dumps(
                {
                    "left_camera_points": left_points,
                    "right_camera_points": right_points,
                    "left_files": left_files,
                    "right_files": right_files,
                    "point_order": POINT_NAMES,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        started = time.perf_counter()
        left_result = solve_homography(left_points, display_points, width, height)
        right_result = solve_homography(right_points, display_points, width, height)
        payload = build_dual_calibration_payload(
            capture_dir=capture_dir,
            image_width=width,
            image_height=height,
            left_points_px=left_points,
            right_points_px=right_points,
            left_files=left_files,
            right_files=right_files,
            display_points=display_points,
            left_result=left_result,
            right_result=right_result,
        )
        calibration_path = output_dir / "calibration_manual.json"
        calibration_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote: {points_path}")
        print(f"wrote: {calibration_path}")
        print(
            f"left rms_error={left_result['rms_error']:.6f}, max_error={left_result['max_error']:.6f}, "
            f"inliers={left_result['inliers']}"
        )
        print(
            f"right rms_error={right_result['rms_error']:.6f}, max_error={right_result['max_error']:.6f}, "
            f"inliers={right_result['inliers']}"
        )
        print(f"elapsed_ms={(time.perf_counter() - started) * 1000:.1f}")
        return

    if not args.image:
        parser.error("Either --image or --capture-dir is required.")

    image_path = Path(args.image).resolve()
    image = read_image(image_path)
    height, width = image.shape[:2]

    if args.points:
        points_path = Path(args.points).resolve()
        camera_points = load_points_json(points_path)
    else:
        camera_points = collect_points_interactive(image, window_max_side=args.window_max_side)
        points_path = output_dir / "manual_points.json"
        save_points_json(points_path, camera_points)

    started = time.perf_counter()
    result = solve_homography(camera_points, display_points, width, height)
    payload = build_calibration_payload(
        image_path=image_path,
        image_width=width,
        image_height=height,
        camera_points_px=camera_points,
        display_points=display_points,
        result=result,
    )

    calibration_path = output_dir / "calibration_manual.json"
    debug_path = output_dir / "manual_calibration_debug.jpg"
    calibration_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    draw_debug(image, camera_points, result, debug_path)

    print(f"wrote: {calibration_path}")
    print(f"wrote: {debug_path}")
    print(f"rms_error={result['rms_error']:.6f}, max_error={result['max_error']:.6f}, inliers={result['inliers']}")
    print(f"elapsed_ms={(time.perf_counter() - started) * 1000:.1f}")


if __name__ == "__main__":
    main()
