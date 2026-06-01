# 校园网服务器部署说明

本文档给工程师部署电脑端/服务器端服务使用。目标是让智能眼镜通过校园网固定地址访问后端，不再依赖本机 `D:\chemistry\...` 绝对路径，也不再依赖 `adb reverse`。

## 1. 交付内容

工程师部署服务器只需要两部分内容：

1. 代码仓库：
   - `pipeline/`
   - `CV_detection2/`
   - `client_factory.py`
   - `openrouter_client.py`
   - `ali_client.py`
   - `omni_client.py`
   - `gemini_client.py`
   - `qiniu_client.py`
   - `requirements.txt`

2. 运行资源包：
   - `runtime_assets/`
   - 该目录不进 git，已经由开发机打包好，部署时整体放到服务器。

Android 工程目录不需要交给服务器。Android 只是眼镜客户端，最终只需要把 APK 安装到眼镜上。

`runtime_assets/` 结构如下：

```text
runtime_assets/
  reference_images/
    distillation_correct_annotated.png
  demo_videos/
    distillation/
      减压蒸馏_操作流程.mp4
      减压蒸馏_原理与小结.mp4
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

提前录好的眼镜提示音不是服务器资源，而是 Android APK 内的 WAV：

```text
D:\Android\projects\app\src\main\res\raw\*.wav
```

这些 WAV 会随 APK 一起安装到眼镜，不需要放到服务器。

## 2. 服务器安装

示例目录：

```text
D:\deploy\chemistry\
  pipeline\
  CV_detection2\
  runtime_assets\
  requirements.txt
  client_factory.py
  openrouter_client.py
  ali_client.py
  omni_client.py
  gemini_client.py
  qiniu_client.py
```

安装依赖：

```powershell
cd D:\deploy\chemistry
python -m pip install -r requirements.txt
```

配置资源目录：

```powershell
$env:CHEM_RUNTIME_ASSETS_DIR="D:\deploy\chemistry\runtime_assets"
```

## 3. API Key 配置

不要把你的本机 `.env` 放进 git，也不要混进 `runtime_assets/` 资源包。推荐做法是工程师在服务器部署目录单独创建 `.env`，或者写入服务器系统环境变量/服务启动脚本。

当前服务至少需要：

```powershell
QINIU_API_KEY="七牛云 key"
DASHSCOPE_API_KEY="阿里云 DashScope key"
```

推荐同时显式写清默认模型：

```powershell
QINIU_MODEL="google/gemini-3.5-flash"
OMNI_MODEL="qwen3.5-omni-flash-realtime"
OMNI_TTS_MODEL="qwen3.5-omni-plus-realtime"
CHEM_RUNTIME_ASSETS_DIR="D:\deploy\chemistry\runtime_assets"
```

说明：

- 实验纠错、安全检查、电解图片分析默认走 `qiniu + google/gemini-3.5-flash`，所以需要 `QINIU_API_KEY`。
- 语音指令、语音问答、语音合成走 Qwen Omni，所以需要 `DASHSCOPE_API_KEY`。
- 如果内部安全允许，可以把你本机 `.env` 通过私密渠道交给工程师，让工程师复制到服务器部署目录；但不要把 `.env` 放进代码包、资源包或 git。

启动服务：

```powershell
python -m pipeline.web.app --host 0.0.0.0 --port 5080
```

如果部署为 Windows 服务、计划任务、NSSM 或 systemd，保持同样的环境变量和启动命令即可。

## 4. 校园网端口

服务器需要在校园网内提供固定访问地址：

```text
http://服务器IP或域名:5080
```

需要确认：

- 服务器防火墙允许 TCP 5080 入站。
- 校园网策略允许眼镜所在网络访问该服务器端口。
- 眼镜和服务器之间不需要 `adb reverse`。

验证：

```text
http://服务器IP或域名:5080/api/glasses/health
```

应返回 JSON，且 `status` 为 `ok`。

## 5. 演示视频管理

演示视频不打进 APK，也不进 git。视频全部放服务器资源目录：

```text
runtime_assets/demo_videos/distillation/
```

只支持 `.mp4`。新增视频时直接复制到该目录，不需要改代码，不需要重启服务。

接口会按文件名排序，并返回编号加原始文件名：

```text
1. 减压蒸馏_操作流程.mp4
2. 减压蒸馏_原理与小结.mp4
3. 新增教学视频.mp4
```

验证：

```text
http://服务器IP或域名:5080/api/glasses/demo/videos?experiment_type=distillation
```

眼镜语音支持：

- “第一个”
- “第二个”
- “第三个”
- “播放 3 号”
- “播放操作流程”
- “播放原理与小结”

编号以当前接口返回的排序为准。

## 6. 眼镜端配置

修改 Android 配置：

```text
D:\Android\projects\app\src\main\res\values\glasses_config.xml
```

网络模式地址填写：

```xml
<string name="glasses_service_base_url_network">http://服务器IP或域名:5080</string>
```

重新打包安装：

```powershell
cd D:\Android\projects
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleDebug
D:\Android\SDK\platform-tools\adb.exe install -r -t app\build\outputs\apk\student\debug\app-student-debug.apk
```

使用校园网部署后，不需要执行：

```powershell
adb reverse tcp:5080 tcp:5080
```

如果只是本地 USB 调试，保持：

```xml
<string name="glasses_connection_mode">usb</string>
<string name="glasses_service_base_url_usb">http://127.0.0.1:5080</string>
```

然后电脑执行：

```powershell
python -m pipeline.web.app --host 0.0.0.0 --port 5080
D:\Android\SDK\platform-tools\adb.exe reverse tcp:5080 tcp:5080
```

## 7. 部署验收

服务器验收：

```text
/api/glasses/health
/api/glasses/demo/videos?experiment_type=distillation
```

眼镜验收：

- 说“小北助手”可以唤醒。
- 说“演示指引”显示当前视频列表。
- 说“第一个/第三个”能播放对应排序视频。
- 播放中说“停止播放”能退出。
- 新增一个 MP4 到 `runtime_assets/demo_videos/distillation/` 后，重新说“演示指引”，列表自动出现新视频。
- 实验纠错、安全检查、电解实验能正常读取资源库。

## 8. 常见问题

- 访问 `health` 失败：检查服务器防火墙、校园网策略、端口是否为 5080。
- 视频列表为空：检查 `CHEM_RUNTIME_ASSETS_DIR` 是否指向正确目录，以及 `.mp4` 是否放在 `runtime_assets/demo_videos/distillation/`。
- 视频能列出但不能播放：检查浏览器是否能直接打开 `stream_url`，以及校园网是否允许大文件流式传输。
- 实验纠错缺参考图：检查 `runtime_assets/reference_images/distillation_correct_annotated.png` 是否存在。
- 安全检查或电解检索失败：检查对应 `features.npz` 和 `metadata.json` 是否存在。
