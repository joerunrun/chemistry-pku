@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_bootstrap.ps1" -Mode Install -Role __ROLE__
exit /b %errorlevel%
