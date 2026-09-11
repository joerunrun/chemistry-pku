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
├── 第一层：原始记录存储（录音、照片、MP4、元数据）
├── 第二层：媒体理解（录音转写、照片描述）
├── 第三层：教学应用（总结、填写、共性问题、报告）
└── 统一工作台：/login → /workspace（旧 /admin、/teacher-admin 仅兼容跳转）
        │
        ├── 本地 CV/规则库
        └── 外部模型服务（七牛 VLM、DashScope Omni/TTS）
```

## 2. 核心数据流

1. 眼镜采集图片、音频或视频，通过 HTTP 上传到 Flask API。
2. Flask 按学生/教师角色和实验类型处理请求。
3. 实验图片进入 CV/规则/VLM 分析；语音指令进入 Omni 意图识别。
4. 录音、照片、MP4 先保存到第一层；录音和照片进入异步媒体分析队列。
5. 第二层结果写入记录目录的 `analysis.json`。
6. 第三层读取原始文件、元数据和第二层结果，生成独立的派生结果。

## 2.1 实验目录与课程总结范围

系统需要把“实验目录”和“记录内容”分开管理：

- 实验目录是全局定义，初始包含减压蒸馏、常压蒸馏等实验。
- 每个实验包含稳定 ID、名称、实验基本信息、操作要求、参考文档和演示视频。
- 教师可以在后台新增实验、上传或替换参考文档和演示视频；已有实验配置继续兼容。
- 记录上传时可以带实验 ID；历史记录没有明确实验时，允许教师在网页上重新映射。
- 总结时由用户选择范围，不再固定只能按“当天全部内容”：
  - 日期范围；
  - 一个或多个实验；
  - 一个或多个设备；
  - 必要时选择具体记录。
- AI 总结只读取所选范围内的第二层转写和实验参考资料，原始记录和单条转写不变。

教师现在可在统一工作台选择最多 31 天、实验、眼镜和具体记录生成课程整理或共性问题报告。输入只来自当前教师已经认领且完成转写的教师音频；旧的单日课程整理作为历史报告继续保留。

## 3. 代码分层

```text
pipeline/web/           API、管理页面、文件上传下载
pipeline/experiments/   实验规格、策略和统一实验入口
pipeline/voice_control/ 语音意图、状态和 prompt
pipeline/media_analysis/录音转写、照片描述队列
pipeline/common/        VLM、图像、计时等公共组件
runtime_assets/         持久化资源、原始记录、分析任务和派生结果
```

## 数据目录

```text
runtime_assets/
├── teacher_records/YYYYMMDD/<record_id>/
│   ├── record_video.mp4      # 可选，原始录像
│   ├── record_audio.wav      # 可选，原始录音
│   ├── record.json           # 设备、实验和文件元数据
│   └── analysis.json         # 第二层分析状态和转写
├── teacher_photos/YYYYMMDD/<photo_id>/
│   ├── photo.jpg             # 原始照片
│   ├── photo.json             # 照片元数据
│   └── analysis.json         # 照片描述状态和结果
├── user_records/、user_photos/ # 学生版对应目录
├── media_analysis_jobs/      # 可恢复的异步任务
├── device_mappings.json      # 教师设备 ID 到一号、二号的映射
├── experiment_catalog/       # 可扩展实验目录、参考文档和配置
└── derived/                  # 第三层派生结果
```

## 数据安全边界

- 上传目录使用时间和随机 ID，新上传不会覆盖旧记录。
- Docker 通过 `./runtime_assets:/app/runtime_assets` 挂载主机目录；重建镜像不会删除这些文件。
- `code_deploy.zip` 只更新代码，不应覆盖 `.env` 或 `runtime_assets/`。
- 正式更新前备份 `runtime_assets/`；禁止使用清空数据目录的更新方式。
- 第三层结果可重新生成，第一层原始文件必须长期保留。
- 实验目录和参考资料属于配置数据，更新时不能删除已有实验 ID；停用实验而不是直接删除。
- 总结报告必须记录筛选条件和输入记录 ID，避免无法追溯“这段课程是由哪些资料生成的”。

## 4. Android 端职责

- `MainActivity`：学生版交互和实验流程。
- `TeacherActivity`：教师版语音状态机、拍照、录像和指导录音。
- `RecordingService`：CameraX 生命周期、录像文件生成。
- `CameraSessionManager`：相机绑定、拍照和录像分段。
- USB 模式只改变网络通道；学生版和教师版仍是两个独立 APK。
