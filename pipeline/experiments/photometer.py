# 光度计实验配置（仅 1 个目标：光斑）
from __future__ import annotations

COMPONENTS: list[dict[str, str]] = [
    {"label": "light_spot", "name": "光斑", "color": "#ff0000"},
]

COMPONENT_LABELS = [c["label"] for c in COMPONENTS]

COLOR_BGR = {
    "light_spot": (0, 0, 255),  # 红色框
}

COLOR_LEGEND = """色框对应关系：
- 红色框: 光斑 (light_spot)"""

APPROACH_A_PROMPT = """请只判断图B的光斑最亮处是否与感光元件中心对准。

图A是参考示范图（正确），其中光斑中心对准感光元件。图B是待纠错的学生图片。两张图都用红色矩形框标注光斑位置。

{color_legend}

CV检测结果说明：
{cv_summary}

判断规则：
1. 唯一关注点是图B光斑最亮处是否偏离感光元件中心。
2. 图A用于提供正确对准状态：光斑中心在感光元件中心。
3. 不要评价光斑大小、清晰度、背景、拍摄角度或其他装置。
3. 如果图B未检测到红色框，结论为“无法判断，图B未检测到光斑”。
4. 如果图A未检测到红色框，结论为“无法判断，参考图未检测到光斑”。

请按以下格式输出：

结论：偏离 / 未偏离 / 无法判断

理由：
- 如果偏离，直接输出：光斑最亮处未与感光元件对准，导致信号弱。
- 如果未偏离，输出：光斑最亮处与感光元件对准。
- 如果无法判断，简要说明缺少哪一侧光斑框。"""

APPROACH_B_DIFF_PROMPT = """请只判断图B的光斑最亮处是否与感光元件中心对准。

图A是参考示范图（正确），其中光斑中心对准感光元件。图B是待纠错的学生图片。

{color_legend}

CV检测概况：
{cv_summary}

只看图B光斑最亮处是否偏离感光元件中心，不评价大小、清晰度、背景或其他装置。

请严格按照以下格式输出：
[light_spot] 偏离 / 未偏离 / 无法判断：如果偏离，理由必须是“光斑最亮处未与感光元件对准，导致信号弱。”"""

APPROACH_B_ANALYSIS_PROMPT = """现在你已经拿到了光斑对准判断，请给出最终结论。

已知：
1. 图A是参考示范图。
2. 图B是待纠错图片。

光斑对准判断：
{diff_text}

CV检测结构化数据：
{cv_structured_summary}

请按下面格式输出：
结论：偏离 / 未偏离 / 无法判断

要求：
1. 只输出中文。
2. 唯一关注点是图B光斑最亮处是否与感光元件中心对准。
3. 如果偏离，必须输出：光斑最亮处未与感光元件对准，导致信号弱。
4. 不要输出其他实验问题。"""

# 使用与蒸馏实验相同的参考图特征库流程：
# 先在 CV_detection2/annotate_app.py 中人工标注 light_spot，再检测学生图。
HAS_REFERENCE_DB = True
REQUIRE_REFERENCE_LABELS = True
