import io

from pipeline.web import app as web


def test_student_record_retry_returns_same_record_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "_record_root", lambda: tmp_path)
    monkeypatch.setattr(web._media_analysis_queue, "enqueue", lambda *args: None)
    client = web.app.test_client()
    identity = "20261014_" + "a" * 32

    def upload(content, device="student-test"):
        return client.post("/api/glasses/records/upload", data={
            "audio": (io.BytesIO(content), "record.wav"),
            "device_id": device, "client_record_id": identity,
        })

    first = upload(b"first original")
    second = upload(b"retry contents")
    assert first.status_code == second.status_code == 201
    assert first.get_json()["record"]["record_id"] == second.get_json()["record"]["record_id"]
    assert len(list(tmp_path.rglob("record.json"))) == 1
    assert next(tmp_path.rglob("*.wav")).read_bytes() == b"first original"
    assert upload(b"another device", "other").status_code == 409


def test_student_photo_retry_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "_photo_root", lambda: tmp_path)
    monkeypatch.setattr(web._media_analysis_queue, "enqueue", lambda *args: None)
    client = web.app.test_client()
    def upload():
        return client.post("/api/glasses/photos/upload", data={
            "photo": (io.BytesIO(b"photo"), "photo.jpg"), "device_id": "student-test",
            "client_record_id": "20261014_" + "b" * 32,
        })
    assert upload().status_code == upload().status_code == 201
    assert len(list(tmp_path.rglob("photo.json"))) == 1
