from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024

DEFAULT_MODEL = os.getenv("OFFLINE_OMNI_MODEL", "qwen3.5-omni-plus").strip()
DEFAULT_BASE_URL = os.getenv(
    "OFFLINE_OMNI_BASE_URL",
    os.getenv("ALI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
).strip()
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("OMNI_TIMEOUT_SECONDS", "120"))

SYSTEM_INSTRUCTIONS = """
You are an expert chemistry lab instruction analyst.
The user uploads an audio clip containing a chemistry teacher's spoken guidance.
Analyze the teaching content in English only.
Be precise, safety-aware, and useful for students reviewing an experiment.
Do not invent details that are not supported by the audio.
""".strip()

ANALYSIS_INSTRUCTIONS = """
Listen to the teacher guidance and produce a structured English analysis.
Use this exact structure:

Transcript:
- Summarize or transcribe the key spoken content.

Experiment Context:
- Identify the likely experiment, apparatus, reagents, and objective if audible.

Procedure Guidance:
- List the operational instructions in the order they are given.

Safety and Risk Checks:
- Identify hazards, PPE, heating, pressure, glassware, ventilation, or waste-disposal warnings.

Student Action Items:
- Give short, concrete steps a student should follow next.

Unclear or Missing Information:
- Note anything important that the audio does not make clear.

Write in concise English. If the audio is not understandable, say so and explain what is missing.
""".strip()


def iter_omni_analysis(
    audio_bytes: bytes,
    *,
    audio_format: str,
    model: str,
    timeout_seconds: int,
) -> Any:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        yield {
            "type": "error",
            "message": "DASHSCOPE_API_KEY is missing. Set it in chemistry/.env or the environment.",
        }
        yield {"type": "done"}
        return

    client = OpenAI(
        api_key=api_key,
        base_url=DEFAULT_BASE_URL,
        timeout=timeout_seconds,
    )
    audio_data = base64.b64encode(audio_bytes).decode("utf-8")
    started_at = time.perf_counter()
    yield {"type": "status", "message": "Uploading audio to Qwen Omni chat completions."}

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:;base64,{audio_data}",
                                "format": audio_format,
                            },
                        },
                        {"type": "text", "text": ANALYSIS_INSTRUCTIONS},
                    ],
                },
            ],
            modalities=["text"],
            stream=True,
            stream_options={"include_usage": True},
        )

        for chunk in completion:
            if chunk.choices:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield {"type": "delta", "text": content}
            else:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    yield {"type": "usage", "usage": _to_plain_dict(usage)}

        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        yield {"type": "metrics", "timings": {"total_ms": elapsed_ms}}
        yield {"type": "done"}
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done"}


def detect_audio_format(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"mp3", "wav", "aac", "m4a", "flac", "ogg", "amr", "3gp", "3gpp"}:
        return "3gp" if suffix == "3gpp" else suffix
    content_type = (content_type or "").lower()
    if "mpeg" in content_type:
        return "mp3"
    if "wav" in content_type or "wave" in content_type:
        return "wav"
    if "aac" in content_type:
        return "aac"
    if "mp4" in content_type or "m4a" in content_type:
        return "m4a"
    return "wav"


def _to_plain_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


@app.get("/")
def index() -> str:
    return render_template_string(INDEX_HTML, default_model=DEFAULT_MODEL)


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "model": DEFAULT_MODEL,
        "base_url": DEFAULT_BASE_URL,
        "has_api_key": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()),
    })


