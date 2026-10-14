# 常用帮助

## 教师版头部停留唤醒（2026-09-22）

- 教师版增加第三种入口：待机时右上角固定显示“北”图标，无中央瞄准点。原语音和镜腿按钮入口保留。
- 进入待机或恢复 App 时，先面向正前方保持片刻建立基准；随后向右转约 12—28 度、抬头约 8—22 度，图标亮起并显示进度环，保持 3 秒后唤醒。移出范围取消进度。图标已放大并提高亮度，中心距每眼右边缘 90dp、顶部 80dp。
- 成功后需回到基准朝向附近保持 0.4 秒，才允许再次触发；拍照、录像及已唤醒状态不检测此动作。仅转动眼球不会触发。
- 基准保留到本次前台会话结束；需要调整正前方时，重新进入 App 并面向新的正前方。传感器数据中断后也会重新建立基准。
- 使用 Android 旋转矢量传感器，不启用 SLAM，不占用摄像头；缺少传感器时自动停用此入口。暂停 App 时注销传感器监听。
- 实现在 `TeacherHeadWake.kt`；角度范围、边界容差和停留时长集中在 `HeadWakeGesture.kt`。只由 `TeacherActivity` 接入，学生版未启用。
- 已通过教师版编译和单元测试，并确认连接设备存在旋转矢量传感器；实际佩戴下的轴向、触发角度、图标双眼位置及长时间漂移仍需实测调整。

## 1. 启动电脑后端

```powershell
cd D:\chemistry\chemistry_agent\chemistry
python -m pipeline.web.app --host 0.0.0.0 --port 5080
```

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:5080/api/glasses/health
```

登录页：`http://127.0.0.1:5080/login`

### 本地 Docker 测试

眼镜继续使用上面的 Python 服务（5080）。本地 Docker 使用同一个后端镜像，只在电脑的 5081 端口提供测试页面，数据保存在独立的 `.local_docker_test/runtime_assets/`，不会与 Python 服务共用记录。

启动前确保 Docker Desktop 已运行，然后在 `chemistry/` 目录执行：

```powershell
docker compose -f docker-compose.local.yml up -d --build
Invoke-RestMethod http://127.0.0.1:5081/api/glasses/health
```

浏览器打开 `http://127.0.0.1:5081/login`。改动后重复启动命令即可重新构建；测试结束执行 `docker compose -f docker-compose.local.yml down`。测试环境使用本机 `.env` 中的模型密钥，调用模型会产生实际请求；北大门户登录在本地测试页禁用。Docker 测试数据目录会保留，方便下次继续测试。

登录页的流体背景直接复用 `WebGL-Fluid-Simulation` 原项目。北大 IAAA 只接受与应用登记信息完全一致的回调地址，`http://127.0.0.1:5080/login` 通常未登记，提交它会显示“回调地址错误”。在外层 `.env` 中配置：

```dotenv
PKU_IAAA_APP_ID=hxb001
PKU_IAAA_REDIRECT_URL=https://已在北大登记的域名/回调路径
```

没有配置 `PKU_IAAA_REDIRECT_URL` 时，页面仍显示流体效果，但门户按钮保持禁用，避免把错误地址发送给北大。修改 `.env` 后需要重启后端。

## 1.1 开发目录与部署目录

- `D:\chemistry\chemistry_agent\chemistry\` 是唯一开发目录，平时只在外层修改和测试。
- `D:\chemistry\chemistry_agent\chemistry\code\` 是服务器部署副本，不直接开发。
- 完成本地验证后，先预览同步清单：

```powershell
cd D:\chemistry\chemistry_agent\chemistry
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync_to_code.ps1
```

- 确认清单后再执行复制：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync_to_code.ps1 -Apply
```

同步脚本只复制或覆盖部署代码，不删除 `code/` 中的文件，也不会复制 `.env`、`runtime_assets/`、缓存和日志。不要把外层源码移动走，否则外层 Git 会再次显示整个 `pipeline/` 被删除。

## 2. Android 版本

本项目目前有两个 App 版本，硬件均为 RayNeo ARGF20：

