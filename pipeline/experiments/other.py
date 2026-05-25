from __future__ import annotations

COMPONENTS: list[dict[str, str]] = [
    {"label": "object_a", "name": "测试物体A", "color": "#ff3b30"},
    {"label": "object_b", "name": "测试物体B", "color": "#34c759"},
    {"label": "object_c", "name": "测试物体C", "color": "#007aff"},
    {"label": "object_d", "name": "测试物体D", "color": "#ffcc00"},
]

COMPONENT_LABELS = [c["label"] for c in COMPONENTS]

COLOR_BGR = {
    "object_a": (48, 59, 255),
    "object_b": (89, 199, 52),
    "object_c": (255, 122, 0),
    "object_d": (0, 204, 255),
}

COLOR_LEGEND = """测试框对应关系：
- 红色框: 测试物体A (object_a)
- 绿色框: 测试物体B (object_b)
- 蓝色框: 测试物体C (object_c)
- 黄色框: 测试物体D (object_d)
"""

APPROACH_A_PROMPT = """这是其它场景的检测框叠加测试。请只根据图像简短说明检测框是否大致覆盖了目标物体。
输出格式：
问题：...
意见：..."""

APPROACH_B_DIFF_PROMPT = APPROACH_A_PROMPT
APPROACH_B_ANALYSIS_PROMPT = APPROACH_A_PROMPT
HAS_REFERENCE_DB = True
