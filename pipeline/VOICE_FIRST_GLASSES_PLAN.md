# 智能眼镜语音中心规划

## Summary

本版目标是把眼镜端改成“语音中心”交互：默认不显示复杂菜单，不要求用户频繁触碰眼镜腿。用户通过语音说出动作，电脑端 Qwen Omni 负责把用户语音映射到几个固定命令上，眼镜端只根据结构化命令执行对应功能。

重要调整：

- 删除默认后台录像。
- 不再进入 App 后一直录像。
- 只有用户明确说“开始录像”时才录像。
- 用户说“结束录像”时立即停止并保存。
- 如果模型没听清或不确定，必须返回 `unknown` / `unclear`，让系统提示用户重新说一遍，不能猜测执行。

屏幕原则：

- 默认只显示极简状态。
- 只在确认命令、等待处理、显示结果时短暂显示文字。
- 不常驻大按钮、长说明或技术细节。

## Core Idea

Qwen Omni 在这里主要做两件事：

1. 语音转写：把用户说的话转成文字。
2. 意图映射：把转写文本映射到固定命令集合。

它不是自由发挥控制系统，而是一个“语音到命令”的分类器。返回必须是结构化 JSON，例如：

```json
{
  "transcript": "开始安全检查",
  "intent": "safety_check",
  "slots": {},
  "reply_text": "开始安全检查，请保持现场在视野中央。",
  "confidence": 0.92
}
```

如果没听清：

```json
{
  "transcript": "",
  "intent": "unclear",
  "slots": {},
  "reply_text": "我没有听清，请再说一遍。",
  "confidence": 0.2
}
```

如果听到了内容但不属于支持命令：

```json
{
  "transcript": "帮我打开微信",
  "intent": "unknown",
  "slots": {},
  "reply_text": "这个指令暂不支持。你可以说：开始录像、安全检查、实验纠错或我要提问。",
  "confidence": 0.5
}
```

## Supported Intents

首版只支持固定命令：

- `start_recording`：开始录像
- `stop_recording`：结束录像
- `capture_photo`：开始拍照，只保存照片
- `experiment_check`：进入实验纠错流程
- `safety_check`：开始安全检查
- `select_experiment`：选择实验类型
- `qa`：语音问答
- `cancel`：取消当前流程
- `repeat`：重复上一条提示
- `unclear`：没听清
- `unknown`：听清了但不是支持命令

实验类型 slot：

- `distillation`：减压蒸馏
- `photometer`：光度计
- `electrolysis`：电解

## Startup Flow

启动流程：

1. 欢迎页短暂显示。
2. 自动测试电脑服务连接。
3. 请求麦克风、相机权限。
4. 不自动开始录像。
5. 进入 `VOICE_IDLE`。

默认界面：

```text
助手就绪
请说指令
```

可以接受的例句：

- “开始录像”
- “结束录像”
- “开始拍照”
- “安全检查”
- “实验纠错”
- “我要提问”

## Recording Flow

### 开始录像

用户说：

- “开始录像”
- “开始录制”
- “开始记录实验”

Omni 返回：

```json
{
  "intent": "start_recording",
  "reply_text": "开始录像。"
}
```

眼镜端行为：

1. 启动 CameraX 录像。
2. 同时开启录音。
3. 保存到相册可见目录。
4. 屏幕角落显示：

```text
录像中
```

### 结束录像

用户说：

- “结束录像”
- “停止录像”
- “保存录像”

眼镜端行为：

1. 停止当前录像。
2. 保存 MP4。
3. 刷新 MediaStore，让相册可见。
4. 短暂显示：

```text
录像已保存
```

### 开始拍照

用户说：

- “开始拍照”
- “拍一张照片”
- “保存照片”

眼镜端行为：

1. 抓取当前画面。
2. 保存照片。
3. 刷新 MediaStore。
4. 不自动做实验纠错或安全检查，除非当前状态已经在检测流程里。

## Experiment Check Flow

用户说：

```text
实验纠错
```

Omni 返回：

```json
{
  "intent": "experiment_check",
  "slots": {
    "experiment_type": null
  },
  "reply_text": "开始实验纠错，请说实验名称。"
}
```

眼镜端进入 `EXPERIMENT_SELECT_BY_VOICE`，继续监听。

用户说：

```text
减压蒸馏
```