| 版本 | 包名 | APK |
|---|---|---|
| 学生版 | `com.example.zrgfirstapp` | `app-student-debug.apk` |
| 教师版 | `com.example.zrgfirstapp.teacher` | `app-teacher-debug.apk` |

当前使用 `debug` 构建；教师版版本名为 `1.0-teacher`。

## 3. 编译和安装

```powershell
cd D:\chemistry\chemistry_agent\android\glasses-app
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleTeacherDebug
D:\Android\SDK\platform-tools\adb.exe devices
D:\Android\SDK\platform-tools\adb.exe install -r .\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

学生版：

```powershell
.\gradlew.bat :app:assembleStudentDebug
D:\Android\SDK\platform-tools\adb.exe install -r .\app\build\outputs\apk\student\debug\app-student-debug.apk
```

首次启动按提示授予麦克风、相机权限。

## 4. USB 模式

编辑 `app/src/main/res/values/glasses_config.xml`：

```xml
<string name="glasses_connection_mode">usb</string>
<string name="glasses_service_base_url_usb">http://127.0.0.1:5080</string>
```

启动后端，再执行：

```powershell
D:\Android\SDK\platform-tools\adb.exe reverse tcp:5080 tcp:5080
D:\Android\SDK\platform-tools\adb.exe reverse --list
```

改 XML 后必须重新编译并安装 APK。

## 5. IP/Wi-Fi 模式

电脑和眼镜连接同一局域网，后端监听 `0.0.0.0`。把配置改为：

```xml
<string name="glasses_connection_mode">network</string>
<string name="glasses_service_base_url_network">http://电脑IPv4</string>
```

Docker/Nginx 使用 80 端口；直接运行 Python 使用 `http://电脑IPv4:5080`。改完后重新编译安装。查询电脑 IP：

```powershell
ipconfig
```

无线失败时检查 Windows 防火墙、网络隔离和电脑服务是否监听 `0.0.0.0`。

## 6. 教师版是否最新、如何安装

当前连接设备已安装教师包，但 ADB 显示的安装时间早于电脑最新构建，不能视为最新版本。重新构建并覆盖安装：

```powershell
cd D:\chemistry\chemistry_agent\android\glasses-app
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleTeacherDebug
D:\Android\SDK\platform-tools\adb.exe install -r .\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

查看设备上的教师包：

```powershell
D:\Android\SDK\platform-tools\adb.exe shell dumpsys package com.example.zrgfirstapp.teacher | Select-String "versionName|lastUpdateTime"
```

最新教师 APK：

```text
D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

## 7. 常用 ADB

```powershell
D:\Android\SDK\platform-tools\adb.exe devices
D:\Android\SDK\platform-tools\adb.exe shell am start -a android.settings.WIFI_SETTINGS
D:\Android\SDK\platform-tools\adb.exe logcat -c
D:\Android\SDK\platform-tools\adb.exe logcat | Select-String "Teacher|Recording|AndroidRuntime"
```

## 8. 新眼镜接入校园网的完整流程

下面以一副全新的 RayNeo ARGF20 为例。学生版和教师版包名不同，可以同时安装；WiFi 设置助手用于打开系统 WiFi 设置和内置校园网认证页面。当前眼镜编号范围为 01—12。

### 8.1 用 USB 连接并确认硬件

打开眼镜的 USB 调试，用数据线连接电脑，然后执行：

```powershell
D:\Android\SDK\platform-tools\adb.exe devices -l
D:\Android\SDK\platform-tools\adb.exe shell settings get secure android_id
```

第一条命令应显示一台状态为 `device` 的 `RayNeoX3Pro / ARGF20`。第二条命令显示的是 ADB shell 读取到的 Android ID，仅用于辅助检查，不要直接拿它填写后台映射。

后台实际使用的是 APK 上报的 `teacher_android_...`。最可靠的检查方法是让当前眼镜在本地模式拍摄并成功上传一张照片，然后在 PowerShell 中执行：

```powershell
$photo = Get-ChildItem D:\chemistry\chemistry_agent\chemistry\runtime_assets\teacher_photos -Filter photo.json -Recurse |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

(Get-Content $photo.FullName -Raw | ConvertFrom-Json).device_id
```

