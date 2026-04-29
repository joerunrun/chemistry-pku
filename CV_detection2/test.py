from __future__ import annotations

import argparse
import time
from typing import Any

import cv2
import numpy as np
from flask import Flask, jsonify, render_template_string, request

import core


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CV Detection2 Match Debug</title>
  <style>
    :root {
      --bg: #f5f1eb;
      --panel: #fff;
      --ink: #202020;
      --muted: #6b625b;
      --line: #d8cec2;
      --accent: #8a4926;
      --accent-dark: #633113;
      --ok: #146c43;
      --bad: #a32222;
      --radius: 8px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    }

    .page {
      width: min(1680px, calc(100% - 28px));
      margin: 0 auto;
      padding: 22px 0 42px;
    }

    h1, h2 { margin: 0; }
    h1 { font-size: 1.55rem; }
    h2 { font-size: 1.08rem; margin-bottom: 10px; }

    .subtle {
      margin: 6px 0 14px;
      color: var(--muted);
      line-height: 1.55;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
    }

    .toolbar {
      display: grid;
      grid-template-columns: 150px 220px minmax(240px, 1fr) 140px 160px;
      align-items: end;
      gap: 12px;
      padding: 14px;
      margin-bottom: 14px;
    }

    label {
      display: grid;
      gap: 6px;
      font-weight: 700;
    }

    input, select {
      width: 100%;
      font: inherit;
    }

    input[type="number"],
    select {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      background: #fff;
    }

    button {
      border: 0;
      border-radius: 7px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }

    button:hover:not(:disabled) { background: var(--accent-dark); }
    button:disabled { cursor: not-allowed; opacity: .55; }

    .status {
      grid-column: 1 / -1;
      min-height: 24px;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(420px, .65fr);
      gap: 14px;
      align-items: start;
    }

    .image-panel { padding: 14px; }

    .result-image {
      display: block;
      width: 100%;
      max-height: calc(100vh - 190px);
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #f2eee8;
    }

    .placeholder {
      display: grid;
      min-height: 540px;
      place-items: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: var(--radius);
      background: #fbfaf8;
    }

    .side {
      display: grid;
      gap: 14px;
    }

    .card { padding: 14px; }

    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 9px;
      background: #fbfaf8;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: .8rem;
    }

    .metric strong { font-size: .95rem; }

    pre {
      max-height: 420px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: .84rem;
      line-height: 1.55;
    }

    .ok { color: var(--ok); font-weight: 700; }
    .bad { color: var(--bad); font-weight: 700; }

    @media (max-width: 1120px) {
      .toolbar, .grid { grid-template-columns: 1fr; }
      .result-image { max-height: none; }
    }
  </style>
</head>
<body>
  <main class="page">
    <h1>CV_detection2 匹配调试</h1>
    <p class="subtle">选择一个参考图的某个装置，上传测试图，查看 good matches 和 RANSAC inliers 的连线。绿色是 inlier，红色是 outlier。</p>

    <form id="debugForm" class="toolbar panel">
      <label>
        算法
        <select name="algorithm" id="algorithm">
          <option value="akaze">AKAZE</option>
          <option value="sift">SIFT</option>
        </select>
      </label>
      <label>
        参考样本
        <select name="sample_id" id="sampleSelect" required></select>
      </label>
      <label>
        测试图
        <input type="file" name="target_image" accept="image/*" required>
      </label>
      <label>
        max-side
        <input type="number" name="max_side" min="600" max="2600" step="100" value="1200">
      </label>
      <button id="runButton" type="submit">查看匹配</button>
      <div id="status" class="status"></div>
    </form>

    <section class="grid">
      <section class="image-panel panel">
        <img id="resultImage" class="result-image" alt="匹配连线" hidden>
        <div id="placeholder" class="placeholder">匹配连线会显示在这里</div>
      </section>

      <aside class="side">
        <section class="card panel">
          <h2>统计</h2>
          <div id="metrics" class="metrics"></div>
        </section>
        <section class="card panel">
          <h2>说明</h2>
          <p class="subtle">
            左边是参考图 mask 区域，右边是测试图。圆点是参与匹配的关键点。
            绿色连线表示 RANSAC 认为符合局部 affine 的内点，红色连线表示被排除的匹配。
          </p>
        </section>
        <section class="card panel">
          <h2>JSON</h2>
          <pre id="jsonResult">等待调试。</pre>
        </section>
      </aside>
    </section>
  </main>

  <script>
    const form = document.getElementById("debugForm");
    const algorithmSelect = document.getElementById("algorithm");
    const sampleSelect = document.getElementById("sampleSelect");
    const statusEl = document.getElementById("status");
    const runButton = document.getElementById("runButton");
    const resultImage = document.getElementById("resultImage");
    const placeholder = document.getElementById("placeholder");
    const metricsEl = document.getElementById("metrics");
    const jsonResult = document.getElementById("jsonResult");

    function metric(label, value, cls = "") {
      return `<div class="metric"><span>${label}</span><strong class="${cls}">${value ?? "-"}</strong></div>`;
    }

    async function loadSamples() {
      const algorithm = algorithmSelect.value;
      const response = await fetch(`/api/samples?algorithm=${algorithm}`);
      const data = await response.json();
      const samples = data.samples || [];
      sampleSelect.innerHTML = samples.map((item) => {
        const text = `${item.label} | ${item.reference_id} | ${item.keypoint_count} kp | ${item.original_filename || ""}`;
        return `<option value="${item.sample_id}">${text}</option>`;
      }).join("");
      if (!samples.length) {
        sampleSelect.innerHTML = '<option value="">没有可用样本</option>';
      }
    }

    algorithmSelect.addEventListener("change", loadSamples);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      statusEl.textContent = "正在计算匹配...";
      runButton.disabled = true;
      resultImage.hidden = true;
      placeholder.hidden = false;
      metricsEl.innerHTML = "";
      jsonResult.textContent = "正在计算。";

      try {
        const response = await fetch("/api/debug-match", {
          method: "POST",
          body: new FormData(form),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "调试失败。");
        }
        resultImage.src = data.match_image;
        resultImage.hidden = false;
        placeholder.hidden = true;
        const s = data.summary;
        metricsEl.innerHTML = [
          metric("算法", s.algorithm),
          metric("标签", s.label),
          metric("raw matches", s.raw_matches),
          metric("good matches", s.good_matches),
          metric("inliers", s.inliers, s.inliers >= 6 ? "ok" : "bad"),
          metric("inlier ratio", s.inlier_ratio, s.inlier_ratio >= 0.25 ? "ok" : "bad"),
          metric("scale", s.scale),
          metric("耗时", `${s.total_ms} ms`),
        ].join("");
        jsonResult.textContent = JSON.stringify(data.summary, null, 2);
        statusEl.textContent = "完成。";
      } catch (error) {
        statusEl.textContent = error.message || String(error);
        jsonResult.textContent = statusEl.textContent;
      } finally {
        runButton.disabled = false;
      }
    });

    loadSamples();
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE_HTML)