Omni 返回：

```json
{
  "intent": "select_experiment",
  "slots": {
    "experiment_type": "distillation"
  },
  "reply_text": "已选择减压蒸馏，请保持装置在视野中央。"
}
```

眼镜端行为：

1. 显示“请保持装置在视野中央”。
2. 倒计时 2 到 3 秒。
3. 自动抓帧。
4. 上传：

```text
POST /api/glasses/jobs
mode=experiment
experiment_type=distillation|photometer|electrolysis
provider=ali
model=qwen3.6-flash
```

结果：

- 减压蒸馏、光度计：可显示检测框预览和文字建议。
- 电解：不显示检测框，只显示文字建议。

如果用户说的实验名称不清楚：

```json
{
  "intent": "unclear",
  "reply_text": "我没有听清实验名称，请说：减压蒸馏、光度计或电解。"
}
```

## Safety Check Flow

用户说：

```text
安全检查
```

Omni 返回：

```json
{
  "intent": "safety_check",
  "reply_text": "开始安全检查，请保持现场在视野中央。"
}
```

眼镜端行为：

1. 倒计时 2 到 3 秒。
2. 自动抓帧。
3. 上传：

```text
POST /api/glasses/jobs
mode=safety
provider=ali
model=qwen3.6-flash
```

屏幕提示使用现场话术：

```text
正在观察现场
正在整理建议
```

结果只显示：

```text
问题：...
意见：...
```

## Voice QA Flow

用户说：

- “我要提问”
- “问答”
- “帮我解释一下”
- 或直接问一个问题

如果用户只是说“我要提问”，系统提示：

```text
请说你的问题。
```

然后录制 5 到 8 秒音频，上传：

```text
POST /api/glasses/qa/audio
audio=<pcm/wav>
model=qwen3.5-omni-plus-realtime
```

返回：

```json
{
  "transcript": "为什么减压蒸馏要降低压强",
  "answer_text": "降低压强可以降低液体沸点，使物质在较低温度下蒸馏，减少分解风险。",
  "answer_audio_data_url": null
}
```

屏幕短暂显示：

```text
回答：
降低压强可以降低沸点，减少高温分解风险。
```

后续可以加语音播放回答。

## Voice Intent API

新增接口：

```text
POST /api/glasses/voice/intent
multipart/form-data
audio: wav 或 pcm
context: 当前状态，可选
```

返回：

```json
{
  "transcript": "开始录像",
  "intent": "start_recording",
  "slots": {},
  "reply_text": "开始录像。",
  "confidence": 0.93
}
```

必须约束：

- `intent` 只能来自固定枚举。
- `confidence < 0.65` 时一律按 `unclear` 处理。
- 不允许模型编造不存在的实验类型。
- 不允许模型输出自由格式文本控制系统。
- `unknown` 和 `unclear` 都不执行动作，只提示用户重新说。

## Android State Machine

建议状态：

- `BOOT`
- `PERMISSION_SETUP`
- `VOICE_IDLE`
- `VOICE_LISTENING`
- `INTENT_PROCESSING`
- `EXPERIMENT_SELECT_BY_VOICE`
- `CAPTURE_COUNTDOWN`
- `JOB_RUNNING`
- `QA_LISTENING`
- `QA_RUNNING`
- `RECORDING`
- `RESULT`

默认循环：

```text
VOICE_IDLE
  -> VOICE_LISTENING
  -> INTENT_PROCESSING
  -> 执行动作或提示重说
  -> VOICE_IDLE
```

录像状态可以叠加：

```text
VOICE_IDLE + recording=true
```

也就是说，录像中仍然可以继续听指令，例如“安全检查”或“结束录像”。

## Listening Strategy

首版建议不要真的 24 小时连续流式监听，先用短周期录音 + 本地语音活动检测：

1. 每轮录 2 到 4 秒 PCM 音频。
2. 眼镜端先做本地音量/VAD 判断。
3. 如果这一轮基本是静音或环境噪声，不上传 Omni，也不显示“没听清”，直接继续下一轮监听。
4. 只有检测到“有人说话”后，才上传 `/api/glasses/voice/intent`。
5. Omni 只在“确实有人说话”的前提下判断 `intent`。
6. 如果有人说话但内容听不清，返回 `unclear`。
7. 如果有人说话且听清了，但不是支持命令，返回 `unknown`。
8. 如果是明确命令，执行。
9. 执行后回到监听。

