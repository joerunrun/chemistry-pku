from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import Flask, jsonify, render_template_string, request

import detect


BBOX_PATH = Path(__file__).with_name("bbox.json")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CV Detection Viewer</title>
  <style>
    :root {
      --bg: #f5f1eb;
      --panel: #ffffff;
      --ink: #202020;
      --muted: #6c625b;
      --line: #ddd2c7;
      --accent: #8b4a26;
      --accent-dark: #623113;
      --good: #146c43;
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
      width: min(1440px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 42px;
    }

    header {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      margin-bottom: 14px;
    }

    h1, h2, h3 { margin: 0; }
    h1 { font-size: 1.55rem; }
    h2 { font-size: 1.1rem; margin-bottom: 12px; }
    h3 { font-size: 1rem; margin-bottom: 8px; }

    .subtle {
      margin: 6px 0 0;
      color: var(--muted);
      line-height: 1.55;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
    }

    .controls {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      padding: 14px;
      margin-bottom: 14px;
    }

    label {
      display: grid;
      gap: 6px;
      font-weight: 700;
    }

    input {
      width: 100%;
      font: inherit;
    }

    .params {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      grid-column: 1 / -1;
      align-items: end;
    }

    .params label {
      font-weight: 600;
      color: var(--muted);
    }

    input[type="number"] {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
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
    button:disabled { cursor: not-allowed; opacity: 0.55; }

    .status {
      grid-column: 1 / -1;
      min-height: 24px;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(360px, 0.55fr);
      gap: 14px;
      align-items: start;
    }

    .image-panel {
      padding: 14px;
    }

    .result-image {
      display: block;
      width: 100%;
      max-height: calc(100vh - 210px);
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #f2eee8;
    }

    .placeholder {
      display: grid;
      min-height: 460px;
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

    .card {
      padding: 14px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px 10px;
      background: #fbfaf8;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 0.84rem;
    }

    .metric strong {
      font-size: 1rem;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }

    th, td {
      padding: 7px 6px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }

    th { color: var(--muted); font-weight: 700; }

    .score-good { color: var(--good); font-weight: 700; }
    .score-bad { color: var(--bad); font-weight: 700; }

    pre {
      max-height: 320px;
      overflow: auto;
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: 0.86rem;
      line-height: 1.55;
    }

    .algorithm {
      color: var(--muted);
      line-height: 1.65;
      margin: 0;
    }

    @media (max-width: 1050px) {
      header { display: block; }
      .controls, .grid, .params { grid-template-columns: 1fr; }
      .result-image { max-height: none; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <h1>CV Detection Viewer</h1>
        <p class="subtle">上传标注时使用的正确图和一张新图，查看 4 个已标注装置的匹配效果。</p>
      </div>
      <p class="subtle">当前读取：<code>CV_detection/bbox.json</code></p>
    </header>

    <form id="detectForm" class="controls panel">
      <label>
        参考图
        <input type="file" name="reference_image" accept="image/*" required>
      </label>
      <label>
        新图片
        <input type="file" name="target_image" accept="image/*" required>
      </label>
      <div class="params">
        <label>
          max-side
          <input type="number" name="max_side" min="600" max="2600" step="100" value="1600">
        </label>
        <label>
          ROI padding
          <input type="number" name="roi_pad" min="0.1" max="2.0" step="0.05" value="0.65">
        </label>
        <label>
          local score
          <input type="number" name="min_template_score" min="0.0" max="1.0" step="0.01" value="0.34">
        </label>
        <button id="runButton" type="submit">开始检测</button>
      </div>
      <div id="status" class="status"></div>
    </form>

    <section class="grid">
      <section class="image-panel panel">
        <img id="resultImage" class="result-image" alt="检测结果" hidden>
        <div id="placeholder" class="placeholder">检测结果会显示在这里</div>
      </section>

      <aside class="side">
        <section class="card panel">
          <h2>耗时</h2>
          <div id="metrics" class="metrics"></div>
        </section>

        <section class="card panel">
          <h2>目标匹配</h2>
          <table>
            <thead>
              <tr><th>目标</th><th>方法</th><th>分数</th><th>框</th></tr>
            </thead>
            <tbody id="detections"></tbody>
          </table>
        </section>

        <section class="card panel">
          <h2>算法逻辑</h2>
          <p class="algorithm">
            先用 ORB 特征点做整图匹配，通过 RANSAC 估计参考图到新图的单应性矩阵；
            再把人工 bbox 投影到新图，在投影框附近做多尺度模板匹配微调。
            模板匹配使用灰度归一化相关，并混入 Canny 边缘响应，所以局部微调看的是亮度纹理和边缘梯度。
            它不是 SIFT，但思路类似“局部特征匹配”：SIFT 用尺度空间梯度方向直方图描述子，当前实现用更快的 ORB 二进制描述子做全局配准。
          </p>
        </section>

        <section class="card panel">
          <h2>JSON</h2>
          <pre id="jsonResult">等待检测。</pre>
        </section>
      </aside>
    </section>
  </main>

  <script>
    const form = document.getElementById("detectForm");
    const statusEl = document.getElementById("status");
    const runButton = document.getElementById("runButton");
    const resultImage = document.getElementById("resultImage");
    const placeholder = document.getElementById("placeholder");
    const metricsEl = document.getElementById("metrics");
    const detectionsEl = document.getElementById("detections");
    const jsonResult = document.getElementById("jsonResult");

    function metric(label, value) {
      return `<div class="metric"><span>${label}</span><strong>${value ?? "-"}</strong></div>`;
    }

    function renderMetrics(timings, homography) {
      metricsEl.innerHTML = [
        metric("总耗时", `${timings.total_ms} ms`),
        metric("读图", `${timings.image_decode_ms} ms`),
        metric("检测", `${timings.detect_total_ms} ms`),
        metric("配准", `${timings.homography_ms} ms`),
        metric("模板", `${timings.template_total_ms} ms`),
        metric("画框+编码", `${timings.draw_encode_ms} ms`),
        metric("特征点", `${homography.keypoints_reference}/${homography.keypoints_target}`),
        metric("内点", `${homography.inliers} (${homography.inlier_ratio})`),
      ].join("");
    }

    function renderDetections(detections) {
      detectionsEl.innerHTML = Object.entries(detections).map(([label, item]) => {
        const score = item.template_score;
        const scoreText = score === null || score === undefined ? "-" : Number(score).toFixed(3);
        const scoreClass = score === null || score === undefined
          ? ""
          : score >= 0.5 ? "score-good" : "score-bad";
        const box = Array.isArray(item.box) && item.box.length ? item.box.join(", ") : "[]";
        return `<tr>
          <td>${label}</td>
          <td>${item.method}</td>
          <td class="${scoreClass}">${scoreText}</td>
          <td>${box}</td>
        </tr>`;
      }).join("");
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      statusEl.textContent = "正在检测...";
      runButton.disabled = true;
      resultImage.hidden = true;
      placeholder.hidden = false;
      metricsEl.innerHTML = "";
      detectionsEl.innerHTML = "";
      jsonResult.textContent = "正在检测。";

      try {
        const response = await fetch("/api/detect", {
          method: "POST",
          body: new FormData(form),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "检测失败。");
        }

        resultImage.src = data.annotated_image;
        resultImage.hidden = false;
        placeholder.hidden = true;
        renderMetrics(data.result.timings, data.result.homography);
        renderDetections(data.result.detections);
        jsonResult.textContent = JSON.stringify(data.result, null, 2);
        statusEl.textContent = "检测完成。";
      } catch (error) {
        statusEl.textContent = error.message || String(error);
        jsonResult.textContent = statusEl.textContent;
      } finally {
        runButton.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE_HTML)


@app.post("/api/detect")
def detect_endpoint():
    started_at = time.perf_counter()
    try:
        annotation = detect.load_annotation(BBOX_PATH)
        reference_bytes = read_upload("reference_image")
        target_bytes = read_upload("target_image")

        decode_started_at = time.perf_counter()
        reference = decode_image(reference_bytes, "reference_image")
        target = decode_image(target_bytes, "target_image")
        detect.validate_reference_size(reference, annotation, BBOX_PATH)
        image_decode_ms = elapsed_ms(decode_started_at)

        max_side = int(request.form.get("max_side", "1600"))
        roi_pad = float(request.form.get("roi_pad", "0.65"))
        min_template_score = float(request.form.get("min_template_score", "0.34"))

        result = detect.detect_components(
            reference=reference,
            target=target,
            annotation=annotation,
            max_side=max_side,
            roi_pad=roi_pad,
            min_template_score=min_template_score,
            global_template_score=0.46,
        )

        draw_started_at = time.perf_counter()
        annotated = detect.draw_boxes(target, result["boxes"], result["detections"])
        annotated_data_url = encode_image_data_url(annotated)
        draw_encode_ms = elapsed_ms(draw_started_at)

        timings = dict(result.get("timings", {}))
        timings.update(
            {
                "image_decode_ms": image_decode_ms,
                "draw_encode_ms": draw_encode_ms,
                "total_ms": elapsed_ms(started_at),
            }
        )
        result.update(
            {
                "image_size": {"width": int(target.shape[1]), "height": int(target.shape[0])},
                "active_labels": [
                    label
                    for label, box in annotation.get("boxes", {}).items()
                    if label in detect.COMPONENTS and box
                ],
                "timings": timings,
            }
        )
        return jsonify({"annotated_image": annotated_data_url, "result": result})
    except (ValueError, detect.DetectionError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"检测失败: {exc}"}), 500


def read_upload(field_name: str) -> bytes:
    uploaded = request.files.get(field_name)
    if uploaded is None or not uploaded.filename:
        raise ValueError(f"缺少上传文件: {field_name}")
    if not (uploaded.mimetype or "").startswith("image/"):
        raise ValueError(f"{field_name} 不是图片文件。")
    data = uploaded.read()
    if not data:
        raise ValueError(f"{field_name} 内容为空。")
    return data


def decode_image(image_bytes: bytes, field_name: str) -> np.ndarray:
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"{field_name} 图片解码失败。")
    return image


def encode_image_data_url(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    if not ok:
        raise ValueError("结果图编码失败。")
    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CV detection web viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
