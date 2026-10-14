from pipeline.voice_control.student_intent import parse_student_intent, student_prompt


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
    assert parse_student_intent('{"intent":"select_experiment"}', "电解", "student_setup", "IDLE")["intent"] == "select_experiment"


def test_prompt_distinguishes_recording_content_from_commands():
    prompt = student_prompt("student_idle", "AUDIO_RECORDING", "", "")
    assert "停止加热" in prompt and "不要追问" in prompt and "no_action" in prompt
