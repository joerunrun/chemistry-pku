from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from flask import Flask, jsonify, render_template_string, request

import core


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CV_detection2 特征算法对比</title>
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
      width: min(1800px, calc(100% - 28px));
      margin: 0 auto;
      padding: 22px 0 40px;
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
    h2 { font-size: 1.08rem; margin-bottom: 10px; }

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
      grid-template-columns: minmax(240px, 1fr) 160px 170px;
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

    input { width: 100%; font: inherit; }
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

    .columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: start;
    }

    .method {
      padding: 14px;
      min-width: 0;
    }

    .result-image {
      display: block;
      width: 100%;
      max-height: 460px;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #f2eee8;
      margin-bottom: 12px;
    }

    .placeholder {
      display: grid;
      min-height: 360px;
      place-items: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: var(--radius);
      background: #fbfaf8;
      margin-bottom: 12px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 9px;
      background: #fbfaf8;
      min-width: 0;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: .8rem;
    }

    .metric strong { font-size: .95rem; }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: .84rem;
      margin-bottom: 12px;
    }

    th, td {
      padding: 6px 5px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      word-break: break-word;
    }

    th { color: var(--muted); }
    .ok { color: var(--ok); font-weight: 700; }
    .bad { color: var(--bad); font-weight: 700; }

    details {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }

    summary {
      cursor: pointer;
      color: var(--accent-dark);
      font-weight: 700;
    }

    pre {
      max-height: 260px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: .78rem;
      line-height: 1.5;
    }

    code { font-family: Consolas, "Courier New", monospace; }

    @media (max-width: 1300px) {
      .columns { grid-template-columns: 1fr; }
      .result-image { max-height: none; }
    }

    @media (max-width: 760px) {
      header { display: block; }
      .toolbar { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <h1>CV_detection2 特征算法对比</h1>
        <p class="subtle">同一张新图并行运行 AKAZE 和 SIFT，横向比较检测框、分数和耗时。</p>
      </div>
      <p class="subtle">逐像素匹配已移除；当前只保留特征点匹配。</p>
    </header>

    <form id="compareForm" class="toolbar panel">
      <label>
        新图片
        <input type="file" name="target_image" accept="image/*" required>
      </label>
      <label>
        max-side
        <input type="number" name="max_side" min="600" max="2200" step="100" value="1200">
      </label>
      <button id="runButton" type="submit">开始对比</button>
      <div id="status" class="status"></div>
    </form>

    <section class="columns">
      <section class="method panel" data-method="akaze">
        <h2>AKAZE</h2>
        <div class="placeholder">等待检测</div>
        <img class="result-image" alt="AKAZE result" hidden>
        <div class="metrics"></div>
        <table><thead><tr><th>目标</th><th>结果</th><th>统计</th></tr></thead><tbody></tbody></table>
        <details><summary>JSON</summary><pre></pre></details>
      </section>

      <section class="method panel" data-method="sift">
        <h2>SIFT</h2>
        <div class="placeholder">等待检测</div>
        <img class="result-image" alt="SIFT result" hidden>
        <div class="metrics"></div>
        <table><thead><tr><th>目标</th><th>结果</th><th>统计</th></tr></thead><tbody></tbody></table>
        <details><summary>JSON</summary><pre></pre></details>
      </section>

    </section>
  </main>

  <script>
    const form = document.getElementById("compareForm");
    const statusEl = document.getElementById("status");
    const runButton = document.getElementById("runButton");

    function metric(label, value) {
      return `<div class="metric"><span>${label}</span><strong>${value ?? "-"}</strong></div>`;
    }

    function methodCard(name) {
      return document.querySelector(`[data-method="${name}"]`);
    }

    function resetCard(card) {
      card.querySelector(".placeholder").hidden = false;
      card.querySelector(".placeholder").textContent = "正在检测...";
      card.querySelector("img").hidden = true;
      card.querySelector(".metrics").innerHTML = "";
      card.querySelector("tbody").innerHTML = "";
      card.querySelector("pre").textContent = "";
    }

    function renderCard(name, payload) {
      const card = methodCard(name);
      const placeholder = card.querySelector(".placeholder");
      const image = card.querySelector("img");
      const metrics = card.querySelector(".metrics");
      const tbody = card.querySelector("tbody");
      const pre = card.querySelector("pre");

      if (!payload || payload.error) {
        placeholder.hidden = false;
        placeholder.textContent = payload?.error || "检测失败";
        image.hidden = true;
        pre.textContent = payload?.error || "";
        return;
      }

      const result = payload.result;
      image.src = payload.annotated_image;
      image.hidden = false;
      placeholder.hidden = true;

      const timings = result.timings || {};
      metrics.innerHTML = [
        metric("总耗时", `${timings.method_total_ms ?? timings.total_ms ?? "-"} ms`),
        metric("提取/准备", `${timings.feature_extract_ms ?? timings.prep_ms ?? "-"} ms`),
        metric("匹配", `${timings.match_total_ms ?? "-"} ms`),
        metric("目标点", result.target_keypoints ?? "-"),
      ].join("");

      tbody.innerHTML = Object.entries(result.detections || {}).map(([label, item]) => {
        const found = Array.isArray(item.box) && item.box.length;
        const status = found
          ? `<span class="ok">found</span><br>${item.box.join(", ")}`
          : `<span class="bad">${item.failure_reason || "not_found"}</span>`;
        const stats = [
          item.good_matches !== undefined ? `good=${item.good_matches}` : "",
          item.inliers !== undefined ? `inliers=${item.inliers}` : "",
          item.inlier_ratio !== undefined ? `ratio=${item.inlier_ratio}` : "",
          item.score !== undefined ? `score=${Number(item.score || 0).toFixed(3)}` : "",
          item.raw_score !== undefined ? `raw=${Number(item.raw_score || 0).toFixed(3)}` : "",
          item.overlap_penalty !== undefined && item.overlap_penalty !== null ? `overlap_penalty=${Number(item.overlap_penalty || 0).toFixed(3)}` : "",
          item.scale !== undefined && item.scale !== null ? `scale=${item.scale}` : "",
          item.reference_id ? `ref=${item.reference_id}` : "",
        ].filter(Boolean).join("<br>");
        return `<tr><td>${label}</td><td>${status}</td><td>${stats}</td></tr>`;
      }).join("");

      pre.textContent = JSON.stringify(result, null, 2);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      statusEl.textContent = "正在并行检测...";
      runButton.disabled = true;
      ["akaze", "sift"].forEach((name) => resetCard(methodCard(name)));

      try {
        const response = await fetch("/api/compare-detect", {
          method: "POST",
          body: new FormData(form),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "检测失败。");
        }
        renderCard("akaze", data.results.akaze);
        renderCard("sift", data.results.sift);
        statusEl.textContent = `完成，总耗时 ${data.total_ms} ms。`;
      } catch (error) {
        statusEl.textContent = error.message || String(error);
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


@app.post("/api/compare-detect")
def compare_detect():
    request_started_at = time.perf_counter()
    uploaded = request.files.get("target_image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少上传图片。"}), 400

    try:
        decode_started_at = time.perf_counter()
        target = core.decode_image(uploaded.read(), "target_image")
        image_decode_ms = core.elapsed_ms(decode_started_at)
        max_side = int(request.form.get("max_side", "1200"))

        jobs: dict[str, Callable[[], dict]] = {
            "akaze": lambda: run_method(
                "akaze",
                target,
                lambda: core.detect_image_relaxed(target, max_side=max_side),
            ),
            "sift": lambda: run_method(
                "sift",
                target,
                lambda: core.detect_image_sift_relaxed(target, max_side=max_side),
            ),
        }
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {name: executor.submit(job) for name, job in jobs.items()}
            results = {name: future.result() for name, future in futures.items()}

        for payload in results.values():
            if payload.get("result"):
                payload["result"]["timings"]["image_decode_ms"] = image_decode_ms

        return jsonify(
            {
                "results": results,
                "image_size": {"width": int(target.shape[1]), "height": int(target.shape[0])},
                "total_ms": core.elapsed_ms(request_started_at),
            }
        )
    except (ValueError, core.CV2Error) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"特征算法检测失败: {exc}"}), 500


def run_method(
    method_name: str,
    target,
    detector: Callable[[], dict],
) -> dict:
    started_at = time.perf_counter()
    try:
        result = detector()
        draw_started_at = time.perf_counter()
        annotated = core.draw_detections(target, result)
        data_url = core.encode_image_data_url(annotated)
        draw_encode_ms = core.elapsed_ms(draw_started_at)
        timings = dict(result.get("timings", {}))
        timings.update(
            {
                "draw_encode_ms": draw_encode_ms,
                "method_total_ms": core.elapsed_ms(started_at),
            }
        )
        result["timings"] = timings
        result["method_name"] = method_name
        return {"annotated_image": data_url, "result": result}
    except Exception as exc:
        return {"error": str(exc), "method_name": method_name}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CV_detection2 three-method comparison app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5072)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
