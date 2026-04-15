# 化学实验纠错助手

这个项目基于 OpenRouter 调用 Gemini 模型，做一个两步式的化学实验纠错流程：

1. 上传一张正确图片和一张错误图片，让 AI 找出关键差异。
2. 输入实验要求文字，让 AI 结合差异分析，输出主要错误、原因和纠正建议。

同时提供一个 `try.py`，先验证 OpenRouter + Gemini 的图片理解链路是否通畅。

## 文件说明

- `gemini3pro.py`：你原始的 OpenRouter 调用示例。 现在只能用qwen
- `openrouter_client.py`：统一封装环境变量读取、图片压缩和 OpenRouter 请求。
- `chemistry_ai.py`：化学实验纠错相关提示词与业务逻辑。
- `try.py`：最小可运行验证脚本。
- `app.py`：Flask Web 主程序。
- `templates/index.html`、`static/styles.css`：前端页面。

## API Key 怎么设置

代码默认读取 `OPENROUTER_API_KEY`。

### 方法 1：当前 PowerShell 会话临时设置

```powershell
$env:OPENROUTER_API_KEY="你的_openrouter_api_key"
```

这个只对当前终端窗口有效，关掉就失效。

### 方法 2：写入当前用户环境变量

```powershell
[System.Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "你的_openrouter_api_key", "User")
```

设置后重新打开 PowerShell，再执行：

```powershell
echo $env:OPENROUTER_API_KEY
```

如果能看到值，说明已经生效。

### 方法 3：使用 `.env` 文件

项目根目录新建一个 `.env` 文件：

```env
OPENROUTER_API_KEY=你的_openrouter_api_key
OPENROUTER_MODEL=google/gemini-3.1-pro-preview
OPENROUTER_APP_NAME=Chemistry Experiment Checker
```

程序里通过 `python-dotenv` 自动读取 `.env`，再用 `os.getenv("OPENROUTER_API_KEY")` 获取。

## 正确读取的代码方式

核心读取逻辑在 `openrouter_client.py`：

```python
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("未读取到 OPENROUTER_API_KEY")
```

这比把 key 写死在代码里安全得多，也方便部署。

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 先跑 try.py 验证链路

### 单图识别

```powershell
python try.py --image "化小北-普化部分\电解\正确\整体1.jpg"
```

### 正确图 / 错误图对比

```powershell
python try.py --correct "化小北-普化部分\电解\正确\整体1.jpg" --wrong "化小北-普化部分\电解\错误\开关接线错.jpg"
```

运行成功后，终端会打印模型名和 Gemini 返回的中文分析结果。

## 启动 Web

```powershell
python app.py --provider openrouter
```

或：

```powershell
python app.py --provider ali
```

浏览器打开：

```text
http://127.0.0.1:5000
```

## Web 工作流

### Step 1：图片差异分析

- 上传正确图片。
- 上传错误图片。
- 后端将两张图压缩后转成 base64，传给 Gemini。
- 返回“主要不同点 / 最可能的关键错误 / 判断依据”。

### Step 2：结合实验要求判断主要错误

- 输入实验要求文字。
- 后端取出 Step 1 的差异分析结果。
- 让 Gemini 结合“实验要求 + 图像差异”重新判断。
- 返回“主要错误 / 原因 / 纠正建议”。

## 设计思路

### 1. 先做最小链路验证

先用 `try.py` 验证三件事：

- 环境变量能否正确读取。
- OpenRouter 请求头是否正确。
- Gemini 是否能对图片返回文字结果。

这样可以先排掉 Key、模型名、图片消息格式这些基础问题。

### 2. 公共调用层单独封装

`openrouter_client.py` 负责：

- 读取环境变量。
- 统一设置请求头。
- 调 OpenRouter Chat Completions 接口。
- 对上传图片做压缩，避免图片过大导致请求体膨胀。

这样 `try.py` 和 Web 共用一套底层逻辑。

### 3. 两步式纠错而不是一步到位

拆成两步有两个原因：

- 第一步只关注视觉差异，减少模型分心。
- 第二步再引入实验要求，让最终判断更贴近具体实验标准。

这比“图片和长文本一次性全喂进去”更容易控制输出质量。

## 可调环境变量

- `OPENROUTER_API_KEY`：必填。
- `OPENROUTER_MODEL`：模型名，默认 `google/gemini-3.1-pro-preview`。
- `OPENROUTER_BASE_URL`：默认 `https://openrouter.ai/api/v1/chat/completions`。
- `OPENROUTER_APP_NAME`：显示在 OpenRouter 请求头中的应用名。
- `OPENROUTER_SITE_URL`：可选，写到 `HTTP-Referer`。
- `OPENROUTER_TIMEOUT_SECONDS`：请求超时秒数，默认 `180`。
- `OPENROUTER_MAX_IMAGE_SIDE`：图片最长边压缩值，默认 `1600`。
- `OPENROUTER_JPEG_QUALITY`：JPEG 压缩质量，默认 `85`。
- `DASHSCOPE_API_KEY`：阿里云百炼 API Key。
- `ALI_MODEL`：阿里云模型名，默认 `qwen3.6-plus`。
- `ALI_BASE_URL`：默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `ALI_TIMEOUT_SECONDS`：请求超时秒数，默认 `180`。
- `ALI_MAX_IMAGE_SIDE`：图片最长边压缩值，默认 `1600`。
- `ALI_JPEG_QUALITY`：JPEG 压缩质量，默认 `85`。

## 后续可以继续扩展

- 增加“实验类型模板”，例如电解、分光光度计、减压蒸馏分别用不同提示词。
- 把 Step 1 的结果结构化成 JSON，便于后续统计和标注。
- 增加历史记录和导出功能。

## 故障排查

### 返回 403：provider Terms Of Service

如果你看到类似报错：

```text
The request is prohibited due to a violation of provider Terms Of Service.
```

可能是被封了
