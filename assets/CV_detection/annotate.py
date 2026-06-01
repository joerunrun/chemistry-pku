from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request


COMPONENTS: list[dict[str, str]] = [
    {"label": "voltage_regulator", "name": "调压器", "color": "#fff200"},
    {"label": "heating_unit", "name": "加热装置", "color": "#ff0000"},
    {"label": "distillation_core", "name": "蒸馏核心", "color": "#00b050"},
    {"label": "condenser_unit", "name": "冷凝装置", "color": "#00a2e8"},
    {"label": "receiving_assembly", "name": "接收装置", "color": "#ff7f27"},
    {"label": "vacuum_gauge", "name": "真空表", "color": "#a349a4"},
]

BBOX_PATH = Path(__file__).with_name("bbox.json")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>实验装置框标注器</title>
  <style>
    :root {
      --bg: #f7f3ed;
      --ink: #1f1f1f;
      --muted: #6b625b;
      --panel: #ffffff;
      --line: #ddd3c8;
      --accent: #92502a;
      --accent-dark: #683516;
      --ok: #157347;
      --warn: #9a6700;
      --radius: 8px;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }

    .page {
      width: min(1400px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 40px;
    }

    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 16px;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 1.55rem;
      line-height: 1.2;
    }

    .subtle {
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 16px;
      align-items: start;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
    }

    .toolbar,
    .side {
      padding: 14px;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
    }

    input[type="file"] {
      max-width: 360px;
    }

    button {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px 12px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      cursor: pointer;
    }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 700;
    }

    button.primary:hover {
      background: var(--accent-dark);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.5;
    }

    .canvas-wrap {
      overflow: auto;
      min-height: 420px;
      max-height: calc(100vh - 190px);
      padding: 10px;
    }

    canvas {
      display: block;
      max-width: 100%;
      height: auto;
      border: 1px solid var(--line);
      background: #f2eee8;
      cursor: crosshair;
    }

    .empty {
      display: grid;
      min-height: 420px;
      place-items: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: var(--radius);
      background: #fbfaf8;
    }

    .current {
      margin: 0 0 12px;
      padding: 11px 12px;
      border-radius: var(--radius);
      background: #f8f0e9;
      color: var(--accent-dark);
      font-weight: 700;
    }

    .labels {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }

    .label-row {
      display: grid;
      grid-template-columns: 16px 1fr auto;
      align-items: center;
      gap: 9px;
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
      border: 1px solid rgba(0, 0, 0, 0.25);
    }

    .status-chip {
      color: var(--muted);
      font-size: 0.82rem;
    }

    .status-chip.done { color: var(--ok); }
    .status-chip.skip { color: var(--warn); }

    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 14px;
    }

    .actions .wide {
      grid-column: 1 / -1;
    }

    .meta {
      display: grid;
      gap: 8px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.94rem;
      line-height: 1.5;
    }

    .message {
      min-height: 24px;
      color: var(--accent-dark);
      font-weight: 600;
    }

    code {
      font-family: Consolas, "Courier New", monospace;
    }

    @media (max-width: 980px) {
      .layout {
        grid-template-columns: 1fr;
      }

      header {
        display: block;
      }

      .canvas-wrap {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <h1>实验装置框标注器</h1>
        <p class="subtle">上传参考图，按右侧固定顺序拖矩形框；保存后覆盖生成 <code>bbox.json</code>。</p>
      </div>
      <p class="subtle">坐标保存为原图像素：[ymin, xmin, ymax, xmax]</p>
    </header>

    <section class="toolbar panel">
      <input id="imageInput" type="file" accept="image/*">
      <button id="resetAllButton" type="button">清空全部标注</button>
      <button id="saveButton" class="primary" type="button" disabled>保存 bbox.json</button>
      <span id="message" class="message"></span>
    </section>

    <section class="layout">
      <div class="panel canvas-wrap">
        <canvas id="canvas" hidden></canvas>
        <div id="emptyState" class="empty">先选择一张参考图</div>
      </div>

      <aside class="panel side">
        <p id="currentLabel" class="current">当前：未加载图片</p>
        <div id="labels" class="labels"></div>
        <div class="actions">
          <button id="previousButton" type="button" disabled>上一个</button>
          <button id="nextButton" type="button" disabled>下一个</button>
          <button id="clearCurrentButton" type="button" disabled>重标当前</button>
          <button id="skipButton" type="button" disabled>跳过/不可见</button>
          <button id="firstMissingButton" class="wide" type="button" disabled>跳到下一个未完成</button>
        </div>
        <div class="meta">
          <div id="imageMeta">图片：未加载</div>
          <div id="progressMeta">进度：0 / 6</div>
          <div>操作：在图上按住鼠标拖动矩形，松开后记录当前标签。</div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const COMPONENTS = {{ components | tojson }};
    const SAVE_URL = "{{ save_url }}";

    const imageInput = document.getElementById("imageInput");
    const canvas = document.getElementById("canvas");
    const ctx = canvas.getContext("2d");
    const emptyState = document.getElementById("emptyState");
    const labelsEl = document.getElementById("labels");
    const currentLabelEl = document.getElementById("currentLabel");
    const imageMeta = document.getElementById("imageMeta");
    const progressMeta = document.getElementById("progressMeta");
    const messageEl = document.getElementById("message");
    const saveButton = document.getElementById("saveButton");
    const resetAllButton = document.getElementById("resetAllButton");
    const previousButton = document.getElementById("previousButton");
    const nextButton = document.getElementById("nextButton");
    const clearCurrentButton = document.getElementById("clearCurrentButton");
    const skipButton = document.getElementById("skipButton");
    const firstMissingButton = document.getElementById("firstMissingButton");

    const state = {
      image: null,
      imageName: "",
      width: 0,
      height: 0,
      activeIndex: 0,
      boxes: {},
      skipped: {},
      dragging: false,
      start: null,
      draft: null,
    };

    for (const item of COMPONENTS) {
      state.boxes[item.label] = null;
      state.skipped[item.label] = false;
    }

    function setMessage(text, isError = false) {
      messageEl.textContent = text;
      messageEl.style.color = isError ? "#9f1d1d" : "var(--accent-dark)";
    }

    function currentComponent() {
      return COMPONENTS[state.activeIndex];
    }

    function isComplete(label) {
      return Array.isArray(state.boxes[label]) || state.skipped[label];
    }

    function completedCount() {
      return COMPONENTS.filter((item) => isComplete(item.label)).length;
    }

    function pointFromEvent(event) {
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const x = Math.round((event.clientX - rect.left) * scaleX);
      const y = Math.round((event.clientY - rect.top) * scaleY);
      return {
        x: Math.max(0, Math.min(canvas.width - 1, x)),
        y: Math.max(0, Math.min(canvas.height - 1, y)),
      };
    }

    function normalizeRect(a, b) {
      const xmin = Math.max(0, Math.min(a.x, b.x));
      const xmax = Math.min(canvas.width, Math.max(a.x, b.x));
      const ymin = Math.max(0, Math.min(a.y, b.y));
      const ymax = Math.min(canvas.height, Math.max(a.y, b.y));
      return [ymin, xmin, ymax, xmax];
    }

    function drawBox(box, color, label, isDraft = false) {
      if (!Array.isArray(box)) return;
      const [ymin, xmin, ymax, xmax] = box;
      const width = xmax - xmin;
      const height = ymax - ymin;
      if (width <= 0 || height <= 0) return;

      ctx.save();
      ctx.lineWidth = Math.max(3, Math.round(Math.min(canvas.width, canvas.height) / 350));
      ctx.strokeStyle = color;
      ctx.setLineDash(isDraft ? [14, 8] : []);
      ctx.strokeRect(xmin, ymin, width, height);
      ctx.setLineDash([]);

      const fontSize = Math.max(18, Math.round(Math.min(canvas.width, canvas.height) / 95));
      ctx.font = `${fontSize}px "Segoe UI", sans-serif`;
      const metrics = ctx.measureText(label);
      const pad = 6;
      const labelHeight = fontSize + pad * 2;
      const labelWidth = metrics.width + pad * 2;
      const top = Math.max(0, ymin - labelHeight);
      ctx.fillStyle = color;
      ctx.fillRect(xmin, top, labelWidth, labelHeight);
      ctx.fillStyle = "#000";
      ctx.fillText(label, xmin + pad, top + fontSize + 2);
      ctx.restore();
    }

    function renderCanvas() {
      if (!state.image) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(state.image, 0, 0);

      for (const item of COMPONENTS) {
        drawBox(state.boxes[item.label], item.color, item.name);
      }

      if (state.draft) {
        const item = currentComponent();
        drawBox(state.draft, item.color, item.name, true);
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
          state.draft = null;
          render();
        });

        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = item.color;

        const text = document.createElement("span");
        text.textContent = `${index + 1}. ${item.name}`;

        const chip = document.createElement("span");
        chip.className = "status-chip";
        if (Array.isArray(state.boxes[item.label])) {
          chip.textContent = "已标";
          chip.classList.add("done");
        } else if (state.skipped[item.label]) {
          chip.textContent = "跳过";
          chip.classList.add("skip");
        } else {
          chip.textContent = "待标";
        }

        button.append(swatch, text, chip);
        labelsEl.appendChild(button);
      }
    }

    function render() {
      const loaded = Boolean(state.image);
      canvas.hidden = !loaded;
      emptyState.hidden = loaded;

      const item = currentComponent();
      currentLabelEl.textContent = loaded ? `当前：${item.name}` : "当前：未加载图片";
      imageMeta.textContent = loaded
        ? `图片：${state.imageName}，${state.width}x${state.height}`
        : "图片：未加载";
      progressMeta.textContent = `进度：${completedCount()} / ${COMPONENTS.length}`;

      saveButton.disabled = !loaded || completedCount() !== COMPONENTS.length;
      previousButton.disabled = !loaded || state.activeIndex === 0;
      nextButton.disabled = !loaded || state.activeIndex === COMPONENTS.length - 1;
      clearCurrentButton.disabled = !loaded;
      skipButton.disabled = !loaded;
      firstMissingButton.disabled = !loaded || completedCount() === COMPONENTS.length;

      renderLabels();
      renderCanvas();
    }

    function moveToNextUnfinished() {
      const nextIndex = COMPONENTS.findIndex((item) => !isComplete(item.label));
      if (nextIndex >= 0) {
        state.activeIndex = nextIndex;
      }
    }

    function resetAll() {
      for (const item of COMPONENTS) {
        state.boxes[item.label] = null;
        state.skipped[item.label] = false;
      }
      state.activeIndex = 0;
      state.draft = null;
      setMessage("");
      render();
    }

    imageInput.addEventListener("change", () => {
      const [file] = imageInput.files;
      if (!file) return;

      const image = new Image();
      image.onload = () => {
        state.image = image;
        state.imageName = file.name;
        state.width = image.naturalWidth;
        state.height = image.naturalHeight;
        canvas.width = state.width;
        canvas.height = state.height;
        resetAll();
        setMessage("图片已加载，请开始标注。");
      };
      image.onerror = () => setMessage("图片加载失败。", true);
      image.src = URL.createObjectURL(file);
    });

    canvas.addEventListener("mousedown", (event) => {
      if (!state.image) return;
      state.dragging = true;
      state.start = pointFromEvent(event);
      state.draft = null;
    });

    canvas.addEventListener("mousemove", (event) => {
      if (!state.dragging || !state.start) return;
      state.draft = normalizeRect(state.start, pointFromEvent(event));
      renderCanvas();
    });

    window.addEventListener("mouseup", (event) => {
      if (!state.dragging || !state.start) return;
      state.dragging = false;
      const box = normalizeRect(state.start, pointFromEvent(event));
      state.start = null;
      state.draft = null;

      const [ymin, xmin, ymax, xmax] = box;
      if (xmax - xmin < 5 || ymax - ymin < 5) {
        setMessage("矩形太小，未记录。", true);
        render();
        return;
      }

      const item = currentComponent();
      state.boxes[item.label] = box;
      state.skipped[item.label] = false;
      setMessage(`${item.name} 已记录。`);

      if (state.activeIndex < COMPONENTS.length - 1) {
        state.activeIndex += 1;
      }
      render();
    });

    previousButton.addEventListener("click", () => {
      state.activeIndex = Math.max(0, state.activeIndex - 1);
      state.draft = null;
      render();
    });

    nextButton.addEventListener("click", () => {
      state.activeIndex = Math.min(COMPONENTS.length - 1, state.activeIndex + 1);
      state.draft = null;
      render();
    });

    firstMissingButton.addEventListener("click", () => {
      moveToNextUnfinished();
      state.draft = null;
      render();
    });

    clearCurrentButton.addEventListener("click", () => {
      const item = currentComponent();
      state.boxes[item.label] = null;
      state.skipped[item.label] = false;
      state.draft = null;
      setMessage(`${item.name} 已清空，可重新拖框。`);
      render();
    });

    skipButton.addEventListener("click", () => {
      const item = currentComponent();
      state.boxes[item.label] = null;
      state.skipped[item.label] = true;
      state.draft = null;
      setMessage(`${item.name} 已标记为不可见。`);
      if (state.activeIndex < COMPONENTS.length - 1) {
        state.activeIndex += 1;
      }
      render();
    });

    resetAllButton.addEventListener("click", resetAll);

    saveButton.addEventListener("click", async () => {
      if (!state.image || completedCount() !== COMPONENTS.length) {
        setMessage("请先完成所有标签，或显式跳过不可见目标。", true);
        return;
      }

      const boxes = {};
      for (const item of COMPONENTS) {
        boxes[item.label] = Array.isArray(state.boxes[item.label])
          ? state.boxes[item.label].map((value) => Math.round(value))
          : [];
      }

      saveButton.disabled = true;
      setMessage("正在保存...");
      try {
        const response = await fetch(SAVE_URL, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            image: state.imageName,
            image_size: {width: state.width, height: state.height},
            boxes,
            voltage_regulator_surface: "uncertain",
          }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "保存失败。");
        }
        setMessage(`已保存到 ${data.path}；已标 ${data.annotated_count}，跳过 ${data.skipped_count}。`);
      } catch (error) {
        setMessage(error.message || String(error), true);
      } finally {
        render();
      }
    });

    render();
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(
        PAGE_HTML,
        components=COMPONENTS,
        save_url="/api/save-annotations",
    )


