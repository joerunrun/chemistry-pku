"""Publish a prepared APK release beside existing runtime assets, without replacing other data."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def publish(source: Path, target: Path) -> None:
    with urlopen("http://127.0.0.1/api/glasses/health", timeout=10) as response:
        health = json.load(response)
    if "teacher_direct_control" not in health.get("features", []):
        raise ValueError("Deploy the new code_deploy.zip before publishing APKs.")
    releases = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    prepared = []
    for role in ("teacher", "student"):
        info = releases[role]
        apk = (source / f"{role}.apk").read_bytes()
        if len(apk) != info["size_bytes"] or hashlib.sha256(apk).hexdigest() != info["sha256"]:
            raise ValueError(f"Incomplete APK: {role}")
        folder = target / "runtime_assets/app_updates" / role
        current_path = folder / "latest.json"
        if current_path.exists() and json.loads(current_path.read_text(encoding="utf-8"))["version_code"] > info["version_code"]:
            raise ValueError(f"A newer {role} release is already published.")
        destination = folder / f"{role}-{int(info['version_code'])}.apk"
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError(f"Published version cannot be replaced: {destination.name}")
        prepared.append((folder, destination, apk, info))
    for folder, destination, apk, info in prepared:
        folder.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            part = folder / "download.part"
            part.write_bytes(apk)
            part.replace(destination)
        part = folder / "latest.json.part"
        part.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        part.replace(folder / "latest.json")
        print(f"Published {folder.name}: {info['version_name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=Path(__file__).resolve().parent)
    parser.add_argument("--target", type=Path, default=Path("/projects/chemistry-glasses"))
    args = parser.parse_args()
    publish(args.source, args.target)
