"""Refresh USB bootstrap APKs and prepare a separate server publication bundle."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse
import hashlib
import json
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--server-only", action="store_true", help="Keep the existing USB bootstrap APKs unchanged")
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
project = root.parents[1]
release = project / "release"
metadata = {}
apks = {}
for role, label in (("teacher", "教师"), ("student", "学生")):
    output = root / f"app/build/outputs/apk/{role}/debug"
    build = json.loads((output / "output-metadata.json").read_text(encoding="utf-8"))
    element = build["elements"][0]
    if "-usb" in element["versionName"]:
        raise SystemExit("Build network APKs without teacherUsb/studentUsb/studentTest before publishing.")
    apk = output / element["outputFile"]
    apks[role] = apk
    if not args.server_only:
        shutil.copy2(apk, release / f"化小北{label}版一键更新包/化小北{label}版.apk")
    metadata[role] = {
        "package_name": build["applicationId"], "version_code": element["versionCode"],
        "version_name": element["versionName"], "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
        "size_bytes": apk.stat().st_size,
        "notes": "在线更新测试：仅递增版本号，功能与 2.5 相同。" if element["versionName"].startswith("2.5.1-")
                 else "保留双眼更新确认，确认后优先直接安装，支持的系统无需第二次安装确认。",
    }
version = str(metadata["teacher"]["version_name"]).removesuffix("-teacher")
name = f"glasses-update-{version}"
server_release = release / "服务器部署"
server_release.mkdir(exist_ok=True)
instructions = f"""眼镜在线更新 {version}

先按老规则部署 chemistry/code_deploy.zip，再发布本包的 APK。
本包单独存放，不要放入 code_deploy.zip 的 runtime_assets 目录。
上传本 ZIP 到服务器项目目录后执行：

cd /projects/chemistry-glasses
unzip {name}.zip -d /tmp
sudo python3 /tmp/{name}/publish_app_updates.py

脚本只向 runtime_assets/app_updates 写入两个新 APK 和各自的 latest.json。
不删除旧 APK，不覆盖数据库、录制资料或 .env，不生成备份。
服务器 .env 如配置 CHEM_RUNTIME_ASSETS_HOST_DIR 为其他路径，应将脚本 --target 指向对应父目录。
默认路径适用于现用服务器 /projects/chemistry-glasses/runtime_assets。

已装带更新功能版本的用户：眼镜联网后重新打开助手，发现新版后选择“立即更新”。
2.3／2.4 首次升级修正版需电脑协助安装一次；2.5 起在双眼页面确认后，支持的系统可直接安装。
首次可能需要允许助手安装应用，USB 一键安装脚本会为本助手设置安装来源许可。
空闲时长按镜腿也可进入更新页面。联网正式版可更新，USB 测试包不自动更新。
安装完成后重新打开助手。保留双眼页面上的用户确认，不模拟点击系统按钮；其他系统仍可能要求系统确认。
"""
bundle = server_release / f"{name}.zip"
with ZipFile(bundle, "w", ZIP_DEFLATED) as zipped:
    for role, apk in apks.items():
        zipped.write(apk, f"{name}/{role}.apk")
    zipped.writestr(f"{name}/manifest.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    zipped.write(project / "chemistry/deploy/publish_app_updates.py", f"{name}/publish_app_updates.py")
    zipped.writestr(f"{name}/发布说明.txt", instructions.encode("utf-8-sig"))
print(f"Prepared {bundle}: {bundle.stat().st_size / 1024 / 1024:.1f} MB")
