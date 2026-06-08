# Docker + Nginx 部署说明

目标：服务器用 Docker Compose 启动两个容器：

- `chemistry-api`：Python 后端，Gunicorn 监听容器内 `5080`。
- `nginx`：对外监听服务器 `80`，反向代理到 `chemistry-api:5080`。

眼镜端只访问：

```text
http://<server-ip-or-domain>
```

不需要写 `:5080`，也不需要 `adb reverse`。

## 1. 服务器目录

把代码和资源包放成这个结构：

```text
chemistry/
  Dockerfile
  docker-compose.yml
  deploy/
    nginx/
      chemistry.conf
  pipeline/
  CV_detection2/
  runtime_assets/
  requirements.txt
  env.example
  .env
  client_factory.py
  openrouter_client.py
  ali_client.py
  omni_client.py
  gemini_client.py
  qiniu_client.py
```

`runtime_assets/` 不进 git，但部署时必须放到服务器：

```text
runtime_assets/
  reference_images/
  demo_videos/
    distillation/
      *.mp4
  cv_detection2_data/
  safety_check_data/
  electrolysis_check_data/
  speaker_models/
```

Android 工程不需要部署到服务器。服务器只需要后端代码、`runtime_assets/`、`.env`。

## 2. 配置 API Key

在服务器代码目录复制模板：

```bash
cp env.example .env
```

然后编辑 `.env`：

```dotenv
QINIU_API_KEY=
DASHSCOPE_API_KEY=

QINIU_MODEL=google/gemini-3.5-flash
QINIU_API_BASE=https://api.qnaigc.com

OMNI_MODEL=qwen3.5-omni-flash-realtime
OMNI_TTS_MODEL=qwen3.5-omni-plus-realtime
OMNI_VOICE=Ethan

CHEM_RUNTIME_ASSETS_DIR=runtime_assets
```

说明：

- `QINIU_API_KEY`：七牛云 key。实验纠错、安全检查、电解图片分析默认用 `qiniu + google/gemini-3.5-flash`。
- `DASHSCOPE_API_KEY`：阿里云 DashScope key。语音指令、语音问答、语音合成使用 Qwen Omni。
- `.env` 不要提交 git，不要放进 `runtime_assets/`。
- Docker Compose 会读取 `.env`，并在容器里把资源目录覆盖为 `/app/runtime_assets`。

## 3. 构建和启动

在服务器代码目录执行：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f chemistry-api
docker compose logs -f nginx
```

停止：

```bash
docker compose down
```

更新代码后重新构建：

```bash
docker compose up -d --build
```

## 4. 验证接口

服务器本机验证：

```bash
curl -i http://127.0.0.1/api/glasses/health
curl -i http://127.0.0.1/api/glasses/demo/videos?experiment_type=distillation
```

外部电脑验证：

```powershell
curl.exe -i http://<server-ip-or-domain>/api/glasses/health
curl.exe -i http://<server-ip-or-domain>/api/glasses/demo/videos?experiment_type=distillation
```

正确结果应为：

```text
HTTP/1.1 200 OK
Content-Type: application/json
...
"status":"ok"
```

如果返回 `302` 并跳到 `its.pku.edu.cn`，说明访问设备没有完成校园网认证，不是后端问题。

## 5. Nginx 配置

当前配置文件：

```text
deploy/nginx/chemistry.conf
```

核心逻辑：

```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 100m;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://chemistry-api:5080;
    }
}
```

它会把所有请求转发到后端容器。后端容器不直接暴露公网端口，只在 Docker 网络内提供 `5080`。

如果以后要用域名和 HTTPS，可以在 Nginx 这一层加证书，不需要改 Python 后端。

## 6. 防火墙和云主机安全组

服务器需要开放：

```text
TCP 80
```

不需要开放 `5080`。

Ubuntu 如果启用了 UFW：

```bash
sudo ufw allow 80/tcp
sudo ufw reload
sudo ufw status
```

云主机控制台也要放行入站 TCP 80。

## 7. 眼镜端配置

Android 配置写：

```xml
<string name="glasses_connection_mode">network</string>
<string name="glasses_service_base_url_network">http://<server-ip-or-domain></string>
```

不要写 `:5080`。

重新打包安装 APK 后，眼镜会访问：

```text
http://<server-ip-or-domain>/api/glasses/health
```

校园网环境下，眼镜自己也必须完成校园网认证。可以在眼镜上执行：

```bash
/system/bin/curl -i http://<server-ip-or-domain>/api/glasses/health
```

如果看到：

```text
HTTP/1.1 302
https://its.pku.edu.cn/?cause=unauthN
```

说明眼镜没有通过校园网认证，需要浏览器登录、MAC 白名单，或改用手机热点。

## 8. 演示视频管理

视频放这里：

```text
runtime_assets/demo_videos/distillation/
```

只支持 `.mp4`。新增视频后不需要重启服务，重新说“演示指引”即可看到新列表。

接口按文件名排序，眼镜端用当前排序编号选择：

```text
1. 减压蒸馏_操作流程.mp4
2. 减压蒸馏_原理与小结.mp4
3. 新增教学视频.mp4
```

语音支持：

- “第一个”
- “第二个”
- “播放 3 号”
- “播放操作流程”
- “停止播放”

## 9. 常见问题

### 9.1 `ImportError: libGL.so.1`

服务器不要装 GUI 版 OpenCV。当前依赖已经使用：

```text
opencv-python-headless
```

如果服务器旧环境装过 GUI 版：

```bash
python -m pip uninstall -y opencv-python opencv-python-headless
python -m pip install -r requirements.txt
```

Docker 部署一般不会遇到这个问题。

### 9.2 外部电脑能访问，眼镜返回 302

眼镜没有完成校园网认证。用 adb 查：

```bash
adb shell "/system/bin/curl -i http://<server-ip-or-domain>/api/glasses/health"
```

如果返回 `its.pku.edu.cn/?cause=unauthN`，需要给眼镜登录校园网或做 MAC 白名单。

### 9.3 视频列表为空

检查：

```bash
ls runtime_assets/demo_videos/distillation/
docker compose exec chemistry-api ls /app/runtime_assets/demo_videos/distillation/
```

### 9.4 容器启动后接口 502

查看后端日志：

```bash
docker compose logs -f chemistry-api
```

常见原因：

- `.env` 没有配置 key。
- `runtime_assets/` 没挂载。
- Python 依赖安装失败。
