# 智能眼镜实验识别 App 调试指南

## 教／学图标及 USB 学生版

学生版使用蓝底“学”，教师版使用橙底“教”；图标按角色分别提供普通与自适应资源。

编译 USB 学生版（包名仍为 `com.example.zrgfirstapp`，覆盖安装原学生版）：

```powershell
.\gradlew.bat -PstudentUsb :app:assembleStudentDebug
```

不带参数构建学生版时，继续读取 `glasses_config.xml` 的连接模式。`-PstudentTest` 仍可构建包名带 `.naturaltest` 的独立 USB 测试版。教师版的网络配置不受这些参数影响。

本地 Docker 后端在 5081 时，连接眼镜后执行 `adb reverse tcp:5080 tcp:5081`；本机 Python 后端在 5080 时，改为 `adb reverse tcp:5080 tcp:5080`。

这个项目是眼镜端 Android App。电脑端负责运行化学实验 pipeline 服务，眼镜端只负责拍照、上传图片、轮询结果并显示文字反馈。

## 1. 重启电脑后如何启动电脑服务

电脑端代码在：

```text
D:\chemistry\chemistry_agent\chemistry
```

打开 PowerShell 后运行：

```powershell
cd D:\chemistry\chemistry_agent\chemistry
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
cd D:\chemistry\chemistry_agent\android\glasses-app
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
D:\chemistry\chemistry_agent\android\glasses-app\app\build\outputs\apk\debug\app-debug.apk
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
D:\chemistry\chemistry_agent\android\glasses-app\app\src\main\res\values\glasses_config.xml  

<string name="glasses_service_base_url">http://172.20.10.2:5080</string>
改成电脑ip


D:\Android\SDK\platform-tools\adb.exe shell am start -a android.settings.WIFI_SETTINGS


## 眼镜内更新（2.1，versionCode 3）

2.2（versionCode 4）：两版单击镜腿直接拍照保存，录像时单击也拍照，不停止录像。教师取消唤醒菜单及转头唤醒入口，持续收音按语句送 `teacher_direct` 一次判断拍照／录像／停止／no_action，持续录音原件照常保存。停止录像改用语音。连接、权限与更新页面的确认操作保留；正在拍照或处理任务时不重复启动拍照。

2.2 教师端需要后端健康接口包含 `teacher_direct_control`。先部署 `chemistry/code_deploy.zip`，再发布 `release/服务器部署/glasses-update-2.2.zip` 的 APK。打包可运行 `tools/package_app_release.py`，随后运行 `tools/package_network_bootstrap.py` 重建含配网工具的首装 ZIP。已安装 2.1 的联网用户可在服务器发布后直接下载更新，仍需按系统提示确认安装。

2.2（versionCode 4）：两版单击镜腿直接拍照保存，录像时单击也拍照，不停止录像。教师取消唤醒菜单及转头唤醒入口，持续收音按语句送 `teacher_direct` 一次判断拍照／录像／停止／no_action，持续录音原件照常保存。停止录像改用语音。连接、权限与更新页面的确认操作保留；正在拍照或处理任务时不重复启动拍照。

2.2 教师端需要后端健康接口包含 `teacher_direct_control`。先部署 `chemistry/code_deploy.zip`，再发布 `release/服务器部署/glasses-update-2.2.zip` 的 APK。打包可运行 `tools/package_app_release.py`，随后运行 `tools/package_network_bootstrap.py` 重建含配网工具的首装 ZIP。已安装 2.1 的联网用户可在服务器发布后直接下载更新，仍需按系统提示确认安装。

教师、学生联网版启动时检查新版本；空闲时长按触摸板可手动检查。下载后核对摘要、包名和版本，再打开系统安装界面。首次 USB 安装包会尝试开启安装来源许可，系统仍可能要求确认安装。USB 开发包不自动检查正式版。

首装文件夹及同名 ZIP 位于 `chemistry_agent/release/化小北教师版一键更新包`、`化小北学生版一键更新包`。完整解压后双击 `安装更新.bat`，两版均直接连接服务器。后续必须递增 versionCode 并沿用当前包名和签名密钥。


## 语音识别指示（学生 2.6.7、助教 2.5.5）

平时隐藏，每次实际上传语音判断时，两眼右上角各显示一个浅蓝色“北”，每秒缓慢明暗闪烁。距每眼右侧 48dp、顶部 40dp；请求结束、失败或取消后隐藏，离开页面也隐藏。只表示正在处理音频，不表示已识别出指令，不额外播报。所有有效语音都可能触发，包括最终判为 no_action 的片段。

录音／录像中使用左镜腿手动拍照。2026-11-18 已从提交 `cb92aff` 发布两版 APK，versionCode 均为 18。公网完整下载和 SHA256 校验通过，部署无需重启后台。

## 快捷拍照（学生 2.6.6、助教 2.5.4，本地测试版）

首次进入按双眼提示单击左镜腿绑定拍照键，保存物理输入设备标识；此后左侧单击直接拍照，右侧保留确认和返回。两版独立完成首次绑定。没有真实点击日志，尚未确认该固件左侧点击能否传到应用。

拍照显示独立的“正在拍照”提示，保存成功后显示“拍照完成，正在上传”，约两秒自动收起；后台上传不占用结果页，拍照不结束录音或录像。教师去掉拍照后的固定 700ms 等待，学生沿用相机低延迟模式。CameraX 原本已绑定录像及拍照用例，实际同时拍照仍需硬件验证。

录音／录像期间仅通过左镜腿点击拍照，语音仍只接受当前任务的结束指令；不放开录制中的语音拍照。左侧快捷拍照不走模型，无需配套更新后台。右上角判断指示图标尚未增加。

## 跟随眼镜系统音量（学生 2.6.5、助教 2.5.3）

2026-11-17 实机发现 RayNeo 桌面的音量操作直接修改 STREAM_SYSTEM，媒体音量没有随之变化。两版在前台每 300ms 检查系统音量变化，按档位比例同步到播放用的媒体通道；离开助手停止同步。标准音量键同样修改系统通道。此修改会影响设备共享的媒体音量。

## 助手音量统一（学生 2.6.4、助教 2.5.2）

两版提示语、播报和视频统一使用媒体音量；进入助手后将硬件音量控制指定为媒体音量，并处理标准的音量加减键。学生的滑动选择、阅读翻页和助教单击拍照沿用原来的操作。

左镜腿需要由眼镜系统产生标准音量事件，或遵循当前媒体音量控制通道；当前没有连接眼镜，尚未验证实际左镜腿事件，不能据此保证所有固件。安装对应新 APK 后需实测左镜腿，并通过媒体音量变化和 `AssistantVolume` 日志确认。此版本不修改音量初始值或系统环境声。

2026-11-11 已发布学生 `2.6.4-student`、助教 `2.5.2-teacher`，versionCode 均为 15，代码提交 `7e41b3b`。两版公网完整下载与 SHA256 校验通过，原有签名保持一致。联网重新打开助手或空闲长按检查更新后可安装。此次只发布 APK，后台未重启；左镜腿仍待实机验证。

## 学生版 2.6.3：三分钟提醒

学生助手连接成功进入待机后开始计时，每次进入只提醒一次。三分钟后双眼显示“你已经做了三分钟实验”，同时播放内置语音，文字约六秒后自动消失；遇到视频或其他提示音播放时，结束后再提醒。切到后台时不弹出，返回后已到期的提醒会补发。

学生待机时单击镜腿不拍照，仍可语音拍照、单击选择视频；助教待机时保留单击拍照。此功能只需更新学生 APK，后端无需修改。

## 本地 USB 测试（教师和学生）

运行 `./gradlew.bat -PteacherUsb -PstudentUsb :app:assembleTeacherDebug :app:assembleStudentDebug`。两版复用正式包名覆盖安装，保留数据，版本名带 `-usb`，跳过在线更新，只访问眼镜 `127.0.0.1:5080`。本机启动 Python 后端 5080 后运行 `adb reverse tcp:5080 tcp:5080`；也可将眼镜 5080 转发到已启动的 Docker 5081。测试包放在 `release/本地USB测试_2.2/`，正式安装包和在线发布包保持分开。恢复正式版时，重新安装 release 的联网 APK 即可。

学生版录制提示音分别为“开始录音／结束录音”和“开始录制视频／结束录制视频”，使用四个内置音频，开始成功及结束保存后播放。上传进度继续显示文字；问答仍仅显示文字，不自动朗读。

2.3（versionCode 5）：学生单击和语音“拍照”统一进入 `requestPhoto()`，共用开始提示、倒计时、拍照、保存及上传流程。拍照成功播放内置“拍照完成”，先显示“照片上传中”，收到真实上传回执后显示“照片已上传”；未上传成功则提示自动重试。教师单击和语音拍照也共用原动作入口，成功后补充完成提示音。旧上传失败资料继续保留并重试，问答仍只显示文字。
