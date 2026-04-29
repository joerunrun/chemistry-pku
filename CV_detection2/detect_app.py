from __future__ import annotations

import argparse
import time

from flask import Flask, jsonify, render_template_string, request

import core


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CV Detection2 Viewer</title>
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
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 42px;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 18px;
      margin-bottom: 14px;
    }

    h1, h2 { margin: 0; }
    h1 { font-size: 1.55rem; }
    h2 { font-size: 1.05rem; margin-bottom: 10px; }

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

    .toolbar {
      display: grid;
      grid-template-columns: minmax(240px, 1fr) 180px 160px;
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

    input {
      width: 100%;
      font: inherit;
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
    button:disabled { cursor: not-allowed; opacity: .55; }

    .status {
      grid-column: 1 / -1;
      min-height: 24px;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(430px, .65fr);
      gap: 14px;
      align-items: start;
    }

    .image-panel { padding: 14px; }

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
      min-height: 520px;
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
      padding: 9px 10px;
      background: #fbfaf8;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: .84rem;
    }

    .metric strong { font-size: 1rem; }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: .9rem;
    }

    th, td {
      padding: 7px 6px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }

    th { color: var(--muted); }
    .ok { color: var(--ok); font-weight: 700; }
    .bad { color: var(--bad); font-weight: 700; }

    pre {
      max-height: 320px;
      overflow: auto;
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: .85rem;
      line-height: 1.55;
    }

    .algorithm {
      color: var(--muted);
      line-height: 1.65;
      margin: 0;
    }

    code { font-family: Consolas, "Courier New", monospace; }

    @media (max-width: 1080px) {
      header { display: block; }
      .toolbar, .grid { grid-template-columns: 1fr; }
      .result-image { max-height: none; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <h1>CV_detection2 AKAZE 检测</h1>
        <p class="subtle">不做整图配准；新图整图提一次 AKAZE，再逐装置匹配描述符并做局部 affine 验证。</p>
      </div>
      <p class="subtle">先用 <code>annotate_app.py</code> 入库参考图。</p>
    </header>

    <form id="detectForm" class="toolbar panel">
      <label>
        新图片
        <input type="file" name="target_image" accept="image/*" required>
      </label>
      <label>
        max-side
        <input type="number" name="max_side" min="600" max="2600" step="100" value="1600">
      </label>
      <button id="runButton" type="submit">开始检测</button>
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
          <h2>目标检测</h2>
          <table>
            <thead>
              <tr>
                <th>目标</th>
                <th>状态</th>
                <th>匹配</th>
                <th>框</th>
              </tr>
            </thead>
            <tbody id="detections"></tbody>
          </table>
        </section>

        <section class="card panel">
          <h2>数据库</h2>
          <div id="databaseStats" class="metrics"></div>
        </section>

        <section class="card panel">
          <h2>算法逻辑</h2>
          <p class="algorithm">
            离线只从 mask 内提 AKAZE 特征，避免 bbox 背景参与匹配。
            在线对新图整图提 AKAZE，然后每个装置拿自己的数据库描述符去匹配。
            匹配点通过 RANSAC 估计局部 affine，只有内点数、内点比例、尺度都合理时才画框。
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
    const databaseStatsEl = document.getElementById("databaseStats");
    const jsonResult = document.getElementById("jsonResult");

    function metric(label, value) {
      return `<div class="metric"><span>${label}</span><strong>${value ?? "-"}</strong></div>`;
    }

    function renderMetrics(timings, targetKeypoints) {
      metricsEl.innerHTML = [
        metric("总耗时", `${timings.request_total_ms} ms`),
        metric("解码", `${timings.image_decode_ms} ms`),
        metric("AKAZE提取", `${timings.feature_extract_ms} ms`),
        metric("匹配总计", `${timings.match_total_ms} ms`),
        metric("画框编码", `${timings.draw_encode_ms} ms`),
        metric("新图keypoints", targetKeypoints),
      ].join("");
    }

    function renderDatabase(stats) {
      databaseStatsEl.innerHTML = [
        metric("样本数", stats.sample_count || 0),
        metric("总keypoints", stats.total_keypoints || 0),
      ].join("");
    }

    function renderDetections(detections) {
      detectionsEl.innerHTML = Object.entries(detections).map(([label, item]) => {
        const found = Array.isArray(item.box) && item.box.length;
        const status = found
          ? `<span class="ok">found</span>`
          : `<span class="bad">${item.failure_reason || "not_found"}</span>`;
        const matches = [
          `good=${item.good_matches}`,
          `inliers=${item.inliers}`,
          `ratio=${item.inlier_ratio}`,
          `score=${Number(item.score || 0).toFixed(3)}`,
          item.reference_id ? `ref=${item.reference_id}` : "",
        ].filter(Boolean).join("<br>");
        const box = found ? item.box.join(", ") : "[]";
        return `<tr>
          <td>${label}</td>
          <td>${status}</td>
          <td>${matches}</td>
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
        renderMetrics(data.result.timings, data.result.target_keypoints);
        renderDetections(data.result.detections);
        renderDatabase(data.result.database_stats || {});
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


@app.get("/api/database")
def database():
    return jsonify({"database_stats": core.database_stats()})


@app.post("/api/detect")
def detect_endpoint():
    request_started_at = time.perf_counter()
    uploaded = request.files.get("target_image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少上传图片。"}), 400

    try:
        decode_started_at = time.perf_counter()
        target = core.decode_image(uploaded.read(), "target_image")
        image_decode_ms = core.elapsed_ms(decode_started_at)
        max_side = int(request.form.get("max_side", core.DEFAULT_MAX_SIDE))

        result = core.detect_image(target, max_side=max_side)
        draw_started_at = time.perf_counter()
        annotated = core.draw_detections(target, result)
        data_url = core.encode_image_data_url(annotated)
        draw_encode_ms = core.elapsed_ms(draw_started_at)

        timings = dict(result.get("timings", {}))
        timings.update(
            {
                "image_decode_ms": image_decode_ms,
                "draw_encode_ms": draw_encode_ms,
                "request_total_ms": core.elapsed_ms(request_started_at),
            }
        )
        result.update(
            {
                "image_size": {"width": int(target.shape[1]), "height": int(target.shape[0])},
                "timings": timings,
            }
        )
        return jsonify({"annotated_image": data_url, "result": result})
    except (ValueError, core.CV2Error) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"检测失败: {exc}"}), 500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CV_detection2 detection viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5071)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
