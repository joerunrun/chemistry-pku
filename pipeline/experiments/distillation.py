# 蒸馏实验配置
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COMPONENTS: list[dict[str, str]] = [
    {"label": "voltage_regulator", "name": "voltage_regulator", "color": "#fff200"},
    {"label": "heating_unit", "name": "heating_unit", "color": "#ff0000"},
    {"label": "distillation_core", "name": "distillation_core", "color": "#00b050"},
    {"label": "condenser_unit", "name": "condenser_unit", "color": "#00a2e8"},
    {"label": "receiving_assembly", "name": "receiving_assembly", "color": "#ff7f27"},
    {"label": "vacuum_gauge", "name": "vacuum_gauge", "color": "#a349a4"},
]

COMPONENT_LABELS = [c["label"] for c in COMPONENTS]

COLOR_BGR = {
    "voltage_regulator": (0, 242, 255),
    "heating_unit": (0, 0, 255),
    "distillation_core": (80, 176, 0),
    "condenser_unit": (232, 162, 0),
    "receiving_assembly": (39, 127, 255),
    "vacuum_gauge": (164, 73, 163),
}

COLOR_LEGEND = """色框对应关系：
- 黄色框: 调压器 (voltage_regulator)
- 红色框: 加热装置 (heating_unit)
- 绿色框: 蒸馏核心 (distillation_core)
- 蓝色框: 冷凝管 (condenser_unit)
- 橙色框: 接收装置 (receiving_assembly)
- 紫色框: 真空表 (vacuum_gauge)"""

APPROACH_A_PROMPT = """请对比图A和图B，判断图B中最主要的减压蒸馏装置错误。

图A是正确参考图，图B是待纠错学生图。两张图上都有彩色框标注装置部件。

{color_legend}

CV检测概况：
{cv_summary}

实验要求：
{requirements}

要求：
1. 只关注减压蒸馏装置是否符合实验要求。
2. 忽略拍摄角度、光线、裁剪范围等表面差异。
3. 如果图B的自动检测框有漏检或误检，以图像实际内容为准。
4. 输出要简洁，只给最主要问题和整改意见。

输出格式：
问题：...
意见：..."""

APPROACH_B_DIFF_PROMPT = """请对比以下两张化学实验装置图片，逐色框找出差异。

图A是参考示范图（正确），图B是待纠错的学生图片。

{color_legend}

CV检测概况：
{cv_summary}

请按每种颜色/装置分别描述：
1. 如果该颜色框在两张图中都存在，描述位置、大小、装置形态的差异。
2. 如果该颜色框只在一张图中存在，明确指出缺失或多余。
3. 错误图B的颜色框为自动检测，可能出现漏检误检

请严格按照以下格式输出，每种装置一段：
[voltage_regulator] ...
[heating_unit] ...
[distillation_core] ...
[condenser_unit] ...
[receiving_assembly] ...
[vacuum_gauge] ..."""

APPROACH_B_ANALYSIS_PROMPT = """现在你已经拿到了图片差异分析和CV检测的结构化数据，请结合实验要求给出最终判断。

已知：
1. 图A是参考示范图。
2. 图B是待纠错图片。

图片差异分析（按装置逐个）：
{diff_text}

CV检测结构化数据：
{cv_structured_summary}

实验要求：
{requirements}

请按下面格式输出：
主要错误：...

要求：
1. 只输出中文。
2. 重点回答"最主要的错误是什么"，不要把很多次要问题混在一起。
3. 若实验要求与图像差异存在冲突，以实验要求为准重新判断。
4. 如果某个装置CV未检测到但差异分析也未提及，说明该装置可能无问题，不要凭空猜测。"""

# 是否支持从数据库选取参考图
HAS_REFERENCE_DB = True
