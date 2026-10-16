from __future__ import annotations

from pipeline.derived_analysis.daily_course import build_daily_course_prompt, generate_daily_course


def test_daily_course_prompt_keeps_metadata_and_requests_narrative():
    prompt = build_daily_course_prompt(
        "2026-08-03",
        [{
            "record_id": "r1",
            "created_at": "2026-08-03T09:00:00Z",
            "device_id": "teacher-1",
            "experiment_type": "distillation",
            "transcript": "先检查温度计位置，然后开始加热。",
        }],
        [{"id": "distillation", "name": "减压蒸馏"}],
    )
    assert "2026-08-03" in prompt
    assert "记录ID=r1" in prompt
    assert "设备=teacher-1" in prompt
    assert "不是逐条复制，也不是简单拼接" in prompt


def test_generate_daily_course_normalizes_model_result(monkeypatch):
    class FakeClient:
        def chat(self, messages, temperature=0.1):
            return '{"title":"课程回顾","course_content":"今天完成了实验并讨论了操作要点。","highlights":["检查仪器"]}'

    monkeypatch.setattr("pipeline.derived_analysis.daily_course.create_vlm", lambda provider, model: FakeClient())
    result = generate_daily_course(
        "2026-08-03",
        [{"record_id": "r1", "transcript": "完成实验"}],
        provider="test",
        model="test-model",
    )
    assert result["title"] == "课程回顾"
    assert result["course_content"] == "今天完成了实验并讨论了操作要点。"
    assert result["provider"] == "test"


def test_full_daily_account_keeps_late_content_in_long_recordings(monkeypatch):
    from pipeline.derived_analysis.daily_reports import generate_daily_reports
    import json
    prompts = []
    class Client:
        def chat(self, messages, **kwargs):
            prompt = messages[0]["content"]
            prompts.append(prompt)
            return json.dumps({"summary": "short", "detail": "END retained" if "END" in prompt else "first part"}) if "输出JSON" in prompt else "daily overview"
    monkeypatch.setattr("pipeline.derived_analysis.daily_reports.create_vlm", lambda *args: Client())
    result = generate_daily_reports("2026-10-16", [{"transcript": "x" * 6500 + "END"}])
    assert "END retained" in result["detailed_content"]
    assert "first part" in result["detailed_content"]
    assert result["course_content"] == "daily overview"

