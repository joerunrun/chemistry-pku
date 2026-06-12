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
├── voice_control/              语音意图识别
├── speaker_verify/             声纹验证（教师识别）
├── experiments/                实验类型配置
├── resource_paths.py           runtime_assets 路径管理
└── tools/                      离线工具脚本
```

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
