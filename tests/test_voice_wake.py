from pipeline.voice_control.intent_omni import (
    OmniIntentCallback,
    _parse_teacher_wake_json,
    _parse_wake_json,
    _teacher_wake_from_transcript,
)


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


def test_teacher_wake_accepts_either_text_or_omni_positive() -> None:
    local_positive = _parse_teacher_wake_json(
        '{"wake": false, "intent": null, "transcript": "小贝助手"}',
        "小贝助手",
    )
    omni_positive = _parse_teacher_wake_json(
        '{"wake": true, "intent": null, "transcript": "请听我说"}',
        "请听我说",
    )
    assert local_positive["wake"] is True
    assert local_positive["decision_source"] == "text"
    assert omni_positive["wake"] is True
    assert omni_positive["decision_source"] == "omni"


def test_teacher_wake_extracts_one_shot_action() -> None:
    photo = _teacher_wake_from_transcript("小北助手，帮我拍张照片")
    recording = _teacher_wake_from_transcript("化小北，把这一段记录下来")
    assert photo["intent"] == "capture_photo"
    assert recording["intent"] == "start_recording"


def test_teacher_callback_does_not_finish_on_transcript_alone() -> None:
    callback = OmniIntentCallback(complete_on_transcript=False)
    callback.on_event({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "不精确的转写",
    })
    assert callback.done is False
    callback.on_event({
        "type": "response.text.done",
        "text": '{"wake": true, "intent": null, "transcript": "小北助手"}',
    })
    assert callback.done is True
