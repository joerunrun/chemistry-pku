# 化学实验智能辅助系统：整体架构

## 1. 系统组成

```text
RayNeo ARGF20 眼镜
├── 学生版 App：实验选择、拍照分析、语音问答、演示指导、录像/照片/录音
└── 教师版 App：教师语音指令、拍照、录像、指导录音
        │ HTTP（USB adb reverse 或局域网 IP）
        ▼
Python Pipeline 服务
├── Flask API：pipeline/web/app.py
├── 实验分析：CV 检测 + 实验策略 + VLM
├── 语音控制：Qwen Omni 实时意图识别
├── 媒体分析：上传录音转写、照片描述
├── 记录/照片/视频存储：runtime_assets/
└── 管理页面：/admin、/teacher-admin
        │
        ├── 本地 CV/规则库
        └── 外部模型服务（七牛 VLM、DashScope Omni/TTS）
```

## 2. 核心数据流

1. 眼镜采集图片、音频或视频，通过 HTTP 上传到 Flask API。
2. Flask 按学生/教师角色和实验类型处理请求。
3. 实验图片进入 CV/规则/VLM 分析；语音指令进入 Omni 意图识别。
4. 录音、照片、MP4 保存到 `runtime_assets/`，录音和照片进入异步媒体分析队列。
5. 分析结果写入记录目录的 `analysis.json`，管理页面读取并预览/下载。

## 3. 代码分层

```text
pipeline/web/           API、管理页面、文件上传下载
pipeline/experiments/   实验规格、策略和统一实验入口
pipeline/voice_control/ 语音意图、状态和 prompt
pipeline/media_analysis/录音转写、照片描述队列
pipeline/common/        VLM、图像、计时等公共组件
runtime_assets/         演示视频、参考库、学生/教师记录和照片
```

## 4. Android 端职责

- `MainActivity`：学生版交互和实验流程。
- `TeacherActivity`：教师版语音状态机、拍照、录像和指导录音。
- `RecordingService`：CameraX 生命周期、录像文件生成。
- `CameraSessionManager`：相机绑定、拍照和录像分段。
- USB 模式只改变网络通道；学生版和教师版仍是两个独立 APK。
