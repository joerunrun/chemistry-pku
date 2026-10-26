"""Build a portable Windows mirroring/WiFi kit, without the main assistant APKs."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scrcpy', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[3]
    release = project / 'release'
    existing = release / '化小北教师版一键更新包'
    target = release / '眼镜配网工具_Windows64'
    mirror = target / 'scrcpy'
    fallback = target / '备用浏览器'
    mirror.mkdir(parents=True, exist_ok=True)
    fallback.mkdir(exist_ok=True)
    for source in args.scrcpy.iterdir():
        if source.suffix.lower() == '.dll' or source.name in (
            'scrcpy.exe', 'scrcpy-server', 'scrcpy.png', 'disconnected.png', 'adb.exe'
        ):
            shutil.copy2(source, mirror / ('adb-safe.exe' if source.name == 'adb.exe' else source.name))
    shutil.copy2(Path(__file__).with_name('LICENSE.scrcpy'), mirror / 'LICENSE')
    shutil.copy2(existing / 'tools/scrcpy/第三方软件.txt', mirror / '第三方软件.txt')
    shutil.copy2(existing / 'WiFi设置助手.apk', target / 'wifi-helper.apk')
    shutil.copy2(existing / '浏览器.apk', fallback / 'browser.apk')

    scripts = {
        '1-打开投屏.cmd': r'''@echo off
setlocal
set "ADB=%~dp0scrcpy\adb-safe.exe"
set "SCRCPY_SERVER_PATH=%~dp0scrcpy\scrcpy-server"
"%~dp0scrcpy\scrcpy.exe" --no-audio %*
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
''',
        '2-安装WiFi助手.cmd': r'''@echo off
setlocal
"%~dp0scrcpy\adb-safe.exe" install -r "%~dp0wifi-helper.apk"
if errorlevel 1 goto done
"%~dp0scrcpy\adb-safe.exe" shell -n am start -n com.example.wifisetting/.MainActivity
:done
pause
''',
        '备用浏览器/安装浏览器.cmd': r'''@echo off
setlocal
"%~dp0..\scrcpy\adb-safe.exe" install -r "%~dp0browser.apk"
if errorlevel 1 goto done
"%~dp0..\scrcpy\adb-safe.exe" shell -n am start -a android.intent.action.VIEW -d https://its.pku.edu.cn -p mark.via.gp
:done
pause
''',
    }
    for name, text in scripts.items():
        # Keep CMD files ASCII-only; Chinese UI belongs to the existing Android app.
        (target / name).write_bytes(text.replace('\n', '\r\n').encode('ascii'))
    (target / '使用说明.txt').write_text('''眼镜配网工具（Windows 64 位）

1. 完整解压 ZIP，不能只取出 scrcpy.exe。眼镜开机、开启 ADB，只连接一副眼镜；首次在眼镜上允许 USB 调试。
2. 双击“1-打开投屏.cmd”，电脑会显示眼镜画面，声音保留在眼镜。
3. 首次使用双击“2-安装WiFi助手.cmd”，安装后会自动打开 WiFi 助手。以后直接在眼镜桌面打开即可。
4. 在投屏窗口用键盘左右方向键选择、回车确认。先进入 WiFi 设置连接 PKU，再返回 WiFi 助手进入校园网认证，用鼠标键盘输入自己的北大账号密码。
5. 完成后关闭投屏、拔掉 USB，再打开“学”或“教”。

如果校园网登录页打不开，运行“备用浏览器/安装浏览器.cmd”，会安装并打开备用浏览器的北大登录页。
如果提示找不到设备或 unauthorized，检查数据线、USB 调试授权和电脑的 ADB 驱动。
电脑无需安装 Python、Android Studio，也无需配置环境变量。
本包只负责投屏与配网，不安装或更新学生、教师助手。
''', encoding='utf-8-sig')
    archive = target.with_suffix('.zip')
    with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(release))
    print(f'{archive}\n{archive.stat().st_size / 1024 / 1024:.1f} MB')


if __name__ == '__main__':
    main()