@app.post("/api/save-annotations")
def save_annotations():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求体必须是 JSON 对象。"}), 400

    try:
        result = normalize_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    atomic_write_json(BBOX_PATH, result)

    boxes = result["boxes"]
    annotated_count = sum(1 for box in boxes.values() if box)
    skipped_count = sum(1 for box in boxes.values() if not box)
    return jsonify(
        {
            "path": str(BBOX_PATH),
            "image_size": result["image_size"],
            "annotated_count": annotated_count,
            "skipped_count": skipped_count,
        }
    )


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    image_name = str(payload.get("image", "")).strip()
    if not image_name:
        raise ValueError("缺少图片文件名。")

    image_size = payload.get("image_size")
    if not isinstance(image_size, dict):
        raise ValueError("缺少 image_size。")
    width = require_positive_int(image_size.get("width"), "image_size.width")
    height = require_positive_int(image_size.get("height"), "image_size.height")

    raw_boxes = payload.get("boxes")
    if not isinstance(raw_boxes, dict):
        raise ValueError("缺少 boxes。")

    boxes: dict[str, list[int]] = {}
    for component in COMPONENTS:
        label = component["label"]
        raw_box = raw_boxes.get(label)
        if raw_box in (None, []):
            boxes[label] = []
            continue
        boxes[label] = normalize_box(raw_box, width, height, label)

    surface = str(payload.get("voltage_regulator_surface", "uncertain")).strip()
    if surface not in {"bench", "rack", "not_visible", "uncertain"}:
        surface = "uncertain"

    return {
        "image": Path(image_name).name,
        "image_size": {"width": width, "height": height},
        "boxes": boxes,
        "voltage_regulator_surface": surface,
    }


def require_positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} 必须是正整数。") from None
    if number <= 0:
        raise ValueError(f"{field_name} 必须是正整数。")
    return number


def normalize_box(value: Any, width: int, height: int, label: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} 的框必须是 [ymin, xmin, ymax, xmax]。")
    try:
        ymin, xmin, ymax, xmax = [int(round(float(item))) for item in value]
    except (TypeError, ValueError):
        raise ValueError(f"{label} 的框坐标必须是数字。") from None

    ymin, ymax = sorted((ymin, ymax))
    xmin, xmax = sorted((xmin, xmax))
    ymin = clamp(ymin, 0, height - 1)
    ymax = clamp(ymax, ymin + 1, height)
    xmin = clamp(xmin, 0, width - 1)
    xmax = clamp(xmax, xmin + 1, width)

    if xmax - xmin < 1 or ymax - ymin < 1:
        raise ValueError(f"{label} 的框尺寸无效。")
    return [ymin, xmin, ymax, xmax]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地实验装置 bbox 标注器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
