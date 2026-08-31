from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from deploy.safe_extract import deploy_archive


def _write_zip(path: Path, files: dict[str, str]) -> None:
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_protected_data_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "chemistry-glasses"
    target.mkdir()
    (target / "runtime_assets" / "auth").mkdir(parents=True)
    (target / "runtime_assets" / "teacher_records" / "existing").mkdir(parents=True)
    (target / ".env").write_text("SERVER_SECRET=keep\n", encoding="utf-8")
    database = target / "runtime_assets" / "auth" / "identity.sqlite3"
    database.write_bytes(b"server-database")
    existing_record = target / "runtime_assets" / "teacher_records" / "existing" / "record.json"
    existing_record.write_text("server-record", encoding="utf-8")

    archive = tmp_path / "code.zip"
    _write_zip(archive, {
        "pipeline/web/app.py": "new-code",
        ".env": "LOCAL_SECRET=overwrite",
        "runtime_assets/auth/identity.sqlite3": "local-database",
        "runtime_assets/teacher_records/new/record.json": "local-record",
    })

    result = deploy_archive(archive, target)

    assert (target / "pipeline" / "web" / "app.py").read_text() == "new-code"
    assert (target / ".env").read_text() == "SERVER_SECRET=keep\n"
    assert database.read_bytes() == b"server-database"
    assert existing_record.read_text() == "server-record"
    assert not (target / "runtime_assets" / "teacher_records" / "new").exists()
    assert result == {"updated": 1, "protected": 3, "planned": 1}


def test_code_folder_prefix_is_removed(tmp_path: Path) -> None:
    target = tmp_path / "chemistry-glasses"
    target.mkdir()
    archive = tmp_path / "code.zip"
    _write_zip(archive, {
        "code/pipeline/web/app.py": "new-code",
        "code/runtime_assets/auth/identity.sqlite3": "must-not-copy",
    })

    deploy_archive(archive, target)

    assert (target / "pipeline" / "web" / "app.py").read_text() == "new-code"
    assert not (target / "code").exists()
    assert not (target / "runtime_assets").exists()


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "chemistry-glasses"
    target.mkdir()
    archive = tmp_path / "code.zip"
    _write_zip(archive, {"../outside.txt": "bad"})

    with pytest.raises(ValueError, match="不安全路径"):
        deploy_archive(archive, target)