也就是说：

- 静音：本地丢弃，不提示。
- 环境噪声：本地尽量丢弃，不提示。
- 用户说话但没听清：`unclear`，提示“没听清，请再说一遍”。
- 用户说了不支持的命令：`unknown`，提示可用命令。

本地 VAD 简化规则：

```text
rms 音量低于阈值 -> silent
有效语音帧少于阈值 -> silent
持续超过 300ms 的明显人声 -> speech_detected
```

首版可以先不用复杂模型，只用 `AudioRecord` 采样后的 RMS 能量判断。后续如果噪声较大，再加 WebRTC VAD 或 RNNoise。

优点：

- 实现简单。
- 不依赖眼镜本地语音服务。
- 不需要在眼镜端做复杂 ASR。
- 更容易用 Qwen Omni 统一处理。

缺点：

- 会有网络和模型延迟。
- 耗电比手动触发高。
- 噪声环境下可能频繁 `unclear`。

降低误触策略：

- 静音段绝不调用 Omni。
- 低置信度语音段不执行动作。
- `unclear` 和 `unknown` 都必须是在“检测到用户说话”后才可能出现。
- 连续多轮静音时，屏幕保持“助手就绪”，不要反复刷新提示。

## Backend Changes

新增模块：

```text
pipeline/voice_control/
  intent_omni.py
```

新增接口：

```text
POST /api/glasses/voice/intent
```

复用现有接口：

- `GET /api/glasses/health`
- `POST /api/glasses/jobs`
- `GET /api/glasses/jobs/<job_id>`
- `POST /api/glasses/qa/audio`

Qwen Omni intent prompt 重点：

```text
你是智能眼镜的语音指令解析器。
请把用户语音映射到固定 intent。
只能输出 JSON。
如果没听清，intent=unclear。
如果命令不支持，intent=unknown。
不要执行命令，只负责解析。
```

## Android Changes

需要修改：

- 删除“启动后后台录像”的默认流程。
- 删除录像许可页或改成“允许语音控制录像权限”。
- 进入 App 后默认 `VOICE_IDLE`。
- 加入短周期录音循环。
- 用 `/api/glasses/voice/intent` 解析命令。
- `start_recording` 才启动录像。
- `stop_recording` 才停止录像。
- 录像时必须同时录音。
- `capture_photo` 只保存照片。
- `experiment_check` / `safety_check` 才进入自动拍照分析流程。
- `unclear` / `unknown` 时只提示重说，不执行动作。

## UI Principle

默认：

```text
助手就绪
请说指令
```

监听：

```text
正在听
```

没听清：

```text
没听清，请再说一遍
```

录像：

```text
录像中
```

检测：

```text
正在观察装置
```

问答：

```text
请说你的问题
```

## First Milestone

第一版只做这些：

1. `/api/glasses/voice/intent`
2. 支持 `start_recording`
3. 支持 `stop_recording`
4. 支持 `safety_check`
5. 支持 `experiment_check + distillation`
6. 支持 `qa`
7. 支持 `unclear` 重新提示

暂不做：

- 真正流式语音常开。
- 多轮复杂对话。
- 语音播放回答。
- 所有实验类型一次性完善。

## Test Plan

电脑端：

```powershell
cd D:\chemistry\chemistry_agent\chemistry
python -m py_compile pipeline\web\app.py pipeline\voice_control\intent_omni.py
```

眼镜端：

```powershell
cd D:\Android\projects
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleDebug
D:\Android\SDK\platform-tools\adb.exe install -r app\build\outputs\apk\debug\app-debug.apk
D:\Android\SDK\platform-tools\adb.exe reverse tcp:5080 tcp:5080
```

测试语音：

- “开始录像”
- “结束录像”
- “开始拍照”
- “安全检查”
- “实验纠错”
- “减压蒸馏”
- “我要提问”
- “为什么减压蒸馏要降低压强”

通过标准：

- 不说“开始录像”就不会录像。
- 说“开始录像”后才开始录像和录音。
- 说“结束录像”后录像停止并保存。
- 说“安全检查”能自动拍照并返回结果。
- 说“实验纠错，减压蒸馏”能自动拍照并返回结果。
- 没听清时不会误执行，只提示重新说。
