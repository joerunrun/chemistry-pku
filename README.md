# 智能眼镜实验识别 App 调试指南

这个项目是眼镜端 Android App。电脑端负责运行化学实验 pipeline 服务，眼镜端只负责拍照、上传图片、轮询结果并显示文字反馈。

## 1. 重启电脑后如何启动电脑服务

电脑端代码在：

```text
D:\chemistry\化小北实验测试数据\chemistry
```

打开 PowerShell 后运行：

```powershell
cd D:\chemistry\化小北实验测试数据\chemistry
python -m pipeline.web.app --host 0.0.0.0 --port 5080
```

看到类似下面的输出后，服务才算真正启动完成：

```text
Running on http://127.0.0.1:5080
Running on http://电脑IP:5080
```

注意：服务启动时会预热安全检查检索库，可能需要十几秒。App 连接失败时，先在电脑浏览器打开：

```text
http://127.0.0.1:5080/api/glasses/health
```

如果电脑本机都打不开，说明后端服务没启动或已经退出。

## 2. 调试阶段推荐方式：USB + adb reverse

调试阶段建议先保持眼镜和电脑有线连接，用 Android Studio 或 adb 操作。这样不依赖校园网/局域网是否允许设备互访。

每次眼镜重新插线、ADB 重连、电脑重启后，重新运行：

```powershell
D:\Android\SDK\platform-tools\adb.exe reverse tcp:5080 tcp:5080
```

这条命令的意思是：眼镜访问自己的 `127.0.0.1:5080` 时，实际转发到电脑的 `5080` 服务。

此时 App 里的服务地址填写：

```text
http://127.0.0.1:5080
```

然后点击“测试连接”。如果连接成功，再点“拍照分析”。

## 3. 只用无线网络连接时怎么做

如果以后眼镜断开 USB，只走 Wi-Fi/局域网，不能再用 `127.0.0.1`，因为眼镜的 `127.0.0.1` 指的是眼镜自己。

无线连接时流程如下：

1. 电脑启动服务时必须使用：

```powershell
python -m pipeline.web.app --host 0.0.0.0 --port 5080
```

2. 在电脑上查 IPv4 地址：

```powershell
ipconfig
```

找到 WLAN 下面的 `IPv4 Address`，例如：

```text
10.7.222.153
```

3. 眼镜 App 的服务地址填写：

```text
http://10.7.222.153:5080
```

4. Windows 防火墙要允许 Python/5080 端口被局域网访问。

5. 眼镜和电脑必须在同一个能互相访问的网络里。很多校园网、访客 Wi-Fi、热点会开启“客户端隔离”，这种情况下眼镜虽然和电脑都在 Wi-Fi 上，但眼镜仍然访问不到电脑。遇到这种情况，继续用 USB + `adb reverse`，或者换成手机热点/路由器局域网测试。

## 4. 眼镜上现在是否安装了程序

当前调试机已经通过 adb 安装过 debug 包：

```powershell
D:\Android\SDK\platform-tools\adb.exe install -r app\build\outputs\apk\debug\app-debug.apk
```

如果重新改了代码，需要重新编译并安装。

## 5. Android Studio 调试流程

调试阶段建议这样做：

1. 电脑启动后端服务。
2. 插上眼镜，确认 ADB 识别：

```powershell
D:\Android\SDK\platform-tools\adb.exe devices
```

3. 开启 USB 反向端口：

```powershell
D:\Android\SDK\platform-tools\adb.exe reverse tcp:5080 tcp:5080
```

4. 在 Android Studio 里点 Run，或命令行编译安装：

```powershell
cd D:\Android\projects
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleDebug
D:\Android\SDK\platform-tools\adb.exe install -r app\build\outputs\apk\debug\app-debug.apk
```

5. App 服务地址填：

```text
http://127.0.0.1:5080
```

6. 点击“测试连接”，成功后再拍照分析。

## 6. 最后交付/安装到设备

最终给手机或眼镜安装时，使用这个 APK：

```text
D:\Android\projects\app\build\outputs\apk\debug\app-debug.apk
```

如果目标设备不连 USB，只靠网络访问电脑服务，App 里的地址必须改成电脑的局域网 IP，例如：

```text
http://电脑IPv4:5080
```

只要电脑服务没启动、电脑 IP 变了、防火墙拦截、或者网络禁止设备互访，App 都会显示连接失败。

## 7. 快速排查

- 电脑本机打不开 `http://127.0.0.1:5080/api/glasses/health`：后端没启动。
- 电脑能打开，但眼镜 `http://电脑IP:5080` 失败：网络不通或防火墙拦截。
- USB 调试时失败：重新运行 `adb reverse tcp:5080 tcp:5080`，App 地址用 `http://127.0.0.1:5080`。
- 拍照后等待很久：VLM 调用或安全检索耗时，先看电脑端 PowerShell/日志是否还在运行。



如何无线 ？
D:\Android\projects\app\src\main\res\values\glasses_config.xml  

<string name="glasses_service_base_url">http://172.20.10.2:5080</string>
改成电脑ip


D:\Android\SDK\platform-tools\adb.exe shell am start -a android.settings.WIFI_SETTINGS




