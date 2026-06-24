# 化学实验智能辅助系统 — Pipeline 服务端

## 系统定位

AR 眼镜 + 校园网服务器的化学实验辅助系统。眼镜端通过 HTTP 访问内网服务器，服务器提供：实验纠错、安全检查、语音问答、演示视频播放、实验记录存储。同时提供管理前端供教师上传视频和下载学生实验记录。

## 架构

```
校园内网
┌──────────────────────────────────────────────────────┐
│                                                      │
│  AR 眼镜 ×10  ──HTTP──▶  Nginx(:80)                 │
│                              │                       │
│  教师浏览器 ────────────────▶│                       │
│                              ▼                       │
│                         Flask App(:5080)             │
│                         Gunicorn 1w + 4t             │
│                              │                       │
│                    ┌─────────┼─────────┐             │
│                    ▼         ▼         ▼             │
│              CV 检测    VLM API    语音 API           │
│             (本地CPU)   (外网)     (外网)             │
│                                                      │
│  数据目录: runtime_assets/                           │
│    ├── demo_videos/        演示视频（教师上传）       │
│    ├── user_records/       学生实验记录               │
│    ├── reference_images/   参考图数据库              │
│    ├── cv_detection2_data/ CV 特征库                 │
│    └── safety_check_data/  安全检查样例库            │
└──────────────────────────────────────────────────────┘
```

## 教师版语音助手（Android TeacherActivity）

教师版是一个透明 Activity，纯语音交互，复用学生端的服务端 prompt 和 RecordingService。

### 流程

教师端有独立状态机（4 态），每个状态用独立的服务端 prompt 和允许的 action：

```
LISTENING（默认）
  持续录音 + 每 3s 切最近 3s 发 context=sleep 检测"小北助手"
  wake=true → AWAKE

AWAKE → INSTRUCTION
  停持续录音，录 3.5s 短片段发 context=teacher_awake
  服务端用 TEACHER_AWAKE_PROMPT，只允许 capture_photo/start_recording
  capture_photo → 拍照 → 回 LISTENING
  start_recording → 录像 → RECORDING
  unclear → 提示 → 回 LISTENING

RECORDING（录像中，独立状态）
  持续录音继续，每 3s 切最近 3s 发 context=teacher_recording
  服务端用 TEACHER_RECORDING_STOP_PROMPT，只允许 stop_recording
  stop_recording → 停录像+上传 → 回 LISTENING
  没听清 → 继续 RECORDING 监听
```

退出/双击：停录像（若在录）+ 停持续录音 + 上传 wav。

### 关键设计

- **教师专用 prompt（服务端 intent_omni.py）**：
  - `TEACHER_AWAKE_PROMPT` — 只分类 capture_photo/start_recording/unclear，不识别纠错/安全检查/演示/问答
  - `TEACHER_RECORDING_STOP_PROMPT` — 只识别 stop_recording/unclear
  - 两个 context（`teacher_awake`/`teacher_recording`）有代码层强约束：非允许 action 一律降级 unclear
- **录像作为独立状态**：录像时持续录音不停（CameraX 录像音频和 AudioRecord 可共存，学生端也是这么做的），唤醒检测自动从"检测唤醒词"切到"检测停止录像"
- **单路 AudioRecord 双职责**：持续录音和检测共用一路 MIC，检测从环形缓冲取片段，不另开录音
- **复用 RecordingService**：拍照/录像直接 bind 调用，不改 RecordingService

## 部署方式

Docker Compose 两个容器：
- `chemistry-api`：Python 3.10 + Gunicorn，监听 5080
- `nginx`：对外 80，反代到 5080

启动：`docker compose up -d --build`

配置文件：
- `.env` — API Key、环境变量
- `deploy/nginx/chemistry.conf` — Nginx 配置
- `pipeline/experiment_ui_config.json` — 实验参数配置

## 代码结构

```
pipeline/
├── web/
│   ├── app.py                  Flask 主应用（所有 API）
│   ├── templates/
│   │   ├── index.html          开发调试前端（实验纠错/安全检查）
│   │   └── admin.html          管理前端（视频管理 + 记录管理）
│   └── static/styles.css
├── common/
│   ├── cv_wrapper.py           CV 特征匹配检测
│   ├── image_builder.py        标注图构建
│   ├── vlm_client.py           VLM 多 Provider 客户端
│   └── timing.py               计时工具
├── approach_a/pipeline_a.py    一步式 VLM 纠错
├── approach_b/pipeline_b.py    两步式 VLM 纠错
├── safety_check/               安全检查模块
├── electrolysis_check/         电解实验纠错模块
├── qa/                         语音问答 + TTS
├── voice_control/              语音意图识别（自然语言映射 + 状态分发）
├── speaker_verify/             声纹验证（教师识别）
├── experiments/                实验类型配置
├── resource_paths.py           runtime_assets 路径管理
└── tools/                      离线工具脚本
```

## 语音控制架构（voice_control/intent_omni.py）

