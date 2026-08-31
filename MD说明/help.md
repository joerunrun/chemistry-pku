# 常用帮助

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
cd D:\Android\projects
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
cd D:\Android\projects
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
D:\Android\projects\app\build\outputs\apk\teacher\debug\app-teacher-debug.apk
```

## 7. 常用 ADB

```powershell
D:\Android\SDK\platform-tools\adb.exe devices
D:\Android\SDK\platform-tools\adb.exe shell am start -a android.settings.WIFI_SETTINGS
D:\Android\SDK\platform-tools\adb.exe logcat -c
D:\Android\SDK\platform-tools\adb.exe logcat | Select-String "Teacher|Recording|AndroidRuntime"
```