命令输出的 `teacher_android_...` 才是眼镜上传数据时携带的设备 ID，可用于后台眼镜编号校准。这条命令只读取最近一次成功保存到本地的教师照片，因此只代表最后上传的那副眼镜；如果本次上传失败，它会读到之前的旧照片。检查多副眼镜时，每次只操作一副，并在拍照成功后立即执行该命令。

新版 APK 启动时会直接打印实际设备 ID。打开学生版或教师版 App 后执行：

```powershell
D:\Android\SDK\platform-tools\adb.exe logcat -d | Select-String "DeviceIdentity"
```

### 8.2 确认业务 App 使用校园网服务器

打开：

```text
D:\chemistry\chemistry_agent\android\glasses-app\app\src\main\res\values\glasses_config.xml
```

应保持：

```xml
<string name="glasses_connection_mode">network</string>
<string name="glasses_service_base_url_network">http://162.105.209.134</string>
```

`network` 表示眼镜直接通过校园网访问服务器，不再依赖 USB 和 `adb reverse`。

### 8.3 编译三个 APK

先编译 WiFi 设置助手：

```powershell
cd D:\chemistry\chemistry_agent\android\wifi-setting
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleDebug
```

再编译学生版和教师版：

```powershell
cd D:\chemistry\chemistry_agent\android\glasses-app
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleStudentDebug :app:assembleTeacherDebug
```

生成的 APK 分别是：

```text
D:\chemistry\chemistry_agent\android\wifi-setting\app\build\outputs\apk\debug\app-debug.apk
D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\student\debug\app-student-debug.apk
D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

### 8.4 安装 WiFi 助手、学生版和教师版

```powershell
D:\Android\SDK\platform-tools\adb.exe install -r D:\chemistry\chemistry_agent\android\wifi-setting\app\build\outputs\apk\debug\app-debug.apk
D:\Android\SDK\platform-tools\adb.exe install -r D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\student\debug\app-student-debug.apk
D:\Android\SDK\platform-tools\adb.exe install -r D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

三个命令都显示 `Success` 即安装完成。安装后眼镜应用列表中应出现“WiFi设置助手”“化学实验识别”和“教师实验助手”。

### 8.5 连接 PKU 并由用户完成认证

1. 戴上并解锁眼镜，进入“WiFi设置助手”。
2. 选择“打开 WiFi 设置”，连接开放网络 `PKU`。
3. 返回“WiFi设置助手”，选择“打开校园网认证”。
4. 在内置认证页面中由实际用户输入自己的北大账号并完成认证；账号和密码不写入项目配置，也不由管理员预先填写。

如果内置认证页面无法显示，可安装 `D:\rayneo-dev\browser.apk`，再从 WiFi 设置助手打开校园网认证页面。

可用下面的命令检查无线连接：

```powershell
D:\Android\SDK\platform-tools\adb.exe shell cmd wifi status
```

出现 `Wifi is connected to "PKU"` 只表示已经连接无线接入点，并不等于完成门户认证。未认证时，访问服务器会被重定向到 `https://its.pku.edu.cn`；用户认证成功后才可正常访问课程助手服务器。

### 8.6 在网页后台校准眼镜编号

1. 确认服务器已经部署支持当前眼镜编号范围的代码。
2. 让眼镜打开学生版或教师版并访问一次服务器，使后台发现它的稳定硬件编号。
3. 管理员打开 `http://162.105.209.134/login`，点击管理员入口进入统一工作台。
4. 在“眼镜编号校准”中找到新硬件，选择眼镜外壳所贴的 10 号并保存。

编号只代表物理眼镜，不永久绑定学生或教师。学生和教师仍在下课后按实际使用时间认领数据。

### 8.7 最终验收

- 学生版能够进入课程助手并向 `http://162.105.209.134` 上传学生数据。
- 教师版启动后能听到“助手就绪，请说指令”，并能上传教师录音、照片或录像。
- 网页工作台能够按校准后的眼镜编号看到待认领数据。
- 拔掉 USB 后仍能使用，说明当前运行的是校园网版本；如果只有插着 USB 才能工作，应重新检查 `glasses_connection_mode` 是否为 `network` 并重新编译安装。
