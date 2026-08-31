from __future__ import annotations

from pipeline.derived_analysis.common_issues import (
    _json_from_model,
    _normalize_issues,
    build_common_issues_prompt,
)


def test_normalize_requires_two_distinct_records_and_recomputes_count():
    result = _normalize_issues(
        {
            "common_issues": [
                {
                    "problem": "重复问题",
                    "guidance": "检查实验要求。",
                    "count": 99,
                    "record_ids": ["r1", "r1", "r2"],
                },
                {
                    "problem": "单条问题",
                    "guidance": "不要输出。",
                    "record_ids": ["r1"],
                },
            ],
        },
        {"r1", "r2"},
    )
    assert result == [{
        "problem": "重复问题",
        "count": 2,
        "guidance": "检查实验要求。",
        "record_ids": ["r1", "r2"],
    }]


def test_model_json_parser_accepts_fenced_json():
    assert _json_from_model("```json\n{\"common_issues\": []}\n```") == {"common_issues": []}


def test_prompt_contains_experiment_knowledge_and_record_ids():
    prompt = build_common_issues_prompt(
        {
            "id": "distillation",
            "name": "减压蒸馏",
            "experiment_requirements": "温度计液泡位于侧管口中心。",
            "components": [{"label": "thermometer", "name": "温度计"}],
        },
        [{"record_id": "r1", "created_at": "2026-08-01", "transcript": "温度计位置不对"}],
    )
    assert "温度计液泡位于侧管口中心" in prompt
    assert "记录ID=r1" in prompt
    assert "至少出现在两条不同记录" in prompt
