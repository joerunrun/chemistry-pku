from __future__ import annotations

import argparse
import json

from flask import Flask, jsonify, render_template_string, request

import core


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CV Detection2 Mask Annotator</title>
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
      --warn: #9a6700;
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

    h1, h2, h3 { margin: 0; }
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
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      padding: 14px;
      margin-bottom: 14px;
    }

    button {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px 12px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      cursor: pointer;
    }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 700;
    }

    button.primary:hover:not(:disabled) { background: var(--accent-dark); }
    button:disabled { cursor: not-allowed; opacity: .5; }

    .status {
      min-height: 24px;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 14px;
      align-items: start;
    }

    .canvas-wrap {
      padding: 10px;
      overflow: auto;
      min-height: 520px;
      max-height: calc(100vh - 190px);
    }

    canvas {
      display: block;
      max-width: 100%;
      height: auto;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #f2eee8;
      cursor: crosshair;
    }

    .empty {
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

    .card {
      padding: 14px;
    }

    .current {
      margin: 0 0 10px;
      padding: 10px 11px;
      border-radius: 7px;
      background: #f8f0e8;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .labels {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
    }

    .label-row {
      display: grid;
      grid-template-columns: 16px 1fr auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      text-align: left;
    }

    .label-row.active {
      border-color: var(--accent);
      background: #fff7ef;
    }

    .swatch {
      width: 14px;
      height: 14px;
      border-radius: 3px;
      border: 1px solid rgba(0,0,0,.25);
    }

    .chip {
      color: var(--muted);
      font-size: .82rem;
    }

    .chip.done { color: var(--ok); }
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .actions .wide { grid-column: 1 / -1; }

    .help {
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.6;
      font-size: .94rem;
    }

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
    code { font-family: Consolas, "Courier New", monospace; }

    @media (max-width: 1080px) {
      header { display: block; }
      .layout { grid-template-columns: 1fr; }
      .canvas-wrap { max-height: none; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <h1>CV_detection2 Mask 标注入库</h1>
        <p class="subtle">上传正确图，给每个装置点选一个或多个多边形 mask；保存后立即提取 AKAZE 描述符。</p>
      </div>
      <p class="subtle">数据目录：<code>CV_detection2/data/</code></p>
    </header>

    <section class="toolbar panel">
      <input id="imageInput" type="file" accept="image/*">
      <button id="finishButton" type="button" disabled>完成当前多边形</button>
      <button id="undoPointButton" type="button" disabled>撤销点</button>
      <button id="saveButton" class="primary" type="button" disabled>保存并建库</button>
      <span id="status" class="status"></span>
    </section>

    <section class="layout">
      <section class="panel canvas-wrap">
        <canvas id="canvas" hidden></canvas>
        <div id="emptyState" class="empty">先选择一张正确参考图</div>
      </section>

      <aside class="side">
        <section class="card panel">
          <p id="currentLabel" class="current">当前：未加载图片</p>
          <div id="labels" class="labels"></div>
          <div class="actions">
            <button id="previousButton" type="button" disabled>上一个标签</button>
            <button id="nextButton" type="button" disabled>下一个标签</button>
            <button id="clearLabelButton" type="button" disabled>清空当前标签</button>
            <button id="clearDraftButton" type="button" disabled>清空当前点</button>
            <button id="clearAllButton" class="wide" type="button" disabled>清空全部</button>
          </div>
          <p class="help">
            在图上连续点击形成多边形，至少 3 个点后点“完成当前多边形”。
            同一个装置可以画多个区域；不画的标签会保存为空 mask。
          </p>
        </section>

        <section class="card panel">
          <h2>已入库参考图</h2>
          <table>
            <thead>
              <tr><th>ID</th><th>文件</th><th>装置</th><th>操作</th></tr>
            </thead>
            <tbody id="referencesTable"></tbody>
          </table>
        </section>
      </aside>
    </section>
  </main>

  <script>
    const COMPONENTS = {{ components | tojson }};
    const imageInput = document.getElementById("imageInput");
    const canvas = document.getElementById("canvas");
    const ctx = canvas.getContext("2d");
    const emptyState = document.getElementById("emptyState");
    const finishButton = document.getElementById("finishButton");
    const undoPointButton = document.getElementById("undoPointButton");
    const saveButton = document.getElementById("saveButton");
    const statusEl = document.getElementById("status");
    const currentLabelEl = document.getElementById("currentLabel");
    const labelsEl = document.getElementById("labels");
    const previousButton = document.getElementById("previousButton");
    const nextButton = document.getElementById("nextButton");
    const clearLabelButton = document.getElementById("clearLabelButton");
    const clearDraftButton = document.getElementById("clearDraftButton");
    const clearAllButton = document.getElementById("clearAllButton");
    const referencesTable = document.getElementById("referencesTable");

    const state = {
      file: null,
      image: null,
      activeIndex: 0,
      polygons: {},
      draft: [],
    };

    for (const item of COMPONENTS) {
      state.polygons[item.label] = [];
    }

    function setStatus(text, error = false) {
      statusEl.textContent = text;
      statusEl.style.color = error ? "#9f1d1d" : "var(--accent-dark)";
    }

    function activeComponent() {
      return COMPONENTS[state.activeIndex];
    }

    function totalPolygons() {
      return Object.values(state.polygons).reduce((sum, items) => sum + items.length, 0);
    }

    function pointFromEvent(event) {
      const rect = canvas.getBoundingClientRect();
      const sx = canvas.width / rect.width;
      const sy = canvas.height / rect.height;
      return [
        Math.max(0, Math.min(canvas.width - 1, Math.round((event.clientX - rect.left) * sx))),
        Math.max(0, Math.min(canvas.height - 1, Math.round((event.clientY - rect.top) * sy))),
      ];
    }

    function hexToRgba(hex, alpha) {
      const value = hex.replace("#", "");
      const r = parseInt(value.slice(0, 2), 16);
      const g = parseInt(value.slice(2, 4), 16);
      const b = parseInt(value.slice(4, 6), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function drawPolygon(points, color, fill = true) {
      if (!points.length) return;
      ctx.beginPath();
      ctx.moveTo(points[0][0], points[0][1]);
      for (const point of points.slice(1)) {
        ctx.lineTo(point[0], point[1]);
      }
      if (fill && points.length >= 3) {
        ctx.closePath();
        ctx.fillStyle = hexToRgba(color, 0.22);
        ctx.fill();
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(3, Math.round(Math.min(canvas.width, canvas.height) / 450));
      ctx.stroke();
      for (const point of points) {
        ctx.beginPath();
        ctx.arc(point[0], point[1], Math.max(4, ctx.lineWidth + 1), 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }
    }

    function renderCanvas() {
      if (!state.image) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(state.image, 0, 0);
      for (const item of COMPONENTS) {
        for (const polygon of state.polygons[item.label]) {
          drawPolygon(polygon, item.color, true);
        }
      }
      if (state.draft.length) {
        drawPolygon(state.draft, activeComponent().color, false);
      }
    }

    function renderLabels() {
      labelsEl.innerHTML = "";
      for (const [index, item] of COMPONENTS.entries()) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `label-row${index === state.activeIndex ? " active" : ""}`;
        button.addEventListener("click", () => {
          state.activeIndex = index;
          state.draft = [];
          render();
        });

        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = item.color;

        const name = document.createElement("span");
        name.textContent = `${index + 1}. ${item.label}`;

        const chip = document.createElement("span");
        const count = state.polygons[item.label].length;
        chip.className = `chip${count ? " done" : ""}`;
        chip.textContent = `${count} 区域`;

        button.append(swatch, name, chip);
        labelsEl.appendChild(button);
      }
    }

    function render() {
      const loaded = Boolean(state.image);
      canvas.hidden = !loaded;
      emptyState.hidden = loaded;
      currentLabelEl.textContent = loaded ? `当前：${activeComponent().label}` : "当前：未加载图片";
      finishButton.disabled = !loaded || state.draft.length < 3;
      undoPointButton.disabled = !loaded || !state.draft.length;
      saveButton.disabled = !loaded || totalPolygons() === 0;
      previousButton.disabled = !loaded || state.activeIndex === 0;
      nextButton.disabled = !loaded || state.activeIndex === COMPONENTS.length - 1;
      clearLabelButton.disabled = !loaded;
      clearDraftButton.disabled = !loaded || !state.draft.length;
      clearAllButton.disabled = !loaded;
      renderLabels();
      renderCanvas();
    }

    function resetAnnotations() {
      for (const item of COMPONENTS) {
        state.polygons[item.label] = [];
      }
      state.draft = [];
      state.activeIndex = 0;
    }

    imageInput.addEventListener("change", () => {
      const [file] = imageInput.files;
      if (!file) return;
      const image = new Image();
      image.onload = () => {
        state.file = file;
        state.image = image;
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        resetAnnotations();
        setStatus(`已加载 ${file.name}，${canvas.width}x${canvas.height}`);
        render();
      };
      image.onerror = () => setStatus("图片加载失败。", true);
      image.src = URL.createObjectURL(file);
    });

    canvas.addEventListener("click", (event) => {
      if (!state.image) return;
      state.draft.push(pointFromEvent(event));
      render();
    });

    finishButton.addEventListener("click", () => {
      if (state.draft.length < 3) return;
      state.polygons[activeComponent().label].push(state.draft);
      state.draft = [];
      setStatus(`${activeComponent().label} 已添加一个 mask 区域。`);
      render();
    });

    undoPointButton.addEventListener("click", () => {
      state.draft.pop();
      render();
    });

    previousButton.addEventListener("click", () => {
      state.activeIndex = Math.max(0, state.activeIndex - 1);
      state.draft = [];
      render();
    });

    nextButton.addEventListener("click", () => {
      state.activeIndex = Math.min(COMPONENTS.length - 1, state.activeIndex + 1);
      state.draft = [];
      render();
    });

    clearLabelButton.addEventListener("click", () => {
      state.polygons[activeComponent().label] = [];
      state.draft = [];
      setStatus(`${activeComponent().label} 已清空。`);
      render();
    });

    clearDraftButton.addEventListener("click", () => {
      state.draft = [];
      render();
    });

    clearAllButton.addEventListener("click", () => {
      resetAnnotations();
      setStatus("已清空全部 mask。");
      render();
    });

    saveButton.addEventListener("click", async () => {
      if (!state.file || !state.image || totalPolygons() === 0) {
        setStatus("请先上传图片并至少画一个 mask。", true);
        return;
      }
      const formData = new FormData();
      formData.append("image", state.file);
      formData.append("annotations", JSON.stringify({
        image_size: {width: canvas.width, height: canvas.height},
        labels: state.polygons,
      }));
      saveButton.disabled = true;
      setStatus("正在保存并更新描述符库...");
      try {
        const response = await fetch("/api/save-reference", {
          method: "POST",
          body: formData,
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "保存失败。");
        }
        setStatus(`保存成功：${data.reference.id}`);
        await loadReferences();
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        render();
      }
    });

    async function loadReferences() {
      const response = await fetch("/api/references");
      const data = await response.json();
      const references = data.references || [];
      if (!references.length) {
        referencesTable.innerHTML = '<tr><td colspan="4">暂无参考图</td></tr>';
        return;
      }
      const rows = [];
      for (const item of references) {
        const labels = Object.entries(item.labels || {})
          .filter(([, value]) => Array.isArray(value.polygons) && value.polygons.length);
        if (!labels.length) {
          rows.push(`<tr>
            <td><code>${item.id}</code></td>
            <td>${item.original_filename || item.filename}</td>
            <td>-</td>
            <td>-</td>
          </tr>`);
          continue;
        }
        for (const [label, value] of labels) {
          const keypoints = (item.keypoints || {})[label] || 0;
          rows.push(`<tr>
            <td><code>${item.id}</code></td>
            <td>${item.original_filename || item.filename}</td>
            <td>${label}<br><span class="chip done">${value.polygons.length} 区域 / ${keypoints} keypoints</span></td>
            <td><button type="button" data-delete-label="${label}" data-reference-id="${item.id}">删除</button></td>
          </tr>`);
        }
      }
      referencesTable.innerHTML = rows.join("");
    }

    referencesTable.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-delete-label]");
      if (!button) return;
      const referenceId = button.dataset.referenceId;
      const label = button.dataset.deleteLabel;
      if (!referenceId || !label) return;
      if (!confirm(`删除参考图 ${referenceId} 的 ${label} 标注？`)) {
        return;
      }
      button.disabled = true;
      setStatus(`正在删除 ${referenceId} / ${label} 并重建描述符库...`);
      try {
        const response = await fetch(`/api/references/${referenceId}/labels/${label}`, {
          method: "DELETE",
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "删除失败。");
        }
        setStatus(`已删除 ${data.reference_id} / ${data.label}。`);
        await loadReferences();
      } catch (error) {
        setStatus(error.message || String(error), true);
      }
    });

    loadReferences();
    render();
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE_HTML, components=core.COMPONENTS)


@app.get("/api/references")
def references():
    try:
        return jsonify(core.list_references())
    except core.CV2Error as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/references/<reference_id>/labels/<label>")
def delete_reference_label(reference_id: str, label: str):
    try:
        return jsonify(core.delete_reference_label(reference_id, label))
    except core.CV2Error as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"删除失败: {exc}"}), 500


@app.post("/api/save-reference")
def save_reference():
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少上传图片。"}), 400
    annotations_text = request.form.get("annotations", "")
    if not annotations_text:
        return jsonify({"error": "缺少 annotations。"}), 400
    try:
        annotations = json.loads(annotations_text)
        result = core.save_reference(
            image_bytes=uploaded.read(),
            original_filename=uploaded.filename,
            labels_payload=annotations.get("labels", {}),
            image_size_payload=annotations.get("image_size", {}),
        )
        return jsonify(result)
    except (json.JSONDecodeError, ValueError, core.CV2Error) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"保存失败: {exc}"}), 500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CV_detection2 mask annotation app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5070)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
