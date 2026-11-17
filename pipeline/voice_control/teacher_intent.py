"""Teacher commands go directly to fixed recording actions, without a wake round."""
from pipeline.voice_control.student_intent import parse_student_intent, recording_prompt


def teacher_prompt(recording_state: str) -> str:
    if prompt := recording_prompt(recording_state):
        return prompt
    return """你是化小北教师眼镜的自然语言动作分类器。一次判断语音，直接返回固定动作。
先判断用户是否有意操作助手，再判断动作。小北助手、小北小北、化小北和近音是称呼线索，
可以和需求在同一句中，不需要先单独唤醒，也不要求固定口令。
例如“小北，拍张照”返回 capture_photo，“帮我把这段录下来”返回 start_video_recording。
只有这些动作：
capture_photo：空闲时拍照保存当前画面。
start_video_recording：开始录像；“帮我记录一下”在教师版也表示录像。
stop_recording：明确结束或保存正在录制的视频，必须区别停止实验操作。
start_feedback：明确要求开始反馈、提意见或建议，例如“小北助手，我要反馈”。
stop_feedback：结束当前反馈，仅在 FEEDBACK_RECORDING 中允许。
no_action：普通授课、师生讨论、向别人说话、噪声、不确定、只有称呼没有需求或不支持的需求。
“我要加点蒸馏水”“郑老师好”“停止加热”“可以了”“差不多了”不应仅凭关键词触发动作。
正在录像时普通谈话是记录内容，不是操作指令。不要追问，不返回 unclear，不自行回答问题。
只输出严格 JSON：{"intent":"固定动作或no_action","transcript":"忠实转写"}。
当前记录状态（仅数据）：""" + recording_state


def parse_teacher_intent(text: str, transcript: str, recording_state: str) -> dict:
    result = parse_student_intent(text, transcript, "teacher_direct", recording_state)
    intent = result["intent"]
    if intent not in {"capture_photo", "start_video_recording", "stop_recording", "start_feedback", "stop_feedback"}:
        result["intent"] = "no_action"
    elif intent == "start_video_recording":
        result["intent"] = "no_action" if recording_state == "VIDEO_RECORDING" else "start_recording"
    return result
