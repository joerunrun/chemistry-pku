"""给现有教师/学生一键更新包加入配网工具，不改动主 APK。"""
import argparse
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scrcpy", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    args = parser.parse_args()
    android = Path(__file__).resolve().parents[2]
    release = android.parent / "release"
    helper = android / "wifi-setting/app/build/outputs/apk/debug/app-debug.apk"
    network = Path(__file__).with_name("network_setup.bat").read_text(encoding="utf-8")

    for role in ("教师", "学生"):
        folder = release / f"化小北{role}版一键更新包"
        shutil.copy2(helper, folder / "WiFi设置助手.apk")
        shutil.copy2(args.browser, folder / "浏览器.apk")
        mirror = folder / "tools/scrcpy"
        mirror.mkdir(parents=True, exist_ok=True)
        for source in args.scrcpy.iterdir():
            if source.suffix == ".dll" or source.name in (
                "scrcpy.exe", "scrcpy-server", "scrcpy.png", "disconnected.png", "LICENSE"
            ):
                if source.name not in ("AdbWinApi.dll", "AdbWinUsbApi.dll"):
                    shutil.copy2(source, mirror / source.name)
        shutil.copy2(Path(__file__).with_name("LICENSE.scrcpy"), mirror / "LICENSE")
        with (folder / "配置网络.bat").open("w", encoding="utf-8", newline="\r\n") as out:
            out.write(network)

        installer = folder / "安装更新.bat"
        text = installer.read_text(encoding="utf-8")
        if 'call "%~dp0配置网络.bat"' not in text:
            marker = "echo 安装成功！现在可以拔掉数据线。"
            text = text.replace(marker, 'call "%~dp0配置网络.bat" "%DEVICE_SERIAL%"\n' + marker)
        with installer.open("w", encoding="utf-8", newline="\r\n") as out:
            out.write(text)
        instructions = f"""化小北{role}版一键更新包（Windows）

1. 完整解压压缩包。眼镜开机、开启 ADB，用 USB 数据线连接电脑。
2. 双击“安装更新”，如眼镜询问 USB 调试权限，请允许。
3. 安装后会弹出眼镜投屏窗口，在里面用鼠标、键盘连接 WiFi。
4. 回到安装窗口按 2，打开北大网络登录页，在投屏窗口输入自己的账号密码并连接。
5. 完成后关闭投屏窗口，回安装窗口按 0，助手会自动打开，此时可拔线。

已经联网可直接按 0 跳过。以后单独配网，双击“配置网络”即可。
更新保留原有助手数据。WiFi 助手和 Via 浏览器缺少时才安装，已有则跳过。
后续助手启动时会检查新版，下载后按眼镜安装提示确认。
无需在电脑另外安装 ADB、浏览器或投屏软件；认证实际在眼镜上完成。
如果投屏没有弹出，请查看 %TEMP%\\chem-glasses-network.log。
"""
        (folder / "安装说明.txt").write_text(instructions, encoding="utf-8-sig")
        (mirror / "第三方软件.txt").write_text(
            "scrcpy：https://github.com/Genymobile/scrcpy （Apache-2.0，见 LICENSE）\n"
            "随附运行库：SDL3（zlib）、FFmpeg（以对应构建许可为准）、libusb（LGPL-2.1）。\n"
            "来源及许可：https://libsdl.org/ 、https://ffmpeg.org/legal.html 、https://libusb.info/\n"
            "Via 浏览器：https://viayoo.com/ ，WiFi 设置助手为本项目应用。\n",
            encoding="utf-8-sig",
        )
        archive = folder.with_suffix(".zip")
        with ZipFile(archive, "w", ZIP_DEFLATED) as out:
            for file in sorted(folder.rglob("*")):
                if file.is_file():
                    out.write(file, file.relative_to(folder.parent))
        print(f"{archive.name}: {archive.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
