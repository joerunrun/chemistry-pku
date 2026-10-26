@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_bootstrap.ps1" -Mode Network -DeviceSerial "%~1"
exit /b %errorlevel%
