# 智能眼镜现场使用与 Qwen Omni 语音方案

## 目标

把当前智能眼镜 App 改成更适合实验课现场使用的助手：

- 启动后先自动测试电脑服务连接。
- 实验开始前完成检测类型和实验类型设置。
- 实验中默认只显示极简状态，尽量不遮挡视野。
- 需要功能时唤出菜单，菜单只保留两个选项：装置检测、语音问答。
- 全程后台录像继续保留，但不再提供“记录现象”入口。
- 语音问答不依赖 RayNeo 系统语音识别，改为眼镜录音上传电脑端 Qwen Omni。

## 交互流程

### 1. 启动与连接检查

启动流程：

```text
欢迎页 -> 自动测试连接 -> 录像许可 -> 实验设置 -> 实验中极简界面
```

自动测试连接调用：

```text
GET /api/glasses/health
```

连接成功后进入录像许可。连接失败时显示两个选项：

- 重试连接
- 继续设置

### 2. 实验设置

设置阶段允许全屏显示和眼镜腿滑动操作。

设置内容：

- 检测类型：实验纠错 / 安全检查
- 实验纠错下选择实验：减压蒸馏 / 光度计 / 电解

设置完成后进入实验中极简界面。

### 3. 实验中极简界面

默认只显示：

- 助手就绪
- 当前检测配置
- 连接状态
- 录像状态

单击唤出功能菜单。双击退出 App。

### 4. 功能菜单

菜单只显示两个大选项：

- 装置检测
- 语音问答

8 秒无操作自动收起，返回实验中极简界面。

## 装置检测

进入“装置检测”后自动完成：

1. 显示“请保持装置在视野中央”。
2. 倒计时约 2.5 秒。
3. 自动抓取当前画面。
4. 上传现有接口：

```text
POST /api/glasses/jobs
```

等待提示只显示现场话术：

- 正在观察装置
- 正在核对实验要求
- 正在整理建议

实验纠错可显示检测框预览。安全检查和电解只显示文字结果。

## 语音问答

语音问答链路：

1. 眼镜端用 `AudioRecord` 录音约 7 秒。
2. 音频格式：16 kHz、单声道、PCM16。
3. 上传电脑端：

```text
POST /api/glasses/qa/audio
```

4. 电脑端用 Qwen Omni Realtime 直接处理音频。
5. 返回用户转写和回答文本。

接口返回：

```json
{
  "transcript": "用户语音转写",
  "answer_text": "模型回答文字",
  "answer_audio_data_url": null,
  "timings": {
    "omni": 1234,
    "total": 1500
  }
}
```

首版只显示文字。回答语音播放作为下一步增强。

## Qwen Omni 技术路线

复用 `omni_demo.py` 的 DashScope Qwen Omni Realtime 方式：

- WebSocket：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
- 默认模型：`qwen3.5-omni-plus-realtime`
- 输入：base64 PCM16 音频块
- 输出：
  - `conversation.item.input_audio_transcription.completed`
  - `response.text.delta`
  - `response.text.done`

不再使用 RayNeo `SpeechRecognizer`，因为该服务在当前设备上没有稳定开放给第三方 App。

## 删除内容

本版删除所有“记录现象”相关设计：

- 不在菜单中显示“记录现象”。
- 不新增 `mode=observation`。
- 不做语音整理实验现象记录。
- 不做“语音 + 当前视野截图”的现象记录扩展。

全程录像仍然保留，作为后台记录能力，不作为用户主动触发的功能。

## 当前实现要点

- Android 启动后自动请求 `/api/glasses/health`。
- Android 现场菜单只保留“装置检测 / 语音问答”。
- Android 语音问答录制 PCM 后上传 `/api/glasses/qa/audio`。
- Flask 新增 `/api/glasses/qa/audio`。
- 电脑端新增 `pipeline.qa.omni_audio_qa`，使用 Qwen Omni Realtime 处理音频。
