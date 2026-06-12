# 部署说明

## 快速部署

```bash
# 1. 把代码同步到服务器（git pull 或 scp）
cd /path/to/chemistry

# 2. 确认 .env 和 runtime_assets/ 在位
ls .env runtime_assets/

# 3. 构建并启动
docker compose up -d --build

# 4. 验证
curl http://127.0.0.1/api/glasses/health
```

## 更新代码后重新部署

```bash
cd /path/to/chemistry
git pull  # 或者 scp 覆盖文件
docker compose up -d --build
```

只改了前端/Python 代码就够了。改了 `docker-compose.yml` 或 Nginx 配置也一样这条命令。

## 服务器目录结构

```
chemistry/
  .env                    ← API Key 配置（不进 git）
  Dockerfile
  docker-compose.yml
  deploy/nginx/chemistry.conf
  pipeline/               ← 后端代码
  CV_detection2/          ← CV 特征匹配代码
  runtime_assets/         ← 运行时资源（不进 git）
    ├── demo_videos/      ← 演示视频（教师通过 /admin 上传也行）
    ├── user_records/     ← 学生实验记录（自动生成）
    ├── reference_images/
    ├── cv_detection2_data/
    ├── safety_check_data/
    ├── electrolysis_check_data/
    └── speaker_models/
  requirements.txt
  client_factory.py
  openrouter_client.py
  ali_client.py
  omni_client.py
  gemini_client.py
  qiniu_client.py
```

## .env 配置

```bash
cp env.example .env
# 编辑 .env 填入：
```

```dotenv
QINIU_API_KEY=你的七牛key
DASHSCOPE_API_KEY=你的阿里云key

QINIU_MODEL=google/gemini-3.5-flash
QINIU_API_BASE=https://api.qnaigc.com

OMNI_MODEL=qwen3.5-omni-flash-realtime
OMNI_TTS_MODEL=qwen3.5-omni-plus-realtime
OMNI_VOICE=Ethan

CHEM_RUNTIME_ASSETS_DIR=runtime_assets
```

## 可选并发参数

在 `.env` 中添加（有默认值，不加也行）：

```dotenv
CHEM_GLASSES_JOB_WORKERS=4       # 后台任务并发数
CHEM_GLASSES_MAX_PENDING_JOBS=50 # 最大排队任务数
CHEM_VLM_CONCURRENCY=4           # VLM API 最大并发
CHEM_OMNI_CONCURRENCY=4          # Omni 语音 API 最大并发
WEB_THREADS=8                    # Gunicorn 线程数
WEB_TIMEOUT=300                  # 请求超时秒数
```

## 访问地址

| 用途 | 地址 |
|------|------|
| 眼镜端 API | `http://<服务器IP>/api/glasses/health` |
| 管理前端 | `http://<服务器IP>/admin` |
| 调试前端 | `http://<服务器IP>/` |

教师浏览器打开 `/admin` 可上传演示视频、查看/下载学生实验记录。

## 眼镜端配置

```xml
<string name="glasses_connection_mode">network</string>
<string name="glasses_service_base_url_network">http://<服务器IP></string>
```

不需要写端口号。

## 常用命令

```bash
# 查看容器状态
docker compose ps

# 查看后端日志
docker compose logs -f chemistry-api

# 查看 Nginx 日志
docker compose logs -f nginx

# 停止
docker compose down

# 重建并启动
docker compose up -d --build

# 进入容器调试
docker compose exec chemistry-api bash
```

## 防火墙

只需开放 TCP 80：

```bash
sudo ufw allow 80/tcp
```

## 常见问题

**接口返回 302 跳转 `its.pku.edu.cn`**
→ 访问设备没有完成校园网认证，不是服务器问题。

**容器启动后 502**
→ `docker compose logs -f chemistry-api` 看具体错误，通常是 `.env` 没配或 `runtime_assets/` 缺文件。

**视频列表为空**
→ 确认 `runtime_assets/demo_videos/` 下有 `.mp4` 文件，或通过 `/admin` 上传。

**上传视频失败 413**
→ 文件超过 100MB，需要调整 `deploy/nginx/chemistry.conf` 中 `client_max_body_size`。