@app.get("/api/samples")
def samples():
    algorithm = request.args.get("algorithm", "akaze").strip().lower()
    try:
        metadata, _ = load_db_for_algorithm(algorithm)
        return jsonify({"samples": metadata.get("samples", [])})
    except core.CV2Error as exc:
        return jsonify({"error": str(exc), "samples": []}), 400


@app.post("/api/debug-match")
def debug_match():
    started_at = time.perf_counter()
    uploaded = request.files.get("target_image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少测试图。"}), 400
    try:
        algorithm = request.form.get("algorithm", "akaze").strip().lower()
        sample_id = request.form.get("sample_id", "").strip()
        max_side = int(request.form.get("max_side", core.DEFAULT_MAX_SIDE))
        if not sample_id:
            raise core.CV2Error("缺少 sample_id。")

        target = core.decode_image(uploaded.read(), "target_image")
        result = build_debug_match(
            algorithm=algorithm,
            sample_id=sample_id,
            target=target,
            max_side=max_side,
        )
        result["summary"]["total_ms"] = core.elapsed_ms(started_at)
        return jsonify(result)
    except (ValueError, core.CV2Error) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"调试失败: {exc}"}), 500


def load_db_for_algorithm(algorithm: str) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    if algorithm == "akaze":
        return core.load_descriptor_db()
    if algorithm == "sift":
        return core.load_sift_descriptor_db()
    raise core.CV2Error(f"Unsupported algorithm: {algorithm}")


