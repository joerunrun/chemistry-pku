from pipeline.voice_control.student_intent import parse_student_intent, student_prompt
from pipeline.voice_control.teacher_intent import parse_teacher_intent


def test_no_transcript_keyword_fallback_or_unclear():
    for response in ("", "bad json", '{"intent":"unclear"}', '{"intent":"invented"}', '[]'):
        assert parse_student_intent(response, "小北停止录音", "student_idle", "AUDIO_RECORDING")["intent"] == "no_action"


def test_record_stop_requires_an_active_recording():
    response = '{"intent":"stop_recording"}'
    assert parse_student_intent(response, "停止录像", "student_idle", "IDLE")["intent"] == "no_action"
    assert parse_student_intent(response, "停止录像", "student_idle", "VIDEO_RECORDING")["intent"] == "stop_recording"


def test_question_is_preserved_and_selection_is_contextual():
    result = parse_student_intent('{"intent":"qa"}', "小北，为什么会沸腾？", "student_idle", "IDLE")
    assert result["question"] == "小北，为什么会沸腾？"
    assert parse_student_intent('{"intent":"select_experiment"}', "电解", "student_idle", "IDLE")["intent"] == "no_action"
    assert parse_student_intent('{"intent":"select_experiment"}', "电解", "student_setup", "IDLE")["intent"] == "no_action"


def test_prompt_distinguishes_recording_content_from_commands():
    prompt = student_prompt("student_idle", "AUDIO_RECORDING", "", "")
    assert "停止实验操作" in prompt and "不追问" in prompt and "no_action" in prompt


def test_teacher_direct_actions_respect_recording_state_and_stay_silent():
    assert parse_teacher_intent('{"intent":"capture_photo"}', "拍张照", "VIDEO_RECORDING")["intent"] == "capture_photo"
    assert parse_teacher_intent('{"intent":"start_video_recording"}', "帮我录下来", "IDLE")["intent"] == "start_recording"
    assert parse_teacher_intent('{"intent":"start_video_recording"}', "继续录", "VIDEO_RECORDING")["intent"] == "no_action"
    assert parse_teacher_intent('{"intent":"stop_recording"}', "保存录像", "IDLE")["intent"] == "no_action"
    assert parse_teacher_intent('{"intent":"stop_recording"}', "保存录像", "VIDEO_RECORDING")["intent"] == "stop_recording"
    for response in ('bad json', '{"intent":"unclear"}', '{"intent":"qa"}'):
        assert parse_teacher_intent(response, "小北拍照", "IDLE")["intent"] == "no_action"


def test_active_tasks_accept_only_their_stop_action():
    from pipeline.voice_control.student_intent import INTENTS
    for state, allowed in (("AUDIO_RECORDING", "stop_recording"), ("VIDEO_RECORDING", "stop_recording"),
                           ("FEEDBACK_RECORDING", "stop_feedback"), ("STARTING_VIDEO", "no_action"),
                           ("STOPPING", "no_action")):
        for intent in INTENTS:
            response = '{"intent":"' + intent + '"}'
            expected = allowed if intent == allowed else "no_action"
            if intent == "capture_photo" and state in {"AUDIO_RECORDING", "VIDEO_RECORDING"}:
                expected = "capture_photo"
            assert parse_student_intent(response, "测试指令", "student_idle", state)["intent"] == expected
            assert parse_teacher_intent(response, "测试指令", state)["intent"] == expected
    assert parse_teacher_intent('{"intent":"start_feedback"}', "我要反馈", "IDLE")["intent"] == "start_feedback"
    assert parse_student_intent('{"intent":"stop_feedback"}', "结束反馈", "student_idle", "IDLE")["intent"] == "no_action"