### 核心设计：自然语言 → 标准 action

眼镜用户用**口语化的自然语言**表达需求（不背固定口令），服务端用 Qwen Omni 做**语义理解**，把语音归类到固定的标准 action，而不是字面关键词匹配。例如"把这一段录下来""帮我存一下现在的过程""实验记录"都映射到 `start_recording`。

### 三类控制状态（按 context 分发）

语音意图识别入口 `classify_audio_intent(context=...)` 根据 context 选择不同的 prompt 和允许的 action 集合。context 由眼镜端根据当前界面状态传入。

**1. 沉睡状态 `sleep`**
- 唤醒词检测器，只判断是否说了"小北助手"
- 输出 `{wake, transcript}`
- 用 `SLEEP_MODE_PROMPT`，转写完成即提前返回

**2. 主控制状态（AWAKE 模式，context 不属于下面几个特殊状态）**
- 用户已唤醒，处于正常实验流程
- 用 `AWAKE_MODE_PROMPT`，理解语义归类到 `MAIN_INTENTS`
- `MAIN_INTENTS = {start_recording, capture_photo, experiment_check, safety_check, demo_guide, qa, unclear}`
- 苏醒模式**不**负责：停止记录、选/停视频、投影测试、切换实验——这些由各自的专项状态处理
- 若只说实验名称而无其他需求 → `unclear`

**3. 其他专项控制状态（窄域识别，只认一类 action）**

| context | 唯一允许的 action | 说明 |
|---------|------------------|------|
| `experiment_setup` | `select_experiment` | 启动阶段选实验，用 `EXPERIMENT_SETUP_PROMPT`，支持 distillation/photometer/electrolysis/tlc |
| `demo_select` | `select_demo_video` | 演示视频选择页，解析 video_index（0-based）或 video_query |
| `demo_playing` | `stop_demo_video` | 视频播放中，只识别"停止/退出/别放了"，其它一律 unclear |
| `recording_stop_only` | `stop_recording` | 学生录像中，只识别"结束/保存/可以了/别录了"等停止表达 |
| `teacher_awake` | `capture_photo` / `start_recording` | 教师唤醒后，用 `TEACHER_AWAKE_PROMPT`，只识别拍照/开始录像 |
| `teacher_recording` | `stop_recording` | 教师录像中，用 `TEACHER_RECORDING_STOP_PROMPT`，只识别停止录像 |

### 专项状态的强约束逻辑

专项状态下，即使模型返回了其他 action，代码层也会强制降级为 `unclear`（见 `classify_audio_intent` 中的 `if ctx == ...` 分支）。这样保证窄域状态不会被模型误判带偏，例如视频播放中不会误触发 qa。

### 兜底机制

- 模型 JSON 解析失败或超时 → `_fallback_from_transcript` 按 context 做关键词兜底
- 模型在 2 秒内未返回 JSON 但已有转写 → 用转写兜底
- 转写为空且超时 → 抛 `TimeoutError`
- 苏醒阶段误返回 `select_experiment` → 强制降级 `unclear` 并提示"请先在启动阶段选择实验"


## 当前状态（2026-06-09）

### 已完成
- 眼镜端全部 API：任务提交/轮询、问答、TTS、意图识别、视频播放、记录上传
- 管理前端基础版：视频上传/删除/下载，记录列表/下载/删除
- Docker + Nginx 部署脚本
- CV 检测 + VLM 纠错 Pipeline（减压蒸馏、光度计、电解）
- 安全检查模块
- 教师声纹识别

### 待完成（按步骤推进）

#### 第 1 步：管理前端小幅补全

在现有 admin.html 基础上增强，保持简洁：
- 视频管理：加页面内播放预览（`<video>` 标签）
- 记录管理：加按日期筛选

#### 第 2 步：Nginx 视频静态服务优化

演示视频改为 Nginx 直接 serve，不走 Flask 线程：
- Nginx 增加 `location /static-videos/` 指向 `runtime_assets/demo_videos/`
- 多台眼镜同时播放时不占用 Flask worker

#### 第 3 步：并发压测

写 `pipeline/tools/load_test_glasses.py`：
- 模拟 N 台眼镜并发提交任务
- 采集响应时间、成功率
- 找出当前瓶颈

#### 第 4 步：并发增强

根据压测结果：
- job worker 数量参数化（环境变量）
- 加队列长度限制
- 加任务耗时记录
- 按需决定是否引入 Redis

#### 第 5 步：认证（后做）

`/admin` 路由加密码保护，环境变量配置。

## 开发约定

- 启动开发服务器：`python pipeline/web/app.py --debug`
- 生产部署：`docker compose up -d --build`
- 运行时资源不进 git，挂载 `runtime_assets/` volume
- 演示视频由教师命名后直接上传，不做分类目录逻辑
- API 返回统一 JSON，错误格式 `{"error": "..."}`
- 文件上传限制 512MB（Flask），Nginx 当前 100m 需同步调整
