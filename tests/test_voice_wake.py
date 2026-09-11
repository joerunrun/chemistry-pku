from pipeline.voice_control.intent_omni import _parse_wake_json


def _wake(transcript: str) -> bool:
    return bool(_parse_wake_json("", transcript)["wake"])


def test_wake_accepts_exact_near_sound_and_activation_intent() -> None:
    assert _wake("小北助手")
    assert _wake("小北助手，开始工作")
    assert _wake("小贝助手")
    assert _wake("晓北助手")
    assert _wake("小北，醒醒")
    assert _wake("助手，来帮我")


def test_wake_rejects_unaddressed_or_unrelated_speech() -> None:
    assert not _wake("开始工作")
    assert not _wake("我们今天开始做实验")
    assert not _wake("外面有点吵")


def test_model_boolean_remains_authoritative() -> None:
    assert _parse_wake_json('{"wake": false, "transcript": "小北助手"}', "小北助手")["wake"] is False
    assert _parse_wake_json('{"wake": true, "transcript": "助手开始工作"}', "")["wake"] is True
