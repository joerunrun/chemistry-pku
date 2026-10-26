@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "ADB=%~dp0tools\adb.exe"
set "SCRCPY_SERVER_PATH=%~dp0tools\scrcpy\scrcpy-server"
set "DEVICE_SERIAL=%~1"
set "DEVICE_COUNT=0"
if defined DEVICE_SERIAL goto prepare

"%ADB%" start-server >nul 2>&1
for /f "skip=1 tokens=1,2" %%A in ('"%ADB%" devices') do (
    if "%%B"=="device" (
        set /a DEVICE_COUNT+=1
        set "DEVICE_SERIAL=%%A"
    )
)
if %DEVICE_COUNT% EQU 1 goto prepare
echo 请只连接一副眼镜，并确认已允许 USB 调试。
goto failed

:prepare
echo 正在准备眼镜配网，请不要拔掉 USB 数据线。
"%ADB%" -s "%DEVICE_SERIAL%" shell pm list packages com.example.wifisetting | findstr /x /c:"package:com.example.wifisetting" >nul
if not errorlevel 1 goto browser
"%ADB%" -s "%DEVICE_SERIAL%" install -r "WiFi设置助手.apk"
if errorlevel 1 goto failed

:browser
"%ADB%" -s "%DEVICE_SERIAL%" shell pm list packages mark.via.gp | findstr /x /c:"package:mark.via.gp" >nul
if not errorlevel 1 goto mirror
"%ADB%" -s "%DEVICE_SERIAL%" install -r "浏览器.apk"
if errorlevel 1 goto failed

:mirror
"%ADB%" -s "%DEVICE_SERIAL%" shell input keyevent 224 >nul 2>&1
"%ADB%" -s "%DEVICE_SERIAL%" shell am start -a android.settings.WIFI_SETTINGS >nul 2>&1
start "" /b "%~dp0tools\scrcpy\scrcpy.exe" --serial "%DEVICE_SERIAL%" --no-audio --window-title "眼镜配网" --window-width 1100 >"%TEMP%\chem-glasses-network.log" 2>&1

:menu
echo.
echo 在弹出的眼镜窗口中，用鼠标和键盘操作。
echo 先连接 WiFi，再回到这里按 2 打开北大登录页。
echo 已能联网可以直接按 0。完成后请关闭投屏窗口。
echo.
echo [1] 打开 WiFi 设置
echo [2] 打开北大网络登录页
echo [0] 完成，返回助手
choice /c 120 /n /m "请选择："
if errorlevel 3 goto done
if errorlevel 2 goto portal
"%ADB%" -s "%DEVICE_SERIAL%" shell am start -a android.settings.WIFI_SETTINGS >nul 2>&1
goto menu

:portal
"%ADB%" -s "%DEVICE_SERIAL%" shell am start -a android.intent.action.VIEW -d "https://its.pku.edu.cn" -p mark.via.gp >nul 2>&1
goto menu

:failed
echo 配网没有完成。请检查 USB 连接；也可稍后重新双击“配置网络”。
pause
popd
endlocal
exit /b 1

:done
popd
endlocal
exit /b 0
