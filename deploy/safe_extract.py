"""Safely deploy a code zip without touching persistent runtime data.

The server project keeps ``runtime_assets`` and ``.env`` beside the code.
Ordinary unzip commands can overwrite files with the same relative path, so
this tool always skips those protected paths.  It also backs up the small,
critical metadata files before updating code and never deletes target files.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from zipfile import BadZipFile, ZipFile, ZipInfo


PROTECTED_TOP_LEVEL = {".env", "runtime_assets"}
CRITICAL_BACKUPS = (
    ".env",
    "runtime_assets/auth/identity.sqlite3",
    "runtime_assets/device_mappings.json",
)


def _safe_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"压缩包包含不安全路径: {name}")
    return path.parts


def _is_symlink(member: ZipInfo) -> bool:
    mode = member.external_attr >> 16
    return stat.S_ISLNK(mode)


def _archive_prefix(members: list[ZipInfo]) -> str | None:
    file_parts = [_safe_parts(member.filename) for member in members if not member.is_dir()]
    if file_parts and all(len(parts) > 1 and parts[0] == "code" for parts in file_parts):
        return "code"
    return None


def _relative_parts(member: ZipInfo, prefix: str | None) -> tuple[str, ...]:
    parts = _safe_parts(member.filename)
    if prefix and parts[0] == prefix:
        parts = parts[1:]
    if not parts:
        raise ValueError(f"压缩包成员缺少相对路径: {member.filename}")
    return parts


def _backup_critical_files(target: Path, backup_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    destination = backup_root / timestamp
    copied = 0
    for relative in CRITICAL_BACKUPS:
        source = target.joinpath(*PurePosixPath(relative).parts)
        if not source.is_file():
            continue
        backup = destination.joinpath(*PurePosixPath(relative).parts)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
        copied += 1
    if copied:
        print(f"已备份 {copied} 个关键数据文件到: {destination}")
    return destination


def deploy_archive(
    archive: Path,
    target: Path,
    *,
    backup_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    archive = archive.resolve()
    target = target.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"找不到部署压缩包: {archive}")
    if not target.is_dir():
        raise NotADirectoryError(f"部署目标不是目录: {target}")
    backup_root = (backup_root or target.parent / f"{target.name}_deploy_backups").resolve()

    with ZipFile(archive) as zipped:
        members = zipped.infolist()
        prefix = _archive_prefix(members)
        selected: list[tuple[ZipInfo, tuple[str, ...]]] = []
        skipped = 0
        for member in members:
            if member.is_dir():
                continue
            if _is_symlink(member):
                raise ValueError(f"压缩包不允许包含符号链接: {member.filename}")
            parts = _relative_parts(member, prefix)
            if parts[0] in PROTECTED_TOP_LEVEL:
                skipped += 1
                continue
            selected.append((member, parts))

        print(f"待更新代码文件: {len(selected)}；受保护而跳过: {skipped}")
        if dry_run:
            return {"updated": 0, "protected": skipped, "planned": len(selected)}

        _backup_critical_files(target, backup_root)
        with tempfile.TemporaryDirectory(prefix="chemistry_deploy_") as temporary:
            staging = Path(temporary)
            for member, parts in selected:
                staged = staging.joinpath(*parts)
                staged.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, staged.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
            for _, parts in selected:
                staged = staging.joinpath(*parts)
                destination = target.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged, destination)

    print("代码更新完成；.env 和 runtime_assets 未被读取、覆盖或删除。")
    return {"updated": len(selected), "protected": skipped, "planned": len(selected)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安全解压代码包，强制保护服务器 .env 与 runtime_assets。"
    )
    parser.add_argument("archive", type=Path, help="code.zip 路径")
    parser.add_argument("--target", type=Path, default=Path.cwd(), help="服务器项目目录")
    parser.add_argument("--backup-root", type=Path, help="关键数据备份目录")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写入")
    args = parser.parse_args()
    try:
        deploy_archive(
            args.archive,
            args.target,
            backup_root=args.backup_root,
            dry_run=args.dry_run,
        )
    except (BadZipFile, OSError, ValueError) as exc:
        parser.exit(1, f"安全部署失败: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
