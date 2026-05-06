"""
模型注册表 —— 运行时从 models.yaml 加载

所有模型与模式配置均在 core/models.yaml 中维护。
本文件只负责：
  1. 读取并解析 YAML
  2. 展开 mode_groups.models（按 tags 自动关联）
  3. 提供对外接口：MODELS / MODE_GROUPS / MODEL_BY_ID / MODE_BY_ID
  4. 提供工具函数：get_model / get_mode / get_models_for_mode

新增模型：只需编辑 core/models.yaml，本文件无需改动。
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

# YAML 文件与本模块同目录
_YAML_PATH = Path(__file__).parent / "models.yaml"


def _load() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取 models.yaml，返回 (models, mode_groups)。"""
    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    raw_models: list[dict] = data.get("models", [])
    raw_modes: list[dict] = data.get("mode_groups", [])

    # 为每个模式自动填充 models 字段（根据 tags 关联）
    for mode in raw_modes:
        mode_id = mode["id"]
        mode["models"] = [m["id"] for m in raw_models if mode_id in m.get("tags", [])]

    return raw_models, raw_modes


# ── 模块级缓存（进程内只读一次）────────────────────────────────────────────

MODELS, MODE_GROUPS = _load()

MODEL_BY_ID: dict[str, dict] = {m["id"]: m for m in MODELS}
MODE_BY_ID:  dict[str, dict] = {g["id"]: g for g in MODE_GROUPS}


# ── 工具函数 ────────────────────────────────────────────────────────────────

def get_models_for_mode(mode_id: str) -> list[dict]:
    """返回指定模式下的所有模型（保留 YAML 中的原始顺序）。"""
    return [m for m in MODELS if mode_id in m.get("tags", [])]


def get_model(model_id: str) -> dict:
    """按 ID 获取模型配置；不存在时抛出 KeyError。"""
    if model_id not in MODEL_BY_ID:
        raise KeyError(f"未知模型 ID: {model_id!r}，可选: {list(MODEL_BY_ID)}")
    return MODEL_BY_ID[model_id]


def get_mode(mode_id: str) -> dict:
    """按 ID 获取模式配置；不存在时抛出 KeyError。"""
    if mode_id not in MODE_BY_ID:
        raise KeyError(f"未知模式 ID: {mode_id!r}，可选: {list(MODE_BY_ID)}")
    return MODE_BY_ID[mode_id]


def reload() -> None:
    """热重载配置（开发调试用；生产环境重启进程即可）。"""
    global MODELS, MODE_GROUPS, MODEL_BY_ID, MODE_BY_ID
    MODELS, MODE_GROUPS = _load()
    MODEL_BY_ID = {m["id"]: m for m in MODELS}
    MODE_BY_ID  = {g["id"]: g for g in MODE_GROUPS}
