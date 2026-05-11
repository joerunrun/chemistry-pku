"""实验配置注册中心。每个实验一个 .py 文件，通过 get_experiment_config(name) 获取。"""
from __future__ import annotations

from pipeline.experiments import distillation, photometer

_REGISTRY = {
    "distillation": distillation,
    "photometer": photometer,
}

def get_experiment_config(name: str):
    """返回实验配置模块。name 不存在时返回 None。"""
    return _REGISTRY.get(name)