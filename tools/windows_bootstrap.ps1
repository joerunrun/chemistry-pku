param(
    [ValidateSet('Network', 'Install')][string]$Mode = 'Network',
    [ValidateSet('student', 'teacher')][string]$Role = 'student',
    [string]$DeviceSerial = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$packageRoot = Split-Path $PSScriptRoot -Parent
$env:ADB = Join-Path $PSScriptRoot 'adb-helper.exe'
$env:SCRCPY_SERVER_PATH = Join-Path $PSScriptRoot 'scrcpy\scrcpy-server'

function Invoke-Adb {
    & $env:ADB @args
    if ($LASTEXITCODE -ne 0) { throw 'ADB 操作失败，请检查数据线和 USB 调试授权。' }
}

function Open-Wifi {
    Invoke-Adb -s $script:DeviceSerial shell -n am start -a android.settings.WIFI_SETTINGS | Out-Null
}

function Set-UpNetwork {
    foreach ($app in @(@('com.example.wifisetting', 'WiFi设置助手.apk'), @('mark.via.gp', '浏览器.apk'))) {
        $installed = @(Invoke-Adb -s $script:DeviceSerial shell -n pm list packages $app[0])
        if ($installed -notcontains "package:$($app[0])") {
            Write-Host "正在安装 $($app[1])……"
            Invoke-Adb -s $script:DeviceSerial install -r (Join-Path $packageRoot $app[1]) | Out-Host
        }
    }
    Invoke-Adb -s $script:DeviceSerial shell -n input keyevent 224 | Out-Null
    Open-Wifi
    $outputLog = Join-Path $env:TEMP 'chem-glasses-network.log'
    $errorLog = Join-Path $env:TEMP 'chem-glasses-network.error.log'
    $mirror = Start-Process -FilePath (Join-Path $PSScriptRoot 'scrcpy\scrcpy.exe') -WorkingDirectory $packageRoot `
        -ArgumentList @('--serial', $script:DeviceSerial, '--no-audio', '--window-title', '眼镜配网', '--window-width', '1100') `
        -WindowStyle Hidden -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -PassThru
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        $mirror.Refresh()
        if ($mirror.HasExited) { break }
        if ($mirror.MainWindowHandle -ne [IntPtr]::Zero) { $ready = $true; break }
    }
    if (!$ready) {
        if (!$mirror.HasExited) { Stop-Process -Id $mirror.Id -Force }
        Get-Content $outputLog, $errorLog -Tail 12 -ErrorAction SilentlyContinue | Out-Host
        throw '投屏窗口没有启动，请根据上方错误检查连接，再重新运行“配置网络”。'
    }
    Write-Host "`n投屏窗口已打开，请用鼠标和键盘操作。"
    Write-Host '先连接 WiFi，再按 2 打开北大登录页；完成后关闭投屏窗口并按 0。'
    while ($true) {
        Write-Host "`n[1] 打开 WiFi 设置   [2] 打开北大网络登录页   [0] 完成"
        Write-Host '请选择：' -NoNewline
        $selection = [Console]::ReadLine()
        if ($null -eq $selection) { return }
        switch ($selection) {
            '0' { return }
            '1' { Open-Wifi }
            '2' { Invoke-Adb -s $script:DeviceSerial shell -n am start -a android.intent.action.VIEW -d https://its.pku.edu.cn -p mark.via.gp | Out-Null }
        }
    }
}

try {
    Invoke-Adb start-server | Out-Null
    $devices = @(Invoke-Adb devices)
    if ($DeviceSerial) {
        if (!($devices -match ('^' + [regex]::Escape($DeviceSerial) + '\s+device\b'))) {
            throw '指定的眼镜未连接或未授权，请重新连接并允许 USB 调试。'
        }
    } else {
        $connected = @($devices | Select-String '^(\S+)\s+device\b')
        if ($connected.Count -ne 1) { throw '请只连接一副眼镜，并在眼镜中允许 USB 调试。' }
        $script:DeviceSerial = $connected[0].Matches[0].Groups[1].Value
    }
    if ($Mode -eq 'Install') {
        $label = if ($Role -eq 'teacher') { '教师' } else { '学生' }
        $appPackage = 'com.example.zrgfirstapp' + $(if ($Role -eq 'teacher') { '.teacher' } else { '' })
        Write-Host "正在安装化小北${label}版，请不要拔线……"
        Invoke-Adb -s $DeviceSerial install -r (Join-Path $packageRoot "化小北${label}版.apk") | Out-Host
        Invoke-Adb -s $DeviceSerial shell -n appops set $appPackage REQUEST_INSTALL_PACKAGES allow | Out-Null
        Write-Host '助手安装成功，接下来配置网络。'
    }
    Set-UpNetwork
    if ($Mode -eq 'Install') {
        Invoke-Adb -s $DeviceSerial shell -n monkey -p $appPackage -c android.intent.category.LAUNCHER 1 | Out-Null
        Write-Host '已完成，可以拔掉数据线。'
        Read-Host '按回车关闭' | Out-Null
    }
    exit 0
} catch {
    Write-Host "`n$($_.Exception.Message)" -ForegroundColor Red
    Read-Host '按回车关闭' | Out-Null
    exit 1
}
