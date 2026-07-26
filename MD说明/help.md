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
