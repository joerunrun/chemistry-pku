# 校园网服务器部署说明

本文档给工程师部署后端服务使用。目标是让智能眼镜通过校园网固定地址访问后端，不依赖开发机绝对路径，也不依赖 `adb reverse`。

## 1. 交付内容

服务器只需要两部分：

1. 代码目录
   - `pipeline/`
   - `CV_detection2/`
   - `client_factory.py`
   - `openrouter_client.py`
   - `ali_client.py`
   - `omni_client.py`
   - `gemini_client.py`
   - `qiniu_client.py`
   - `requirements.txt`
   - `.env.example`

2. 运行资源目录
   - `runtime_assets/`
   - 该目录不进 git，部署时作为资源包整体放到代码根目录旁边。

Android 工程目录不需要交给服务器。Android 只是眼镜客户端，服务器只负责 HTTP API、模型调用、图片/视频资源读取。最终给眼镜安装 APK 即可。

## 2. 推荐目录结构

部署目录可以是任意位置，只要保持如下相对结构：

```text
chemistry/
  pipeline/
  CV_detection2/
  runtime_assets/
  requirements.txt
  .env
  client_factory.py
  openrouter_client.py
  ali_client.py
  omni_client.py
  gemini_client.py
  qiniu_client.py
```

`runtime_assets/` 结构：

```text
runtime_assets/
  reference_images/
    distillation_correct_annotated.png
  demo_videos/
    distillation/
      *.mp4
  cv_detection2_data/
    annotations.json
    descriptor_db.json
    descriptor_db.npz
    sift_descriptor_db.json
    sift_descriptor_db.npz
    images/
  safety_check_data/
    features.npz
    metadata.json
    images/
  electrolysis_check_data/
    features.npz
    metadata.json
    images/
  speaker_models/
    3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx
```

提前录好的眼镜提示音在 Android APK 内，不是服务器资源，不需要放到服务器。

## 3. 安装依赖

在服务器部署目录执行：

```powershell
cd <deploy-root>/chemistry
python -m pip install -r requirements.txt
```

## 4. 配置 `.env`

部署时直接使用 API key。做法是复制模板：

```powershell
copy .env.example .env
```

然后编辑 `.env`，填入 key 和模型配置。

最少必须配置：

```dotenv
QINIU_API_KEY=
DASHSCOPE_API_KEY=
```

推荐完整配置：

```dotenv
# 图片理解：实验纠错、安全检查、电解纠错
QINIU_API_KEY=
QINIU_MODEL=google/gemini-3.5-flash
QINIU_API_BASE=https://api.qnaigc.com

# 语音控制、语音问答、语音合成
DASHSCOPE_API_KEY=
OMNI_MODEL=qwen3.5-omni-flash-realtime
OMNI_TTS_MODEL=qwen3.5-omni-plus-realtime
OMNI_VOICE=Ethan

# 资源目录。使用相对路径即可，默认按服务启动目录解析。
CHEM_RUNTIME_ASSETS_DIR=runtime_assets
```

说明：

- `QINIU_API_KEY`：七牛云模型服务 key。实验纠错、安全检查、电解图片分析默认走 `qiniu + google/gemini-3.5-flash`。
- `DASHSCOPE_API_KEY`：阿里云 DashScope key。语音指令、语音问答、语音合成使用 Qwen Omni。
- `CHEM_RUNTIME_ASSETS_DIR`：资源包目录。部署目录结构按本文档放置时，填 `runtime_assets` 即可，不需要本机绝对路径。
- `.env` 不进 git，不放进 `runtime_assets/`。交付工程师时可以单独私密传递 `.env`，或让工程师在服务器上按 `.env.example` 填写。

## 5. 启动服务

在部署目录执行：

```powershell
python -m pipeline.web.app --host 0.0.0.0 --port 5080
```

如果部署为 Windows 服务、计划任务、NSSM、Docker 或 systemd，保持同样的工作目录、环境变量和启动命令即可。

## 6. 校园网访问

服务器需要提供固定访问地址：

```text
http://<server-ip-or-domain>:5080
```

需要确认：

- 防火墙允许 TCP 5080 入站。
- 校园网策略允许眼镜所在网络访问该服务器和端口。
- 校园网部署后，眼镜不需要 `adb reverse`。

健康检查：

```text
http://<server-ip-or-domain>:5080/api/glasses/health
```

应返回 JSON，且 `status` 为 `ok`。

## 7. 演示视频管理

演示视频不进 APK，也不进 git。全部放服务器资源目录：

```text
runtime_assets/demo_videos/distillation/
```

当前只支持 `.mp4`。新增视频时直接复制到该目录即可，不需要改代码。接口会按文件名排序，并返回编号加文件名：

```text
1. 减压蒸馏_操作流程.mp4
2. 减压蒸馏_原理与小结.mp4
3. 新增教学视频.mp4
```

验证接口：

```text
http://<server-ip-or-domain>:5080/api/glasses/demo/videos?experiment_type=distillation
```

眼镜语音选择以接口返回顺序为准，支持：

- “第一个”
- “第二个”
- “第三个”
- “播放 3 号”
- “播放操作流程”
- “播放原理与小结”

## 8. Android 客户端

Android 工程不需要部署到服务器。

服务器部署完成后，只需要把眼镜 App 的服务地址改成校园网地址，重新打包安装 APK：

```xml
<string name="glasses_connection_mode">network</string>
<string name="glasses_service_base_url_network">http://<server-ip-or-domain>:5080</string>
```

打包和安装在开发机执行即可。服务器不需要 Android SDK、Gradle 或 Android 工程。

本地 USB 调试才需要：

```powershell
adb reverse tcp:5080 tcp:5080
```

校园网部署不需要执行这条命令。

## 9. 验收

服务器验收：

```text
/api/glasses/health
/api/glasses/demo/videos?experiment_type=distillation
```

眼镜验收：

- 说“小北助手”可以唤醒。
- 说“演示指引”显示当前视频列表。
- 说“第二个”能播放对应排序的视频。
- 播放中说“停止播放”能退出。
- 新增 MP4 到 `runtime_assets/demo_videos/distillation/` 后，重新说“演示指引”，列表自动出现新视频。
- 实验纠错、安全检查、电解实验能正常读取资源库。

## 10. 常见问题

- `health` 访问失败：检查服务是否启动、防火墙、校园网策略、端口是否为 5080。
- 视频列表为空：检查 `CHEM_RUNTIME_ASSETS_DIR` 是否指向正确目录，以及 `.mp4` 是否放在 `runtime_assets/demo_videos/distillation/`。
- 视频能列出但不能播放：用浏览器打开接口返回的 `stream_url`，检查是否能直接播放或下载。
- 实验纠错缺参考图：检查 `runtime_assets/reference_images/distillation_correct_annotated.png` 是否存在。
- 安全检查或电解检索失败：检查对应目录下的 `features.npz` 和 `metadata.json` 是否存在。