@app.post("/api/analyze")
def analyze_audio() -> Response:
    uploaded = request.files.get("audio")
    if uploaded is None:
        return jsonify({"error": "audio is required"}), 400

    audio_bytes = uploaded.read()
    if not audio_bytes:
        return jsonify({"error": "audio is empty"}), 400

    model = request.form.get("model", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    timeout_raw = request.form.get("timeout_seconds", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    timeout_seconds = int(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
    audio_format = detect_audio_format(uploaded.filename or "", uploaded.content_type)

    @stream_with_context
    def generate():
        for item in iter_omni_analysis(
            audio_bytes,
            audio_format=audio_format,
            model=model,
            timeout_seconds=timeout_seconds,
        ):
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Offline Omni Voice Analysis</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101114;
      --panel: #191b20;
      --panel-2: #20242b;
      --line: #343a43;
      --text: #f3f5f7;
      --muted: #a9b0ba;
      --accent: #4fd1a5;
      --accent-2: #f7c948;
      --danger: #ff6b6b;
      --shadow: 0 18px 60px rgba(0, 0, 0, 0.32);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 18% 12%, rgba(79, 209, 165, 0.14), transparent 30%),
        linear-gradient(135deg, #101114 0%, #171a1f 48%, #101114 100%);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    .shell {
      width: min(1280px, calc(100vw - 32px));
      min-height: 100vh;
      margin: 0 auto;
      padding: 24px 0;
      display: grid;
      grid-template-columns: minmax(340px, 460px) 1fr;
      gap: 18px;
      align-items: stretch;
    }

    .left, .right {
      background: rgba(25, 27, 32, 0.92);
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .left {
      border-radius: 8px;
      display: flex;
      flex-direction: column;
    }

    .brand {
      padding: 24px 24px 18px;
      border-bottom: 1px solid var(--line);
    }

    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .subtitle {
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 14px;
    }

    .drop {
      margin: 18px;
      min-height: 260px;
      border: 1px dashed #59616d;
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.018));
      display: grid;
      place-items: center;
      padding: 24px;
      text-align: center;
      cursor: pointer;
      transition: border-color 140ms ease, background 140ms ease, transform 140ms ease;
    }

    .drop:hover, .drop.dragging {
      border-color: var(--accent);
      background: rgba(79, 209, 165, 0.08);
      transform: translateY(-1px);
    }

    .drop-mark {
      width: 76px;
      height: 76px;
      margin: 0 auto 18px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #111418;
      border: 1px solid var(--line);
    }

    .drop-mark svg {
      width: 38px;
      height: 38px;
      stroke: var(--accent);
    }

    .drop-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
    }

    .drop-note {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .file-input { display: none; }

    .controls {
      padding: 0 18px 18px;
      display: grid;
      gap: 12px;
    }

    .field {
      display: grid;
      gap: 7px;
    }

    label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    input[type="text"], input[type="number"] {
      width: 100%;
      min-height: 42px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #121419;
      color: var(--text);
      padding: 0 12px;
      font-size: 14px;
      outline: none;
    }

    .two {
      display: grid;
      grid-template-columns: 1fr 124px;
      gap: 10px;
    }

    .audio-panel {
      padding: 16px 18px 18px;
      border-top: 1px solid var(--line);
      display: grid;
      gap: 12px;
    }

    .file-meta {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
      font-size: 13px;
      min-height: 20px;
    }

    canvas {
      width: 100%;
      height: 92px;
      border-radius: 6px;
      background: #0f1115;
      border: 1px solid var(--line);
    }

    audio {
      width: 100%;
      height: 38px;
    }

    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
    }

    button {
      border: 0;
      border-radius: 6px;
      min-height: 44px;
      padding: 0 15px;
      color: #07110d;
      background: var(--accent);
      font-weight: 800;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: opacity 140ms ease, transform 140ms ease;
    }

    button:hover { transform: translateY(-1px); }
    button:disabled { cursor: not-allowed; opacity: 0.45; transform: none; }

    .secondary {
      color: var(--text);
      background: #2b3038;
      border: 1px solid var(--line);
    }

    .status {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      min-height: 20px;
    }

    .right {
      border-radius: 8px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 0;
    }

    .analysis-head {
      padding: 18px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(32, 36, 43, 0.78);
    }

    .analysis-title {
      display: grid;
      gap: 4px;
    }

    h2 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }

    .pill {
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      background: #121419;
    }

    .log {
      overflow: auto;
      padding: 20px;
      scroll-behavior: smooth;
    }

    .entry {
      border-left: 3px solid var(--line);
      padding: 0 0 18px 16px;
      margin: 0 0 2px;
    }

    .entry:last-child { padding-bottom: 0; }

    .entry h3 {
      margin: 0 0 8px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .entry.status-entry { border-left-color: var(--accent-2); }
    .entry.transcript-entry { border-left-color: #7aa7ff; }
    .entry.answer-entry { border-left-color: var(--accent); }
    .entry.error-entry { border-left-color: var(--danger); }

    .entry p, .answer {
      margin: 0;
      color: #dfe4ea;
      line-height: 1.62;
      font-size: 15px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .placeholder {
      height: 100%;
      min-height: 320px;
      display: grid;
      place-items: center;
      color: var(--muted);
      text-align: center;
    }

    .placeholder svg {
      width: 84px;
      height: 84px;
      stroke: #4d5560;
      margin-bottom: 16px;
    }

    @media (max-width: 920px) {
      .shell {
        grid-template-columns: 1fr;
        padding: 14px 0;
        width: min(100vw - 20px, 680px);
      }

      .right {
        min-height: 560px;
      }
    }

    @media (max-width: 520px) {
      .brand { padding: 20px 18px 14px; }
      h1 { font-size: 24px; }
      .drop { margin: 12px; min-height: 220px; }
      .two { grid-template-columns: 1fr; }
      .analysis-head { align-items: flex-start; flex-direction: column; }
      .actions { flex-direction: column; align-items: stretch; }
      button { justify-content: center; width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="left">
      <div class="brand">
        <h1>Offline Omni Voice Analysis</h1>
        <p class="subtitle">Drop a teacher guidance recording and review a live English analysis of the chemistry experiment instructions.</p>
      </div>

      <label class="drop" id="dropZone" for="fileInput">
        <input class="file-input" id="fileInput" type="file" accept="audio/*">
        <span>
          <span class="drop-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 15V3"></path>
              <path d="M7 8l5-5 5 5"></path>
              <path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"></path>
            </svg>
          </span>
          <span class="drop-title">Drop audio here</span>
          <span class="drop-note">WAV, MP3, M4A, or any browser-decodable audio file.</span>
        </span>
      </label>

      <div class="controls">
        <div class="two">
          <div class="field">
            <label for="modelInput">Model</label>
            <input id="modelInput" type="text" value="{{ default_model }}">
          </div>
          <div class="field">
            <label for="timeoutInput">Timeout</label>
            <input id="timeoutInput" type="number" min="10" max="300" step="5" value="120">
          </div>
        </div>
      </div>

      <div class="audio-panel">
        <div class="file-meta">
          <span id="fileName">No file selected</span>
          <span id="fileStats"></span>
        </div>
        <canvas id="waveform" width="900" height="220"></canvas>
        <audio id="player" controls></audio>
        <div class="actions">
          <button id="analyzeBtn" disabled>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polygon points="5 3 19 12 5 21 5 3"></polygon>
            </svg>
            Analyze
          </button>
          <button id="clearBtn" class="secondary" disabled>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M3 6h18"></path>
              <path d="M8 6V4h8v2"></path>
              <path d="M19 6l-1 14H6L5 6"></path>
            </svg>
            Clear
          </button>
        </div>
        <div class="status" id="statusLine">Ready.</div>
      </div>
    </section>

    <section class="right">
      <div class="analysis-head">
        <div class="analysis-title">
          <h2>Live AI Analysis</h2>
          <span class="status" id="analysisState">Waiting for audio.</span>
        </div>
        <span class="pill" id="sampleRatePill">Qwen Omni chat completions</span>
      </div>
      <div class="log" id="log">
        <div class="placeholder" id="placeholder">
          <div>
            <svg viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 14a8 8 0 1 0 16 0"></path>
              <path d="M8 14a4 4 0 1 0 8 0"></path>
              <path d="M12 2v12"></path>
            </svg>
            <div>The rolling analysis will appear here.</div>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    const TARGET_RATE = 16000;
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const player = document.getElementById('player');
    const waveform = document.getElementById('waveform');
    const fileName = document.getElementById('fileName');
    const fileStats = document.getElementById('fileStats');
    const statusLine = document.getElementById('statusLine');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const clearBtn = document.getElementById('clearBtn');
    const log = document.getElementById('log');
    const analysisState = document.getElementById('analysisState');
    const modelInput = document.getElementById('modelInput');
    const timeoutInput = document.getElementById('timeoutInput');

    let selectedFile = null;
    let audioReady = false;
    let activeAnswer = null;

    drawEmptyWaveform();

    dropZone.addEventListener('dragover', (event) => {
      event.preventDefault();
      dropZone.classList.add('dragging');
    });

    dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('dragging');
    });

    dropZone.addEventListener('drop', (event) => {
      event.preventDefault();
      dropZone.classList.remove('dragging');
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) loadFile(file);
    });

    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      if (file) loadFile(file);
    });

    analyzeBtn.addEventListener('click', analyze);
    clearBtn.addEventListener('click', reset);

    async function loadFile(file) {
      selectedFile = file;
      audioReady = false;
      activeAnswer = null;
      analyzeBtn.disabled = true;
      clearBtn.disabled = false;
      fileName.textContent = file.name;
      fileStats.textContent = formatBytes(file.size);
      statusLine.textContent = 'Decoding audio in the browser.';
      analysisState.textContent = 'Audio selected.';
      player.src = URL.createObjectURL(file);

      try {
        const buffer = await file.arrayBuffer();
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const audioBuffer = await audioContext.decodeAudioData(buffer.slice(0));
        const pcm = audioBufferToPcm16(audioBuffer, TARGET_RATE);
        drawWaveform(audioBuffer);
        const seconds = audioBuffer.duration.toFixed(1);
        const previewSeconds = (pcm.byteLength / 2 / TARGET_RATE).toFixed(1);
        audioReady = true;
        fileStats.textContent = `${formatBytes(file.size)} / ${seconds}s`;
        statusLine.textContent = `Decoded preview at 16 kHz mono (${previewSeconds}s). Original file will be sent to Omni.`;
        analyzeBtn.disabled = false;
        await audioContext.close();
      } catch (error) {
        statusLine.textContent = `Decode failed: ${error.message}`;
        analysisState.textContent = 'Decode failed.';
        drawEmptyWaveform();
      }
    }

    async function analyze() {
      if (!audioReady || !selectedFile) return;

      analyzeBtn.disabled = true;
      statusLine.textContent = 'Starting Omni analysis.';
      analysisState.textContent = 'Running.';
      clearLog();
      appendEntry('status', 'Status', 'Preparing upload.');

      const formData = new FormData();
      formData.append('audio', selectedFile, selectedFile.name);
      formData.append('model', modelInput.value.trim());
      formData.append('timeout_seconds', timeoutInput.value.trim());

      try {
        const response = await fetch('/api/analyze', {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.trim()) continue;
            handleEvent(JSON.parse(line));
          }
        }
      } catch (error) {
        appendEntry('error', 'Error', error.message);
        statusLine.textContent = 'Analysis failed.';
        analysisState.textContent = 'Failed.';
      } finally {
        analyzeBtn.disabled = !audioReady;
      }
    }

    function handleEvent(event) {
      if (event.type === 'status') {
        appendEntry('status', 'Status', event.message);
        statusLine.textContent = event.message;
      } else if (event.type === 'transcript') {
        appendEntry('transcript', 'Transcript', event.text);
      } else if (event.type === 'delta') {
        if (!activeAnswer) activeAnswer = appendEntry('answer', 'AI Analysis', '');
        activeAnswer.textContent += event.text;
        scrollLog();
      } else if (event.type === 'final_text') {
        if (!activeAnswer) activeAnswer = appendEntry('answer', 'AI Analysis', event.text);
      } else if (event.type === 'metrics') {
        const total = event.timings && event.timings.total_ms;
        appendEntry('status', 'Timing', total ? `${total} ms total` : 'Completed.');
      } else if (event.type === 'error') {
        appendEntry('error', 'Error', event.message);
        statusLine.textContent = 'Analysis failed.';
        analysisState.textContent = 'Failed.';
      } else if (event.type === 'done') {
        statusLine.textContent = 'Done.';
        analysisState.textContent = 'Complete.';
      }
    }

    function appendEntry(kind, title, text) {
      const currentPlaceholder = document.getElementById('placeholder');
      if (currentPlaceholder) currentPlaceholder.remove();
      const entry = document.createElement('article');
      entry.className = `entry ${kind}-entry`;
      const heading = document.createElement('h3');
      heading.textContent = title;
      const body = document.createElement('p');
      body.className = kind === 'answer' ? 'answer' : '';
      body.textContent = text || '';
      entry.appendChild(heading);
      entry.appendChild(body);
      log.appendChild(entry);
      scrollLog();
      return body;
    }

    function clearLog() {
      log.innerHTML = '';
      activeAnswer = null;
    }

    function scrollLog() {
      log.scrollTop = log.scrollHeight;
    }

    function reset() {
      selectedFile = null;
      audioReady = false;
      activeAnswer = null;
      fileInput.value = '';
      player.removeAttribute('src');
      fileName.textContent = 'No file selected';
      fileStats.textContent = '';
      statusLine.textContent = 'Ready.';
      analysisState.textContent = 'Waiting for audio.';
      analyzeBtn.disabled = true;
      clearBtn.disabled = true;
      drawEmptyWaveform();
      log.innerHTML = '<div class="placeholder" id="placeholder"><div>The rolling analysis will appear here.</div></div>';
    }

    function audioBufferToPcm16(audioBuffer, targetRate) {
      const sourceRate = audioBuffer.sampleRate;
      const length = Math.floor(audioBuffer.duration * targetRate);
      const pcm = new Int16Array(length);
      const channels = [];
      for (let channel = 0; channel < audioBuffer.numberOfChannels; channel += 1) {
        channels.push(audioBuffer.getChannelData(channel));
      }

      for (let i = 0; i < length; i += 1) {
        const sourceIndex = i * sourceRate / targetRate;
        const left = Math.floor(sourceIndex);
        const right = Math.min(left + 1, audioBuffer.length - 1);
        const fraction = sourceIndex - left;
        let sample = 0;
        for (const data of channels) {
          sample += data[left] + (data[right] - data[left]) * fraction;
        }
        sample /= channels.length || 1;
        const clipped = Math.max(-1, Math.min(1, sample));
        pcm[i] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff;
      }
      return pcm.buffer;
    }

    function drawWaveform(audioBuffer) {
      const ctx = waveform.getContext('2d');
      const width = waveform.width;
      const height = waveform.height;
      const data = audioBuffer.getChannelData(0);
      ctx.clearRect(0, 0, width, height);
      paintWaveBackground(ctx, width, height);
      ctx.strokeStyle = '#4fd1a5';
      ctx.lineWidth = 2;
      ctx.beginPath();
      const step = Math.max(1, Math.floor(data.length / width));
      for (let x = 0; x < width; x += 1) {
        let min = 1;
        let max = -1;
        const start = x * step;
        for (let j = 0; j < step && start + j < data.length; j += 1) {
          const value = data[start + j];
          min = Math.min(min, value);
          max = Math.max(max, value);
        }
        const y1 = (1 + min) * height / 2;
        const y2 = (1 + max) * height / 2;
        ctx.moveTo(x, y1);
        ctx.lineTo(x, y2);
      }
      ctx.stroke();
    }

    function drawEmptyWaveform() {
      const ctx = waveform.getContext('2d');
      paintWaveBackground(ctx, waveform.width, waveform.height);
      ctx.strokeStyle = '#343a43';
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let x = 0; x < waveform.width; x += 12) {
        const y = waveform.height / 2 + Math.sin(x / 28) * 10;
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    function paintWaveBackground(ctx, width, height) {
      ctx.fillStyle = '#0f1115';
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = '#20242b';
      ctx.lineWidth = 1;
      for (let x = 0; x < width; x += 60) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
    }

    function formatBytes(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
      return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
    }
  </script>
</body>
</html>
"""


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="Offline Omni voice-analysis demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