dddd1@zhourungui:/mnt/c/WINDOWS/system32$ curl https://api.qnaigc.com/v1/models \
  -H "Authorization: Bearer $QINIU_AI_API_KEY"
{"object":"list","data":[{"id":"meituan/longcat-flash-lite","object":"model","created":1770370439,"owned_by":"system"},{"id":"qwen3-vl-30b-a3b-thinking","object":"model","created":1770629483,"owned_by":"system"},{"id":"z-ai/glm-5","object":"model","created":1770864286,"owned_by":"system"},{"id":"minimax/minimax-m2.5","object":"model","created":1770886840,"owned_by":"system"},{"id":"minimax/minimax-m2.7","object":"model","created":1774243880,"owned_by":"system"},{"id":"MiniMax-M1","object":"model","created":1754380642,"owned_by":"system"},{"id":"deepseek-r1-0528","object":"model","created":1754380642,"owned_by":"system"},{"id":"deepseek-r1","object":"model","created":1754380642,"owned_by":"system"},{"id":"deepseek-v3-0324","object":"model","created":1754380642,"owned_by":"system"},{"id":"doubao-1.5-thinking-pro","object":"model","created":1754380642,"owned_by":"system"},{"id":"doubao-1.5-vision-pro","object":"model","created":1754380642,"owned_by":"system"},{"id":"glm-4.5-air","object":"model","created":1754380642,"owned_by":"system"},{"id":"glm-4.5","object":"model","created":1754380642,"owned_by":"system"},{"id":"kimi-k2","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen-max-2025-01-25","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen-turbo","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen-vl-max-2025-01-25","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen2.5-vl-72b-instruct","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen2.5-vl-7b-instruct","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen3-235b-a22b","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen3-30b-a3b","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen3-32b","object":"model","created":1754380642,"owned_by":"system"},{"id":"qwen3-235b-a22b-instruct-2507","object":"model","created":1754981498,"owned_by":"system"},{"id":"qwen3-235b-a22b-thinking-2507","object":"model","created":1754981531,"owned_by":"system"},{"id":"deepseek-v3","object":"model","created":1755051424,"owned_by":"system"},{"id":"qwen3-coder-480b-a35b-instruct","object":"model","created":1755170619,"owned_by":"system"},{"id":"doubao-seed-1.6","object":"model","created":1755241785,"owned_by":"system"},{"id":"doubao-seed-1.6-flash","object":"model","created":1755242599,"owned_by":"system"},{"id":"doubao-seed-1.6-thinking","object":"model","created":1755242697,"owned_by":"system"},{"id":"deepseek-v3.1","object":"model","created":1755609557,"owned_by":"system"},{"id":"qwen3-max-preview","object":"model","created":1757139635,"owned_by":"system"},{"id":"qwen3-next-80b-a3b-thinking","object":"model","created":1757662142,"owned_by":"system"},{"id":"qwen3-next-80b-a3b-instruct","object":"model","created":1757662892,"owned_by":"system"},{"id":"deepseek/deepseek-v3.1-terminus-thinking","object":"model","created":1758547768,"owned_by":"system"},{"id":"deepseek/deepseek-v3.1-terminus","object":"model","created":1758547876,"owned_by":"system"},{"id":"qwen3-max","object":"model","created":1758693892,"owned_by":"system"},{"id":"deepseek/deepseek-v3.2-exp","object":"model","created":1759142897,"owned_by":"system"},{"id":"deepseek/deepseek-v3.2-exp-thinking","object":"model","created":1759143007,"owned_by":"system"},{"id":"z-ai/glm-4.6","object":"model","created":1760155542,"owned_by":"system"},{"id":"minimax/minimax-m2","object":"model","created":1761618594,"owned_by":"system"},{"id":"moonshotai/kimi-k2-thinking","object":"model","created":1762499974,"owned_by":"system"},{"id":"deepseek/deepseek-v3.2-251201","object":"model","created":1764587960,"owned_by":"system"},{"id":"z-ai/glm-4.7","object":"model","created":1766459139,"owned_by":"system"},{"id":"minimax/minimax-m2.1","object":"model","created":1766470220,"owned_by":"system"},{"id":"moonshotai/kimi-k2.5","object":"model","created":1769567202,"owned_by":"system"},{"id":"qwen3-30b-a3b-instruct-2507","object":"model","created":1770180413,"owned_by":"system"},{"id":"qwen3-30b-a3b-thinking-2507","object":"model","created":1770180511,"owned_by":"system"},{"id":"deepseek/deepseek-v3.2-speciale-or","object":"model","created":1774939268,"owned_by":"system"},{"id":"qwen/qwen3.5-plus","object":"model","created":1775100827,"owned_by":"system"},{"id":"qwen/qwen3.6-plus","object":"model","created":1775214276,"owned_by":"system"},{"id":"z-ai/glm-5.1","object":"model","created":1775795828,"owned_by":"system"},{"id":"moonshotai/kimi-k2.6","object":"model","created":1776736255,"owned_by":"system"},{"id":"deepseek/deepseek-v4-flash","object":"model","created":1777001016,"owned_by":"system"},{"id":"deepseek/deepseek-v4-pro","object":"model","created":1777001102,"owned_by":"system"},{"id":"tencent/hy3-preview","object":"model","created":1777426639,"owned_by":"system"},{"id":"qwen/qwen3.7-max","object":"model","created":1779431465,"owned_by":"system"},{"id":"nvidia/nemotron-3-super-120b-a12b","object":"model","created":1773712038,"owned_by":"system"}]}dddd1@zhourungui:/mnt/c/WINDOWS/system32$