def build_debug_match(
    *,
    algorithm: str,
    sample_id: str,
    target: np.ndarray,
    max_side: int,
) -> dict[str, Any]:
    metadata, arrays = load_db_for_algorithm(algorithm)
    sample = next((item for item in metadata.get("samples", []) if item["sample_id"] == sample_id), None)
    if sample is None:
        raise core.CV2Error(f"Sample not found: {sample_id}")

    reference = reference_for_sample(sample)
    reference_image = core.read_image(core.IMAGES_DIR / reference["filename"])
    label = sample["label"]

    if algorithm == "akaze":
        target_points, target_descriptors = core.extract_akaze_features(target, max_side=max_side)
        norm_type = cv2.NORM_HAMMING
    elif algorithm == "sift":
        target_points, target_descriptors = core.extract_sift_features(target, max_side=max_side)
        norm_type = cv2.NORM_L2
    else:
        raise core.CV2Error(f"Unsupported algorithm: {algorithm}")

    source_points = arrays[str(sample["points_key"])]
    source_descriptors = arrays[str(sample["descriptors_key"])]
    if target_descriptors is None or len(target_descriptors) < 2:
        raise core.CV2Error("测试图特征点不足。")

    matcher = cv2.BFMatcher(norm_type)
    raw_matches = matcher.knnMatch(source_descriptors, target_descriptors, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < core.LOWE_RATIO * second.distance:
            good_matches.append(first)

    affine = None
    mask = None
    inlier_flags = np.zeros((len(good_matches),), dtype=bool)
    if len(good_matches) >= 3:
        src = np.float32([source_points[match.queryIdx] for match in good_matches])
        dst = np.float32([target_points[match.trainIdx] for match in good_matches])
        affine, mask = cv2.estimateAffinePartial2D(
            src,
            dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=5.0,
            maxIters=2000,
            confidence=0.99,
            refineIters=10,
        )
        if mask is not None:
            inlier_flags = mask.ravel().astype(bool)

    inliers = int(inlier_flags.sum())
    ratio = inliers / max(1, len(good_matches))
    scale = core.affine_scale(affine) if affine is not None else None
    projected_box = (
        core.project_bbox_affine(sample.get("bbox", []), affine, target.shape)
        if affine is not None
        else []
    )

    match_image = draw_match_debug(
        reference_image=reference_image,
        target=target,
        sample=sample,
        source_points=source_points,
        target_points=target_points,
        good_matches=good_matches,
        inlier_flags=inlier_flags,
        projected_box=projected_box,
    )
    return {
        "match_image": core.encode_image_data_url(match_image, quality=88),
        "summary": {
            "algorithm": algorithm,
            "sample_id": sample_id,
            "reference_id": sample["reference_id"],
            "label": label,
            "raw_matches": len(raw_matches),
            "good_matches": len(good_matches),
            "inliers": inliers,
            "inlier_ratio": round(float(ratio), 3),
            "scale": None if scale is None else round(float(scale), 3),
            "projected_box": projected_box,
            "target_keypoints": int(target_points.shape[0]),
            "reference_keypoints": int(source_points.shape[0]),
            "reference_file": reference.get("original_filename", reference.get("filename")),
            "status": classify_status(len(good_matches), inliers, ratio, scale, projected_box),
        },
    }


def reference_for_sample(sample: dict[str, Any]) -> dict[str, Any]:
    annotations = core.load_annotations()
    reference_id = str(sample["reference_id"])
    for reference in annotations.get("references", []):
        if str(reference.get("id")) == reference_id:
            return reference
    raise core.CV2Error(f"Reference not found for sample: {reference_id}")


def classify_status(
    good_matches: int,
    inliers: int,
    ratio: float,
    scale: float | None,
    projected_box: list[int],
) -> str:
    if good_matches < core.MIN_GOOD_MATCHES:
        return "not_enough_good_matches"
    if inliers < core.MIN_INLIERS:
        return "not_enough_inliers"
    if ratio < core.MIN_INLIER_RATIO:
        return "weak_inlier_ratio"
    if scale is None or not (core.MIN_SCALE <= scale <= core.MAX_SCALE):
        return "scale_out_of_range"
    if not projected_box:
        return "projected_box_invalid"
    return "accepted"


def draw_match_debug(
    *,
    reference_image: np.ndarray,
    target: np.ndarray,
    sample: dict[str, Any],
    source_points: np.ndarray,
    target_points: np.ndarray,
    good_matches: list[cv2.DMatch],
    inlier_flags: np.ndarray,
    projected_box: list[int],
) -> np.ndarray:
    bbox = sample.get("bbox", [])
    reference_view = reference_image.copy()
    target_view = target.copy()
    if bbox:
        ymin, xmin, ymax, xmax = bbox
        cv2.rectangle(reference_view, (xmin, ymin), (xmax, ymax), core.COLOR_BGR[sample["label"]], 5)
    if projected_box:
        ymin, xmin, ymax, xmax = projected_box
        cv2.rectangle(target_view, (xmin, ymin), (xmax, ymax), core.COLOR_BGR[sample["label"]], 5)

    max_display_height = 900
    ref_small, ref_scale = resize_to_height(reference_view, max_display_height)
    target_small, target_scale = resize_to_height(target_view, max_display_height)
    height = max(ref_small.shape[0], target_small.shape[0])
    width = ref_small.shape[1] + target_small.shape[1]
    canvas = np.full((height, width, 3), 245, dtype=np.uint8)
    canvas[: ref_small.shape[0], : ref_small.shape[1]] = ref_small
    canvas[: target_small.shape[0], ref_small.shape[1] :] = target_small
    x_offset = ref_small.shape[1]

    draw_matches = good_matches[:250]
    for idx, match in enumerate(draw_matches):
        src_pt = source_points[match.queryIdx]
        dst_pt = target_points[match.trainIdx]
        p1 = (int(round(src_pt[0] * ref_scale)), int(round(src_pt[1] * ref_scale)))
        p2 = (
            x_offset + int(round(dst_pt[0] * target_scale)),
            int(round(dst_pt[1] * target_scale)),
        )
        is_inlier = idx < len(inlier_flags) and bool(inlier_flags[idx])
        color = (0, 190, 0) if is_inlier else (0, 0, 220)
        thickness = 2 if is_inlier else 1
        cv2.line(canvas, p1, p2, color, thickness, cv2.LINE_AA)
        cv2.circle(canvas, p1, 4, color, -1, cv2.LINE_AA)
        cv2.circle(canvas, p2, 4, color, -1, cv2.LINE_AA)

    cv2.putText(canvas, "reference", (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    cv2.putText(canvas, "target", (x_offset + 20, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    return canvas


def resize_to_height(image: np.ndarray, max_height: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    if height <= max_height:
        return image.copy(), 1.0
    scale = max_height / height
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CV_detection2 match debug viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5073)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
